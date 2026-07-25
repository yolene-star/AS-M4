#!/usr/bin/env python
"""冻结音频语义重构路线的只读基础证据。

本脚本不加载模型、不访问 GPU、不修改已有实验产物。它只核验已冻结的
semantic-contrast 训练清单、dev760 五条件预测和关键实现文件，并生成可提交的
基础锁 JSON，供后续诊断计划引用。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import torch


ROOT = Path(__file__).resolve().parents[2]
INTERSUIT = ROOT / "intersuit"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def counter(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(key) or "missing") for row in rows).items()))


def answer_kind(answer: Any) -> str:
    text = str(answer or "").strip()
    if re.fullmatch(r"[A-Da-d]", text):
        return "multiple_choice_letter"
    if not text:
        return "empty"
    if "," in text:
        return "comma_separated_label"
    if len(text.split()) == 1:
        return "single_token_or_word"
    return "free_text_or_phrase"


def manifest_audit(path: Path) -> dict[str, Any]:
    rows = read_json(path)
    physical_ids = [str(row.get("physical_media_id") or "") for row in rows]
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": sha256(path),
        "count": len(rows),
        "source_dataset_counts": counter(rows, "source_dataset"),
        "task_type_counts": counter(rows, "task_type"),
        "qa_origin_counts": counter(rows, "qa_origin"),
        "answer_kind_counts": dict(sorted(Counter(answer_kind(row.get("answer")) for row in rows).items())),
        "missing_event_label_count": sum(not row.get("event_label") for row in rows),
        "missing_physical_media_id_count": sum(not value for value in physical_ids),
        "unique_physical_media_count": len({value for value in physical_ids if value}),
    }


def training_pair_audit(path: Path) -> dict[str, Any]:
    result = manifest_audit(path)
    rows = read_json(path)
    result.update(
        {
            "missing_negative_event_label_count": sum(
                not row.get("scene_audio_negative_event_label") for row in rows
            ),
            "same_nonempty_event_label_count": sum(
                bool(row.get("event_label"))
                and row.get("event_label") == row.get("scene_audio_negative_event_label")
                for row in rows
            ),
            "visual_task_with_contrastive_target_count": sum(
                row.get("task_type") == "visual" for row in rows
            ),
            "multiple_choice_letter_target_count": sum(
                answer_kind(row.get("answer")) == "multiple_choice_letter" for row in rows
            ),
        }
    )
    return result


def feature_manifest_audit(path: Path) -> dict[str, Any]:
    rows = read_jsonl(path)
    missing_paths = []
    window_seconds = Counter()
    hop_seconds = Counter()
    checkpoint_hashes = Counter()
    nonfinite_count = 0
    for row in rows:
        for key in ("audio_feature_path", "video_feature_path", "source_video_path"):
            value = Path(str(row[key]))
            if not value.is_file():
                missing_paths.append(str(value))
        audio_path = Path(str(row["audio_feature_path"]))
        if not audio_path.is_file():
            continue
        payload = torch.load(audio_path, map_location="cpu", weights_only=True)
        metadata = payload.get("metadata") or {}
        window_seconds[str(metadata.get("window_sec"))] += 1
        hop_seconds[str(metadata.get("hop_sec"))] += 1
        checkpoint_hashes[str(metadata.get("checkpoint_sha256"))] += 1
        embeddings = payload.get("audio_embedding")
        if not isinstance(embeddings, torch.Tensor) or not torch.isfinite(embeddings).all():
            nonfinite_count += 1
    compatible = (
        not missing_paths
        and not nonfinite_count
        and window_seconds == {"1.0": len(rows)}
        and hop_seconds == {"1.0": len(rows)}
    )
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": sha256(path),
        "count": len(rows),
        "missing_path_count": len(missing_paths),
        "nonfinite_audio_feature_count": nonfinite_count,
        "window_sec_counts": dict(sorted(window_seconds.items())),
        "hop_sec_counts": dict(sorted(hop_seconds.items())),
        "checkpoint_sha256_counts": dict(sorted(checkpoint_hashes.items())),
        "formal_r1_contract_compatible": compatible,
        "qualification_zh": (
            "仅可用于 evaluator 开发；正式 R1 需要按 1.0s window / 1.0s hop 重新提取。"
            if not compatible
            else "可用于正式 R1。"
        ),
    }


def index(rows: list[dict[str, Any]], condition: str) -> dict[str, dict[str, Any]]:
    indexed = {str(row["id"]): row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError(f"{condition} 存在重复 id")
    return indexed


def paired_strata(
    prediction_paths: dict[str, Path],
    manifest_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = {condition: read_jsonl(path) for condition, path in prediction_paths.items()}
    indexed = {condition: index(value, condition) for condition, value in rows.items()}
    expected = set(indexed["video_only"])
    for condition, value in indexed.items():
        if set(value) != expected:
            raise ValueError(f"{condition} 的预测 id 集不一致")

    def paired(candidate: str, baseline: str = "video_only") -> dict[str, int]:
        gain = harm = same = 0
        for sample_id in expected:
            candidate_row = indexed[candidate][sample_id]
            baseline_row = indexed[baseline][sample_id]
            gain += bool(candidate_row.get("correct")) and not bool(baseline_row.get("correct"))
            harm += bool(baseline_row.get("correct")) and not bool(candidate_row.get("correct"))
            same += str(candidate_row.get("prediction") or "") == str(baseline_row.get("prediction") or "")
        return {"gain": gain, "harm": harm, "net": gain - harm, "same_prediction_count": same}

    manifest_by_sample = {str(row.get("sample_id") or row.get("id")): row for row in manifest_rows}
    strata: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(Counter))
    for sample_id in sorted(expected):
        correct = indexed["correct"][sample_id]
        mismatch = indexed["mismatched"][sample_id]
        video = indexed["video_only"][sample_id]
        manifest_row = manifest_by_sample.get(str(correct.get("sample_id") or ""), {})
        keys = {
            "source_dataset": str(
                correct.get("source_dataset") or manifest_row.get("source_dataset") or "missing"
            ),
            "task_type": str(correct.get("task_type") or manifest_row.get("task_type") or "missing"),
            "answer_kind": answer_kind(correct.get("answer")),
        }
        flags = {
            "count": 1,
            "video_correct": int(bool(video.get("correct"))),
            "correct_audio_correct": int(bool(correct.get("correct"))),
            "mismatched_audio_correct": int(bool(mismatch.get("correct"))),
            "correct_only_vs_mismatch": int(bool(correct.get("correct")) and not bool(mismatch.get("correct"))),
            "mismatch_only_vs_correct": int(bool(mismatch.get("correct")) and not bool(correct.get("correct"))),
        }
        for family, value in keys.items():
            strata[family][value].update(flags)

    ratios = [
        float(row["delta_to_video_ratio"])
        for row in rows["correct"]
        if row.get("delta_to_video_ratio") is not None
    ]
    mismatch_ratios = [
        float(row["delta_to_video_ratio"])
        for row in rows["mismatched"]
        if row.get("delta_to_video_ratio") is not None
    ]
    return {
        "count": len(expected),
        "prediction_sha256": {
            condition: sha256(path) for condition, path in sorted(prediction_paths.items())
        },
        "paired_vs_video_only": {
            condition: paired(condition) for condition in ("correct", "mismatched", "silence", "gate0")
        },
        "correct_only_vs_mismatch": sum(
            bool(indexed["correct"][sample_id].get("correct"))
            and not bool(indexed["mismatched"][sample_id].get("correct"))
            for sample_id in expected
        ),
        "mismatch_only_vs_correct": sum(
            bool(indexed["mismatched"][sample_id].get("correct"))
            and not bool(indexed["correct"][sample_id].get("correct"))
            for sample_id in expected
        ),
        "mean_correct_delta_to_video_ratio": mean(ratios),
        "mean_mismatched_delta_to_video_ratio": mean(mismatch_ratios),
        "strata": {
            family: {value: dict(counts) for value, counts in sorted(values.items())}
            for family, values in sorted(strata.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(INTERSUIT / "configs/audio_semantic_reconstruction_basis_lock.json"),
    )
    args = parser.parse_args()

    train = INTERSUIT / "datasets/AS_M4_STAGE2/train_manifest_semantic_negative.json"
    dev = INTERSUIT / "datasets/AS_M4_STAGE2/dev_manifest.json"
    feature_manifest = (
        INTERSUIT
        / "harness/artifacts/ave_hf_300_clip_window_features/ave_hf_clip_window_feature_manifest.jsonl"
    )
    artifact_root = INTERSUIT / "harness/artifacts/as_m4_semantic_contrast_dev760_gate0005"
    prediction_root = artifact_root / "combined_predictions"
    prediction_paths = {
        condition: prediction_root / f"dev760_{condition}_predictions.jsonl"
        for condition in ("video_only", "correct", "mismatched", "silence", "gate0")
    }
    code_paths = [
        INTERSUIT / "intersuit/model/language_model/llava_qwen.py",
        INTERSUIT / "intersuit/model/llava_arch.py",
        INTERSUIT / "intersuit/model/streaming_av/fusion.py",
        INTERSUIT / "scripts/build_stage2_semantic_negative_manifest.py",
        INTERSUIT / "intersuit/harness/runners/run_predictions_from_plan.py",
    ]

    train_audit = training_pair_audit(train)
    dev_audit = manifest_audit(dev)
    train_ids = {
        str(row.get("physical_media_id")) for row in read_json(train) if row.get("physical_media_id")
    }
    dev_ids = {
        str(row.get("physical_media_id")) for row in read_json(dev) if row.get("physical_media_id")
    }
    result = {
        "format_version": 1,
        "frozen_at": "2026-07-25",
        "route": "audio_semantic_reconstruction_diagnostic",
        "selector_route_status": "stopped_checkpoint_ineligible",
        "training_allowed": False,
        "reason_zh": (
            "现有 semantic-contrast checkpoint 的 correct 音频低于 mismatched 与 video-only；"
            "下一步必须先证明冻结音频表示、语义目标和融合接口分别具备可分性与因果效用。"
        ),
        "training_manifest_audit": train_audit,
        "dev_manifest_audit": dev_audit,
        "existing_ave_hf_300_feature_audit": feature_manifest_audit(feature_manifest),
        "train_dev_physical_overlap_count": len(train_ids & dev_ids),
        "dev760_failure_audit": paired_strata(prediction_paths, read_json(dev)),
        "frozen_evidence": {
            "selectivity_summary": {
                "path": str((artifact_root / "selectivity_summary.json").relative_to(ROOT)),
                "sha256": sha256(artifact_root / "selectivity_summary.json"),
            },
            "checkpoint_lock": {
                "path": "intersuit/configs/dynamic_selector_v2_checkpoint_lock.json",
                "sha256": sha256(INTERSUIT / "configs/dynamic_selector_v2_checkpoint_lock.json"),
            },
        },
        "implementation_sha256": {
            str(path.relative_to(ROOT)): sha256(path) for path in code_paths
        },
        "review_findings": [
            "当前答案目标是忽略 prompt 后对全部答案 token embedding 做无上下文平均。",
            "A-D 多选字母与纯视觉答案不能作为可靠的音频事件语义目标。",
            "缺失 event_label 的样本无法保证负音频与正音频语义不同。",
            "semantic ranking 直接约束 scene-audio projector，但不直接约束 fusion。",
            "beats_simple_residual 将相同帧级音频残差广播到该帧全部空间视觉 token。",
            "dev760 中 correct 与 mismatched 的残差幅度近似相同，当前链路没有语义选择性。",
            "AVE_HF 本地 manifest 的 start_seconds 是源视频片段偏移，不是可靠事件边界；既有 projector 将同一视频全部窗口复制为同一弱标签。",
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "output": str(output), "sha256": sha256(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
