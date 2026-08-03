#!/usr/bin/env python
"""使用全部 524 条合法 train 重新执行音频语义教师 T0v2。"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch
from transformers import ClapModel, ClapProcessor


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
V1_ROOT = INTERSUIT_ROOT / "harness/artifacts/audio_semantic_teacher_t0"
ARTIFACT_ROOT = INTERSUIT_ROOT / "harness/artifacts/audio_semantic_teacher_t0_v2"
VIEW_ROOT = INTERSUIT_ROOT / "datasets/AUDIO_SEMANTIC_TEACHER_T0_V2"
VIEW_MANIFEST = VIEW_ROOT / "teacher_view_full_train_confirm_manifest.jsonl"
VIEW_LOCK = VIEW_ROOT / "teacher_view_full_train_confirm_lock.json"
DEVELOPMENT_MANIFEST = (
    INTERSUIT_ROOT
    / "datasets/AVE_HF_EXPANDED/teacher_development_full_train_manifest.jsonl"
)
CONFIRM_MANIFEST = (
    INTERSUIT_ROOT / "datasets/AVE_HF_EXPANDED/teacher_confirm_manifest.jsonl"
)
SPLIT_LOCK = (
    INTERSUIT_ROOT / "datasets/AVE_HF_EXPANDED/teacher_confirm_split_lock.json"
)
OLD_R2_LOCK = (
    INTERSUIT_ROOT
    / "harness/artifacts/audio_semantic_reconstruction_r2_targets"
    / "old_r2_baseline_lock.json"
)
ASSET_LOCK = V1_ROOT / "teacher_asset_lock.json"
V1_LEDGER = V1_ROOT / "candidate_selection_ledger.json"
LEDGER = ARTIFACT_ROOT / "candidate_selection_ledger.json"
OLD_HELPER = INTERSUIT_ROOT / "scripts/qualify_audio_semantic_teacher_t0.py"

SPEC = importlib.util.spec_from_file_location("teacher_t0_v1_helpers", OLD_HELPER)
HELPER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HELPER
assert SPEC.loader is not None
SPEC.loader.exec_module(HELPER)

SEED = 20260727
EXPECTED_DEVELOPMENT = 524
EXPECTED_CONFIRM = 84
EXPECTED_LABELS = 28


def subset_indices(
    rows: list[dict[str, Any]], role: str
) -> list[int]:
    return [
        index
        for index, row in enumerate(rows)
        if row["teacher_view_role"] == role
    ]


def run() -> dict[str, Any]:
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("必须通过 CUDA_VISIBLE_DEVICES 显式暴露一张 GPU")
    required = (
        VIEW_MANIFEST,
        VIEW_LOCK,
        DEVELOPMENT_MANIFEST,
        CONFIRM_MANIFEST,
        SPLIT_LOCK,
        OLD_R2_LOCK,
        ASSET_LOCK,
        V1_LEDGER,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"缺少 T0v2 前置资产：{missing}")

    rows = HELPER.read_jsonl(VIEW_MANIFEST)
    if len(rows) != EXPECTED_DEVELOPMENT + EXPECTED_CONFIRM:
        raise ValueError(f"T0v2 教师视图数量异常：{len(rows)}")
    if any(
        row["validation_accessed"] or row["test_locked_accessed"]
        for row in rows
    ):
        raise ValueError("T0v2 教师视图污染")
    development_indices = subset_indices(
        rows, "teacher_development_full_train"
    )
    confirm_indices = subset_indices(rows, "teacher_confirm")
    if (
        len(development_indices) != EXPECTED_DEVELOPMENT
        or len(confirm_indices) != EXPECTED_CONFIRM
    ):
        raise ValueError(
            "T0v2 数据角色数量异常："
            f"development={len(development_indices)}, "
            f"confirm={len(confirm_indices)}"
        )
    development_ids = {
        rows[index]["physical_media_id"] for index in development_indices
    }
    expected_development_rows = HELPER.read_jsonl(DEVELOPMENT_MANIFEST)
    expected_development_ids = {
        row["physical_media_id"] for row in expected_development_rows
    }
    if development_ids != expected_development_ids:
        raise ValueError("T0v2 视图没有使用全部 524 条 teacher-development train")
    development_labels = {
        rows[index]["canonical_label"] for index in development_indices
    }
    if len(development_labels) != EXPECTED_LABELS:
        raise ValueError(
            f"teacher-development 必须覆盖 28 类：{len(development_labels)}"
        )
    labels = sorted({row["canonical_label"] for row in rows})
    if len(labels) != EXPECTED_LABELS:
        raise ValueError("T0v2 canonical label 必须为 28 类")

    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    device = torch.device("cuda:0")
    waveforms = [
        HELPER.load_waveform(row["teacher_audio_path"]) for row in rows
    ]
    snapshot = HELPER.SNAPSHOT
    processor = ClapProcessor.from_pretrained(snapshot, local_files_only=True)
    model = (
        ClapModel.from_pretrained(
            snapshot, local_files_only=True, trust_remote_code=False
        )
        .eval()
        .requires_grad_(False)
        .to(device)
    )
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise ValueError("教师没有完全冻结")
    prototypes = HELPER.text_prototypes(
        model, processor, labels, device
    )

    metrics: dict[str, Any] = {}
    per_sample_output = []
    candidates = []
    for padding in HELPER.PADDINGS:
        audio = HELPER.audio_embeddings(
            model, processor, rows, waveforms, padding, device
        )
        for prompt_name, text_vectors in prototypes.items():
            name = f"{padding}__{prompt_name}"
            metrics[name] = {}
            for role, indices in (
                ("teacher_development_full_train", development_indices),
                ("teacher_confirm", confirm_indices),
            ):
                role_rows = [rows[index] for index in indices]
                values, samples = HELPER.evaluate(
                    role_rows, audio[indices], text_vectors, labels
                )
                if (
                    values["label_count"] != EXPECTED_LABELS
                    or min(values["per_class_count"].values()) <= 0
                ):
                    raise ValueError(
                        f"{role} 类别覆盖不完整：{values['per_class_count']}"
                    )
                metrics[name][role] = values
                per_sample_output.extend(
                    {
                        "config": name,
                        "evaluation_role": role,
                        **sample,
                    }
                    for sample in samples
                )
            selection = metrics[name]["teacher_development_full_train"]
            candidates.append(
                (
                    (
                        selection["macro_recall_at_1"],
                        selection["rank_first"],
                        selection["margin_mean"],
                        int(padding == "pad"),
                        HELPER.PROMPT_TIE_PRIORITY[prompt_name],
                    ),
                    padding,
                    prompt_name,
                )
            )
    _, chosen_padding, chosen_prompt = max(
        candidates, key=lambda item: item[0]
    )
    chosen_name = f"{chosen_padding}__{chosen_prompt}"
    golden_index = confirm_indices[0]
    golden = HELPER.golden_features(
        processor,
        rows[golden_index],
        waveforms[golden_index],
        chosen_padding,
    )

    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    config = {
        "stage": "R2-T0v2",
        "selection_role": "all 524 teacher_development_full_train",
        "selection_subsampled": False,
        "selection_count": EXPECTED_DEVELOPMENT,
        "selection_label_count": EXPECTED_LABELS,
        "confirmation_role": "teacher_confirm only",
        "confirmation_count": EXPECTED_CONFIRM,
        "selection_rule": (
            "maximize full-train macro Recall@1, then rank-first, margin, "
            "pad preference, fixed prompt priority"
        ),
        "paddings": list(HELPER.PADDINGS),
        "prompt_sets": {
            key: list(value) for key, value in HELPER.PROMPT_SETS.items()
        },
        "chosen_config": chosen_name,
        "seed": SEED,
        "batch_size": HELPER.BATCH_SIZE,
        "external_clip_rule": (
            "take samples [0,480000); shorter clips remain short before padding"
        ),
        "validation_accessed": False,
        "test_locked_accessed": False,
    }
    HELPER.write_json(ARTIFACT_ROOT / "config.json", config)
    HELPER.write_json(ARTIFACT_ROOT / "metrics.json", metrics)
    HELPER.write_jsonl(ARTIFACT_ROOT / "per_sample.jsonl", per_sample_output)

    ledger = HELPER.read_json(V1_LEDGER)
    event = {
        "event_id": "t0v2_full_train_selection_20260727",
        "attempt_id": (
            "laion/clap-htsat-unfused@"
            "8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a"
        ),
        "selection_data_accessed": ["teacher_development_full_train:524"],
        "selection_subsampled": False,
        "selection_label_count": EXPECTED_LABELS,
        "confirmation_data_accessed": ["teacher_confirm:84"],
        "validation_accessed": False,
        "test_locked_accessed": False,
        "candidate_configs": sorted(metrics),
        "chosen_config": chosen_name,
        "supersedes": "t0_preprocessing_selection_20260727",
        "supersession_reason": "legacy pilot300 covered only 20/28 labels",
    }
    ledger.setdefault("events", [])
    existing = [
        item for item in ledger["events"] if item["event_id"] == event["event_id"]
    ]
    if existing and existing != [event]:
        raise ValueError("T0v2 candidate ledger event 冲突")
    if not existing:
        ledger["events"].append(event)
    HELPER.write_json(LEDGER, ledger)

    old_qualification = HELPER.read_json(
        V1_ROOT / "teacher_qualification_lock.json"
    )
    qualification = copy.deepcopy(old_qualification)
    qualification.update(
        {
            "schema_version": 2,
            "stage": "R2-T0v2",
            "status": "T0V2_PASS",
            "preprocessing": {
                **qualification["preprocessing"],
                "padding": chosen_padding,
                "golden_waveform_sha256": golden["teacher_audio_sha256"],
                "golden_input_features_sha256": golden[
                    "input_features_sha256"
                ],
            },
            "prompts": {
                **qualification["prompts"],
                "name": chosen_prompt,
                "templates": list(HELPER.PROMPT_SETS[chosen_prompt]),
            },
            "data": {
                **qualification["data"],
                "manifest_sha256": HELPER.sha256(VIEW_MANIFEST),
                "teacher_view_manifest_sha256": HELPER.sha256(VIEW_MANIFEST),
                "teacher_view_lock_sha256": HELPER.sha256(VIEW_LOCK),
                "teacher_development_manifest_path": str(
                    DEVELOPMENT_MANIFEST.resolve()
                ),
                "teacher_development_manifest_sha256": HELPER.sha256(
                    DEVELOPMENT_MANIFEST
                ),
                "teacher_development_count": EXPECTED_DEVELOPMENT,
                "teacher_development_label_count": EXPECTED_LABELS,
                "teacher_development_subsampled": False,
                "teacher_confirm_manifest_path": str(
                    CONFIRM_MANIFEST.resolve()
                ),
                "teacher_confirm_manifest_sha256": HELPER.sha256(
                    CONFIRM_MANIFEST
                ),
                "split_lock_sha256": HELPER.sha256(SPLIT_LOCK),
                "old_r2_baseline_lock_sha256": HELPER.sha256(OLD_R2_LOCK),
                "candidate_selection_ledger_prevalidation_sha256": (
                    HELPER.sha256(LEDGER)
                ),
            },
            "golden": golden,
            "selection_metrics": metrics[chosen_name],
            "selection_contract": {
                "role": "teacher_development_full_train",
                "count": EXPECTED_DEVELOPMENT,
                "label_count": EXPECTED_LABELS,
                "subsampled": False,
                "all_manifest_rows_used": True,
            },
            "validation_accessed": False,
            "test_locked_accessed": False,
            "implementation_sha256": HELPER.sha256(Path(__file__).resolve()),
            "implementation_dependencies": {
                str(OLD_HELPER.resolve()): HELPER.sha256(OLD_HELPER)
            },
        }
    )
    HELPER.write_json(
        ARTIFACT_ROOT / "teacher_qualification_lock.json", qualification
    )
    decision = {
        "stage": "R2-T0v2",
        "status": "PASS",
        "chosen_config": chosen_name,
        "selection_count": EXPECTED_DEVELOPMENT,
        "selection_label_count": EXPECTED_LABELS,
        "selection_subsampled": False,
        "teacher_qualification_lock_sha256": HELPER.sha256(
            ARTIFACT_ROOT / "teacher_qualification_lock.json"
        ),
        "authorized_next_stage": "R2-Q develop and transaction preparation",
        "validation_accessed": False,
        "test_locked_accessed": False,
    }
    HELPER.write_json(ARTIFACT_ROOT / "decision.json", decision)
    HELPER.write_json(
        ARTIFACT_ROOT / "environment.json",
        {
            "conda_env": "M4",
            "torch": torch.__version__,
            "numpy": np.__version__,
            "device": "one explicit CUDA device",
            "validation_accessed": False,
            "test_locked_accessed": False,
        },
    )
    (ARTIFACT_ROOT / "review.md").write_text(
        "\n".join(
            [
                "# 音频语义教师 T0v2 完整训练集复核",
                "",
                "- 结论：PASS",
                "- 配置选择：全部 524 条 teacher-development train",
                "- 类别覆盖：28/28",
                "- 抽样：false",
                f"- 冻结配置：`{chosen_name}`",
                "- teacher_confirm：84 条，仅确认",
                "- validation/test_locked：未访问",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "status": "PASS",
        "chosen_config": chosen_name,
        "selection_count": EXPECTED_DEVELOPMENT,
        "selection_label_count": EXPECTED_LABELS,
        "selection_subsampled": False,
        "selection_metrics": metrics[chosen_name][
            "teacher_development_full_train"
        ],
        "teacher_confirm_metrics": metrics[chosen_name]["teacher_confirm"],
        "teacher_qualification_lock_sha256": decision[
            "teacher_qualification_lock_sha256"
        ],
    }


def main() -> int:
    try:
        result = run()
    except Exception as exc:
        print(f"T0v2 完整训练集资格准备失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
