#!/usr/bin/env python
"""冻结音频语义重构 R0 的标签、物理划分和媒体契约。

本脚本只读取已经通过本地媒体校验的 AVE_HF manifest、Hugging Face parquet
schema 元数据和 AVE 官方 Annotations.txt。它不提取特征、不加载模型、不访问
GPU，也不读取或计算 AVE 官方 test 指标。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_EXPANDED = INTERSUIT_ROOT / "datasets/AVE_HF_EXPANDED/ave_hf_pilot_valid.jsonl"
DEFAULT_PILOT = INTERSUIT_ROOT / "datasets/AVE_HF_300/ave_hf_pilot_valid.jsonl"
DEFAULT_PARQUET_ROOT = INTERSUIT_ROOT / "datasets/AVE_HF_EXPANDED/parquet_cache/data"
DEFAULT_ANNOTATIONS = (
    INTERSUIT_ROOT / "datasets/AVE_OFFICIAL_D1_SOURCE/extracted/AVE_Dataset/Annotations.txt"
)
DEFAULT_BEATS = INTERSUIT_ROOT / "checkpoints/BEATs_iter3_plus_AS2M.pt"
DEFAULT_OUTPUT = (
    INTERSUIT_ROOT / "harness/artifacts/audio_semantic_reconstruction_r0_contract"
)
DEFAULT_SEED = 20260727


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_key(seed: int, collection: str, label_id: int, physical_id: str) -> str:
    value = f"{seed}:{collection}:{label_id}:{physical_id}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"manifest 为空：{path}")
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def load_hf_label_names(parquet_root: Path) -> tuple[list[str], dict[str, str]]:
    parquet_paths = sorted(parquet_root.glob("train-*.parquet"))
    if not parquet_paths:
        raise FileNotFoundError(f"未找到 parquet 分片：{parquet_root}")
    reference: list[str] | None = None
    hashes: dict[str, str] = {}
    for path in parquet_paths:
        metadata = pq.read_metadata(path).metadata or {}
        if b"huggingface" not in metadata:
            raise ValueError(f"parquet 缺少 Hugging Face schema 元数据：{path}")
        payload = json.loads(metadata[b"huggingface"].decode("utf-8"))
        names = list(payload["info"]["features"]["label"]["names"])
        if reference is None:
            reference = names
        elif names != reference:
            raise ValueError(f"parquet 分片的 ClassLabel mapping 不一致：{path}")
        hashes[str(path.relative_to(REPO_ROOT))] = sha256(path)
    assert reference is not None
    if len(reference) != 28 or len(set(reference)) != 28:
        raise ValueError(f"ClassLabel 必须恰好包含 28 个唯一类别，实际 {len(reference)}")
    return reference, hashes


def load_official_annotations(path: Path) -> dict[str, set[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "Category&VideoID&Quality&StartTime&EndTime":
        raise ValueError(f"AVE 官方 annotations 表头异常：{path}")
    mapping: dict[str, set[str]] = defaultdict(set)
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("&")
        if len(parts) != 5:
            raise ValueError(f"AVE 官方 annotation 行字段数异常：{line!r}")
        category, youtube_id = parts[0], parts[1]
        mapping[youtube_id].add(category)
    return dict(mapping)


def validate_source_rows(
    rows: list[dict[str, Any]],
    collection: str,
    label_names: list[str],
    official: dict[str, set[str]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for row in rows:
        youtube_id = str(row.get("youtube_id") or "")
        if not youtube_id or youtube_id in seen:
            raise ValueError(f"{collection} youtube_id 缺失或重复：{youtube_id!r}")
        seen.add(youtube_id)
        if row.get("valid") is not True:
            raise ValueError(f"{collection}:{youtube_id} 未通过上游媒体审计")
        label_id = int(row["label"])
        if label_id < 0 or label_id >= len(label_names):
            raise ValueError(f"{collection}:{youtube_id} label_id 越界：{label_id}")
        canonical_label = label_names[label_id]
        official_labels = official.get(youtube_id)
        if not official_labels:
            raise ValueError(f"{collection}:{youtube_id} 不在 AVE 官方 annotations")
        if canonical_label not in official_labels:
            raise ValueError(
                f"{collection}:{youtube_id} HF={canonical_label!r} 与官方={sorted(official_labels)!r} 不一致"
            )
        audio_path = Path(str(row.get("audio_path") or ""))
        video_path = Path(str(row.get("video_path") or ""))
        if not audio_path.is_file() or audio_path.stat().st_size <= 44:
            raise ValueError(f"{collection}:{youtube_id} 音频文件缺失或为空：{audio_path}")
        if not video_path.is_file() or video_path.stat().st_size <= 0:
            raise ValueError(f"{collection}:{youtube_id} 视频文件缺失或为空：{video_path}")
        if not row.get("has_video") or not row.get("has_audio") or not row.get("audio_non_empty"):
            raise ValueError(f"{collection}:{youtube_id} 上游媒体资格字段不完整")
        normalized.append(
            {
                "collection": collection,
                "physical_media_id": f"AVE:{youtube_id}",
                "youtube_id": youtube_id,
                "source_label": str(row["label"]),
                "label": str(row["label"]),
                "label_id": label_id,
                "canonical_label": canonical_label,
                "synonyms": [canonical_label],
                "supervision_level": "clip",
                "audio_required": True,
                "answer_letter": None,
                "answer_choice_text": None,
                "source_split": str(row.get("split") or ""),
                "start_seconds": row.get("start_seconds"),
                "audio_path": str(audio_path.resolve()),
                "video_path": str(video_path.resolve()),
                "media_qualification": "inherited_valid_full_decode",
            }
        )
    return normalized


def split_expanded(
    rows: list[dict[str, Any]],
    seed: int,
) -> list[dict[str, Any]]:
    by_label: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_label[int(row["label_id"])].append(row)
    output: list[dict[str, Any]] = []
    for label_id in sorted(by_label):
        bucket = sorted(
            by_label[label_id],
            key=lambda row: stable_key(
                seed, "expanded", label_id, str(row["physical_media_id"])
            ),
        )
        count = len(bucket)
        validation_count = count // 5
        test_count = count // 5
        train_count = count - validation_count - test_count
        for index, row in enumerate(bucket):
            if index < train_count:
                split = "train"
            elif index < train_count + validation_count:
                split = "validation"
            else:
                split = "test_locked"
            output.append({**row, "diagnostic_split": split})
    return sorted(output, key=lambda row: str(row["physical_media_id"]))


def split_pilot(rows: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    by_label: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_label[int(row["label_id"])].append(row)
    output: list[dict[str, Any]] = []
    for label_id in sorted(by_label):
        bucket = sorted(
            by_label[label_id],
            key=lambda row: stable_key(
                seed, "pilot300", label_id, str(row["physical_media_id"])
            ),
        )
        query_count = max(1, len(bucket) // 3) if len(bucket) >= 2 else 0
        reference_count = len(bucket) - query_count
        for index, row in enumerate(bucket):
            split = "reference" if index < reference_count else "query"
            output.append({**row, "diagnostic_split": split})
    return sorted(output, key=lambda row: str(row["physical_media_id"]))


def split_label_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        counts[str(row["diagnostic_split"])][str(row["canonical_label"])] += 1
    return {
        split: dict(sorted(values.items()))
        for split, values in sorted(counts.items())
    }


def assert_zero_overlap(rows: list[dict[str, Any]], splits: list[str]) -> None:
    ids = {
        split: {
            str(row["physical_media_id"])
            for row in rows
            if row["diagnostic_split"] == split
        }
        for split in splits
    }
    for index, left in enumerate(splits):
        for right in splits[index + 1 :]:
            overlap = ids[left] & ids[right]
            if overlap:
                raise ValueError(f"split 物理媒体重叠：{left}/{right}={len(overlap)}")


def build_review(metrics: dict[str, Any], decision: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# 音频语义重构 R0 数据契约审查",
            "",
            f"- 结论：`{decision['status']}`",
            f"- 扩展集：{metrics['expanded_count']} 条，28 类；train/validation/test_locked 物理零重叠。",
            f"- 正式 pilot：{metrics['pilot_count']} 条，与扩展集物理重叠为 0。",
            f"- HF ClassLabel 与 AVE 官方 annotations 逐条不一致数：{metrics['official_label_mismatch_count']}。",
            f"- 媒体路径与上游资格字段失败数：{metrics['media_qualification_failure_count']}。",
            "- 监督粒度固定为 `clip`；不得把 1 秒窗口复制为官方 window label。",
            "- `test_locked` 只完成冻结，R0/R1 不读取其指标。",
            "- 本阶段未提取特征、未加载模型、未访问 GPU、未启动训练。",
            "",
            "## 下一步",
            decision["next_step_zh"],
            "",
        ]
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    expanded_path = Path(args.expanded_manifest).resolve()
    pilot_path = Path(args.pilot_manifest).resolve()
    parquet_root = Path(args.parquet_root).resolve()
    annotations_path = Path(args.annotations).resolve()
    beats_path = Path(args.beats_checkpoint).resolve()
    output_root = Path(args.output_root).resolve()

    label_names, parquet_hashes = load_hf_label_names(parquet_root)
    official = load_official_annotations(annotations_path)
    expanded_source = read_jsonl(expanded_path)
    pilot_source = read_jsonl(pilot_path)
    expanded = validate_source_rows(
        expanded_source, "AVE_HF_EXPANDED", label_names, official
    )
    pilot = validate_source_rows(pilot_source, "AVE_HF_300", label_names, official)
    expanded_ids = {row["physical_media_id"] for row in expanded}
    pilot_ids = {row["physical_media_id"] for row in pilot}
    collection_overlap = expanded_ids & pilot_ids
    if collection_overlap:
        raise ValueError(f"pilot 与 expanded 物理媒体重叠：{len(collection_overlap)}")

    expanded_split = split_expanded(expanded, args.seed)
    pilot_split = split_pilot(pilot, args.seed)
    assert_zero_overlap(expanded_split, ["train", "validation", "test_locked"])
    assert_zero_overlap(pilot_split, ["reference", "query"])

    label_mapping = [
        {
            "source_label": str(index),
            "label_id": index,
            "canonical_label": name,
            "synonyms": [name],
            "mapping_source": "Hugging Face parquet ClassLabel metadata",
            "official_annotation_verified": True,
        }
        for index, name in enumerate(label_names)
    ]
    output_root.mkdir(parents=True, exist_ok=True)
    label_mapping_path = output_root / "label_mapping.json"
    expanded_path_out = output_root / "expanded_split_manifest.jsonl"
    expanded_train_validation_path = (
        output_root / "expanded_train_validation_manifest.jsonl"
    )
    pilot_path_out = output_root / "pilot300_split_manifest.jsonl"
    per_sample_path = output_root / "per_sample.jsonl"
    write_json(label_mapping_path, label_mapping)
    write_jsonl(expanded_path_out, expanded_split)
    write_jsonl(
        expanded_train_validation_path,
        [
            row
            for row in expanded_split
            if row["diagnostic_split"] in {"train", "validation"}
        ],
    )
    write_jsonl(pilot_path_out, pilot_split)
    write_jsonl(per_sample_path, expanded_split + pilot_split)

    expanded_split_counts = Counter(
        str(row["diagnostic_split"]) for row in expanded_split
    )
    pilot_split_counts = Counter(str(row["diagnostic_split"]) for row in pilot_split)
    qualified_pilot_labels = sorted(
        label
        for label, count in Counter(
            str(row["canonical_label"]) for row in pilot_split
        ).items()
        if count >= 3
    )
    metrics = {
        "expanded_count": len(expanded_split),
        "pilot_count": len(pilot_split),
        "label_count": len(label_names),
        "expanded_split_counts": dict(sorted(expanded_split_counts.items())),
        "pilot_split_counts": dict(sorted(pilot_split_counts.items())),
        "expanded_per_split_label_counts": split_label_counts(expanded_split),
        "pilot_per_split_label_counts": split_label_counts(pilot_split),
        "pilot_qualified_label_min_count": 3,
        "pilot_qualified_labels": qualified_pilot_labels,
        "pilot_qualified_label_count": len(qualified_pilot_labels),
        "collection_physical_overlap_count": len(collection_overlap),
        "split_physical_overlap_count": 0,
        "official_label_mismatch_count": 0,
        "missing_field_count": 0,
        "media_qualification_failure_count": 0,
        "formal_test_metric_accessed": False,
        "supervision_level_counts": {"clip": len(expanded_split) + len(pilot_split)},
        "audio_required_counts": {"true": len(expanded_split) + len(pilot_split)},
    }
    metrics_path = output_root / "metrics.json"
    write_json(metrics_path, metrics)

    existing_config_path = output_root / "config.json"
    existing_created_at = None
    if existing_config_path.is_file():
        existing_created_at = json.loads(
            existing_config_path.read_text(encoding="utf-8")
        ).get("created_at")
    config = {
        "created_at": existing_created_at
        or datetime.now(timezone.utc).isoformat(),
        "stage": "R0",
        "seed": args.seed,
        "expanded_split_rule": "per-label stable SHA256 order; 60/20/20 with integer remainder assigned to train",
        "pilot_split_rule": "per-label stable SHA256 order; about 2/3 reference and 1/3 query; singleton reference-only",
        "window_contract": {
            "sample_rate": 16000,
            "window_sec": 1.0,
            "hop_sec": 1.0,
            "tail": "right zero padding",
            "valid_sample_count_required": True,
        },
        "pooling_contract": {
            "primary": "clip_mean",
            "secondary": "RMS Top-M mean; M must be selected on train/reference only before validation",
        },
        "formal_test_metric_accessed": False,
        "training_authorized": False,
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "git_head": git_head(),
        "implementation_sha256": sha256(Path(__file__).resolve()),
    }
    config_path = output_root / "config.json"
    write_json(config_path, config)

    manifest_lock = {
        "inputs": {
            "expanded_manifest": {
                "path": str(expanded_path),
                "sha256": sha256(expanded_path),
            },
            "pilot_manifest": {"path": str(pilot_path), "sha256": sha256(pilot_path)},
            "official_annotations": {
                "path": str(annotations_path),
                "sha256": sha256(annotations_path),
            },
            "parquet_shards": parquet_hashes,
            "beats_checkpoint": {
                "path": str(beats_path),
                "sha256": sha256(beats_path),
            },
            "basis_lock": {
                "path": str(
                    INTERSUIT_ROOT
                    / "configs/audio_semantic_reconstruction_basis_lock.json"
                ),
                "sha256": sha256(
                    INTERSUIT_ROOT
                    / "configs/audio_semantic_reconstruction_basis_lock.json"
                ),
            },
        },
        "outputs": {
            "config.json": sha256(config_path),
            "label_mapping.json": sha256(label_mapping_path),
            "expanded_split_manifest.jsonl": sha256(expanded_path_out),
            "expanded_train_validation_manifest.jsonl": sha256(
                expanded_train_validation_path
            ),
            "pilot300_split_manifest.jsonl": sha256(pilot_path_out),
            "metrics.json": sha256(metrics_path),
            "per_sample.jsonl": sha256(per_sample_path),
        },
    }
    manifest_lock_path = output_root / "manifest_lock.json"
    write_json(manifest_lock_path, manifest_lock)

    decision = {
        "stage": "R0",
        "status": "PASS",
        "r1_pilot_authorized": True,
        "projector_training_authorized": False,
        "fusion_changes_authorized": False,
        "selector_training_authorized": False,
        "formal_test_authorized": False,
        "next_step": "extract_pilot300_beats_1s_1s",
        "next_step_zh": (
            "按锁定 BEATs checkpoint 为同一 300 条 pilot 重提取 1.0/1.0 "
            "音频特征；先过特征 manifest 和 evaluator golden，再运行不训练的 R1 pilot。"
        ),
    }
    decision_path = output_root / "decision.json"
    write_json(decision_path, decision)
    review_path = output_root / "review.md"
    review_path.write_text(build_review(metrics, decision), encoding="utf-8")

    manifest_lock["outputs"].update(
        {
            "decision.json": sha256(decision_path),
            "review.md": sha256(review_path),
        }
    )
    write_json(manifest_lock_path, manifest_lock)
    return {
        "status": decision["status"],
        "output_root": str(output_root),
        "metrics": metrics,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expanded-manifest", default=str(DEFAULT_EXPANDED))
    parser.add_argument("--pilot-manifest", default=str(DEFAULT_PILOT))
    parser.add_argument("--parquet-root", default=str(DEFAULT_PARQUET_ROOT))
    parser.add_argument("--annotations", default=str(DEFAULT_ANNOTATIONS))
    parser.add_argument("--beats-checkpoint", default=str(DEFAULT_BEATS))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser


def main() -> None:
    result = run(build_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
