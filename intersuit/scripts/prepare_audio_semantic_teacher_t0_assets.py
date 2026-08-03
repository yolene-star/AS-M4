#!/usr/bin/env python
"""构建并校验音频语义教师 T0 的数据划分与旧 R2 可比基线锁。

本脚本只读取已经冻结的 R0/R2 产物，不加载模型、不访问 GPU、不读取
test_locked 媒体或指标。输出是确定性的；相同输入、seed 和代码必须产生相同 SHA。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
R0_ROOT = INTERSUIT_ROOT / "harness/artifacts/audio_semantic_reconstruction_r0_contract"
R2_ROOT = INTERSUIT_ROOT / "harness/artifacts/audio_semantic_reconstruction_r2_targets"
DATASET_ROOT = INTERSUIT_ROOT / "datasets/AVE_HF_EXPANDED"

DEFAULT_EXPANDED_SPLIT = R0_ROOT / "expanded_split_manifest.jsonl"
DEFAULT_PILOT_SPLIT = R0_ROOT / "pilot300_split_manifest.jsonl"
DEFAULT_CONFIRM = DATASET_ROOT / "teacher_confirm_manifest.jsonl"
DEFAULT_STUDENT_TRAIN = (
    DATASET_ROOT / "teacher_confirm_excluded_from_student_train.jsonl"
)
DEFAULT_TEACHER_DEVELOPMENT = (
    DATASET_ROOT / "teacher_development_full_train_manifest.jsonl"
)
DEFAULT_SPLIT_LOCK = DATASET_ROOT / "teacher_confirm_split_lock.json"
DEFAULT_R2_PER_SAMPLE = R2_ROOT / "per_sample.jsonl"
DEFAULT_OLD_PER_SAMPLE = R2_ROOT / "old_r2_baseline_per_sample.jsonl"
DEFAULT_OLD_LOCK = R2_ROOT / "old_r2_baseline_lock.json"

SEED = 20260727
CONFIRM_PER_CLASS = 3
EXPECTED_LABELS = 28
EXPECTED_TRAIN = 608
EXPECTED_VALIDATION = 196
EXPECTED_TEST_LOCKED = 196
EXPECTED_CONFIRM = EXPECTED_LABELS * CONFIRM_PER_CLASS
EXPECTED_STUDENT_TRAIN = EXPECTED_TRAIN - EXPECTED_CONFIRM
R2_SEEDS = (17, 23, 47)
BOOTSTRAP_REPLICATES = 10_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json_sha256(payload: Any) -> str:
    value = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256_text(value)


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"缺少 JSON：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少 JSONL：{path}")
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"JSONL 为空：{path}")
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            )


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPO_ROOT))


def stable_selection_key(row: dict[str, Any]) -> tuple[str, str]:
    value = (
        f"{SEED}:{row['canonical_label']}:{row['physical_media_id']}"
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest(), row["physical_media_id"]


def require_unique(rows: list[dict[str, Any]], field: str, name: str) -> set[str]:
    values = [str(row[field]) for row in rows]
    if len(values) != len(set(values)):
        duplicates = sorted(
            value for value, count in Counter(values).items() if count > 1
        )
        raise ValueError(f"{name} 的 {field} 重复：{duplicates[:10]}")
    return set(values)


def build_teacher_confirm(
    expanded_rows: list[dict[str, Any]],
    pilot_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    split_counts = Counter(row["diagnostic_split"] for row in expanded_rows)
    expected = {
        "train": EXPECTED_TRAIN,
        "validation": EXPECTED_VALIDATION,
        "test_locked": EXPECTED_TEST_LOCKED,
    }
    if dict(sorted(split_counts.items())) != expected:
        raise ValueError(
            f"R0 expanded split 数量异常：{dict(split_counts)} != {expected}"
        )

    expanded_ids = require_unique(
        expanded_rows, "physical_media_id", "expanded split"
    )
    pilot_ids = require_unique(pilot_rows, "physical_media_id", "pilot split")
    if expanded_ids & pilot_ids:
        raise ValueError("expanded 与 pilot300 存在 physical media 重叠")

    train_rows = [
        row for row in expanded_rows if row["diagnostic_split"] == "train"
    ]
    validation_ids = {
        row["physical_media_id"]
        for row in expanded_rows
        if row["diagnostic_split"] == "validation"
    }
    test_ids = {
        row["physical_media_id"]
        for row in expanded_rows
        if row["diagnostic_split"] == "test_locked"
    }

    per_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in train_rows:
        if row["physical_media_id"] in pilot_ids:
            continue
        if row.get("audio_required") is not True:
            raise ValueError("train 中出现 audio_required!=true")
        if row.get("supervision_level") != "clip":
            raise ValueError("train 中出现 supervision_level!=clip")
        per_label[row["canonical_label"]].append(row)

    if len(per_label) != EXPECTED_LABELS:
        raise ValueError(f"train 类别数异常：{len(per_label)}")

    confirm_source: list[dict[str, Any]] = []
    shortages: dict[str, int] = {}
    for label in sorted(per_label):
        candidates = sorted(per_label[label], key=stable_selection_key)
        if len(candidates) < CONFIRM_PER_CLASS:
            shortages[label] = len(candidates)
            continue
        confirm_source.extend(candidates[:CONFIRM_PER_CLASS])
    if shortages:
        raise ValueError(f"teacher_confirm 类别样本不足：{shortages}")

    confirm_ids = {row["physical_media_id"] for row in confirm_source}
    if len(confirm_ids) != EXPECTED_CONFIRM:
        raise ValueError("teacher_confirm physical ID 数量不是 84")
    if confirm_ids & pilot_ids:
        raise ValueError("teacher_confirm 与 pilot300 重叠")
    if confirm_ids & validation_ids:
        raise ValueError("teacher_confirm 与 validation 重叠")
    if confirm_ids & test_ids:
        raise ValueError("teacher_confirm 与 test_locked 重叠")

    confirm_rows = []
    for row in sorted(
        confirm_source,
        key=lambda item: (
            item["canonical_label"],
            stable_selection_key(item),
        ),
    ):
        output = dict(row)
        output["t0_role"] = "teacher_confirm"
        output["student_training_allowed"] = False
        output["selection_seed"] = SEED
        output["selection_rule"] = "per_label_stable_sha256_first_3"
        confirm_rows.append(output)

    student_rows = []
    for row in train_rows:
        if row["physical_media_id"] in confirm_ids:
            continue
        output = dict(row)
        output["t0_role"] = "student_train_after_teacher_confirm"
        output["student_training_allowed"] = True
        output["teacher_confirm_excluded"] = True
        student_rows.append(output)
    student_rows.sort(
        key=lambda item: (
            item["canonical_label"],
            item["physical_media_id"],
        )
    )

    if len(student_rows) != EXPECTED_STUDENT_TRAIN:
        raise ValueError(
            f"student train 数量异常：{len(student_rows)} != "
            f"{EXPECTED_STUDENT_TRAIN}"
        )
    require_unique(student_rows, "physical_media_id", "student train")
    student_ids = {row["physical_media_id"] for row in student_rows}
    if student_ids & confirm_ids:
        raise ValueError("student train 仍含 teacher_confirm")
    if student_ids | confirm_ids != {
        row["physical_media_id"] for row in train_rows
    }:
        raise ValueError("student train + teacher_confirm 不能还原原始 train")

    confirm_label_counts = Counter(
        row["canonical_label"] for row in confirm_rows
    )
    student_label_counts = Counter(
        row["canonical_label"] for row in student_rows
    )
    if set(confirm_label_counts.values()) != {CONFIRM_PER_CLASS}:
        raise ValueError(
            f"teacher_confirm 每类数量异常：{dict(confirm_label_counts)}"
        )

    audit = {
        "status": "PASS",
        "selection_seed": SEED,
        "selection_rule": "per_label_stable_sha256_first_3",
        "expanded_split_counts": dict(sorted(split_counts.items())),
        "teacher_confirm_count": len(confirm_rows),
        "teacher_confirm_label_count": len(confirm_label_counts),
        "teacher_confirm_per_label": dict(sorted(confirm_label_counts.items())),
        "student_train_count": len(student_rows),
        "student_train_per_label": dict(sorted(student_label_counts.items())),
        "physical_overlap": {
            "confirm_vs_pilot300": len(confirm_ids & pilot_ids),
            "confirm_vs_validation": len(confirm_ids & validation_ids),
            "confirm_vs_test_locked": len(confirm_ids & test_ids),
            "confirm_vs_student_train": len(confirm_ids & student_ids),
            "expanded_vs_pilot300": len(expanded_ids & pilot_ids),
        },
        "formal_test_metric_accessed": False,
        "test_locked_media_accessed": False,
    }
    return confirm_rows, student_rows, audit


def build_teacher_development(
    student_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    development_rows = []
    for row in student_rows:
        output = dict(row)
        output["teacher_development_role"] = "full_train_after_confirm_exclusion"
        output["teacher_development_allowed"] = True
        output["teacher_development_subsampled"] = False
        development_rows.append(output)
    if len(development_rows) != EXPECTED_STUDENT_TRAIN:
        raise ValueError("teacher-development 必须使用全部 524 条")
    if {
        row["physical_media_id"] for row in development_rows
    } != {
        row["physical_media_id"] for row in student_rows
    }:
        raise ValueError("teacher-development 与 student train ID 集合不一致")
    development_counts = Counter(
        row["canonical_label"] for row in development_rows
    )
    if len(development_counts) != EXPECTED_LABELS or min(
        development_counts.values()
    ) <= 0:
        raise ValueError(
            f"teacher-development 类别覆盖异常：{dict(development_counts)}"
        )
    return development_rows, dict(sorted(development_counts.items()))


def bootstrap_mean_ci(values: list[float], seed: int) -> list[float]:
    array = np.asarray(values, dtype=np.float64)
    rng = np.random.default_rng(seed)
    indices = rng.integers(
        0, array.size, size=(BOOTSTRAP_REPLICATES, array.size)
    )
    means = array[indices].mean(axis=1)
    return [
        float(np.quantile(means, 0.025)),
        float(np.quantile(means, 0.975)),
    ]


def assert_close(actual: float, expected: float, name: str) -> None:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"{name} 不可重算：{actual} != {expected}")


def build_old_r2_lock(
    r2_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    r2_metrics: dict[str, Any],
    r2_config: dict[str, Any],
    r2_target_lock: dict[str, Any],
    source_paths: list[Path],
    evaluator_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    old_rows = [
        row for row in r2_rows if row.get("target") == "old_answer_token_mean"
    ]
    if len(old_rows) != EXPECTED_VALIDATION:
        raise ValueError(f"旧 R2 行数异常：{len(old_rows)}")
    require_unique(old_rows, "sample_id", "旧 R2 baseline")

    validation_ids = [row["youtube_id"] for row in validation_rows]
    old_ids = [row["sample_id"] for row in old_rows]
    if old_ids != validation_ids:
        raise ValueError("旧 R2 sample 顺序与冻结 validation 不一致")

    labels = sorted({row["canonical_label"] for row in old_rows})
    if len(labels) != EXPECTED_LABELS:
        raise ValueError(f"旧 R2 类别数异常：{len(labels)}")
    per_class_total = Counter(row["canonical_label"] for row in old_rows)
    per_class_correct = Counter(
        row["canonical_label"] for row in old_rows if row["correct"]
    )
    per_class_recall = {
        label: per_class_correct[label] / per_class_total[label]
        for label in labels
    }
    margins = [float(row["margin"]) for row in old_rows]
    reproduced = {
        "query_count": len(old_rows),
        "label_count": len(labels),
        "macro_class_recall_at_1": mean(per_class_recall.values()),
        "balanced_accuracy": mean(per_class_recall.values()),
        "accuracy": sum(bool(row["correct"]) for row in old_rows)
        / len(old_rows),
        "rank_first": sum(value > 0 for value in margins) / len(margins),
        "margin_mean": mean(margins),
        "margin_bootstrap_95_ci_by_seed": {
            str(seed): bootstrap_mean_ci(margins, seed) for seed in R2_SEEDS
        },
        "per_class_recall_at_1": per_class_recall,
        "per_class_query_count": dict(sorted(per_class_total.items())),
        "random_class_baseline": 1.0 / len(labels),
    }

    expected = r2_metrics["targets"]["old_answer_token_mean"]
    for key in (
        "macro_class_recall_at_1",
        "balanced_accuracy",
        "accuracy",
        "rank_first",
        "margin_mean",
    ):
        assert_close(
            reproduced[key], float(expected[key]["mean"]), f"old R2 {key}"
        )
    expected_lower = min(
        item[0]
        for item in reproduced["margin_bootstrap_95_ci_by_seed"].values()
    )
    expected_upper = min(
        item[1]
        for item in reproduced["margin_bootstrap_95_ci_by_seed"].values()
    )
    assert_close(
        expected_lower,
        float(expected["margin_ci_lower_worst_seed"]),
        "old R2 margin CI lower",
    )
    assert_close(
        expected_upper,
        float(expected["margin_ci_upper_worst_seed"]),
        "old R2 margin CI upper",
    )

    evaluator_sha = sha256(evaluator_path)
    if evaluator_sha != r2_config["implementation_sha256"]:
        raise ValueError(
            "当前 R2 evaluator SHA 与历史 config implementation_sha256 不一致"
        )

    old_target_spec = r2_target_lock["old_answer_token_mean"]
    lock = {
        "schema_version": 1,
        "status": "PASS",
        "baseline_name": "old_answer_token_mean",
        "source_files": {
            relative(path): sha256(path) for path in source_paths
        },
        "evaluator": {
            "path": relative(evaluator_path),
            "sha256": evaluator_sha,
            "implementation_sha256_matches_historical_config": True,
            "label_order": labels,
            "label_order_rule": "sorted canonical labels",
            "prompt_aggregation": (
                "mean of frozen Qwen input-embedding rows for the full "
                "assistant target token sequence, including chat envelope "
                "and end token"
            ),
            "prototype_normalization": "L2 normalize, eps=1e-12",
            "audio_normalization": "L2 normalize, eps=1e-12",
            "similarity": "dot product of normalized vectors",
            "prediction": (
                "torch.argmax over lexically sorted labels; first index wins ties"
            ),
            "hard_negative": (
                "set true-label similarity to -inf, then torch.argmax; "
                "first index wins ties"
            ),
            "margin": "positive_similarity - hard_negative_similarity",
            "rank_first": "strict margin > 0; ties at 0 are failures",
            "bootstrap": {
                "replicates": BOOTSTRAP_REPLICATES,
                "seeds": list(R2_SEEDS),
                "rng": "numpy.random.default_rng",
                "quantiles": [0.025, 0.975],
            },
        },
        "validation": {
            "count": len(validation_ids),
            "ordered_youtube_ids_sha256": sha256_text(
                "\n".join(validation_ids) + "\n"
            ),
            "ordered_physical_ids_sha256": sha256_text(
                "\n".join(f"AVE:{value}" for value in validation_ids) + "\n"
            ),
            "same_order_as_r0_validation": True,
            "formal_test_metric_accessed": False,
        },
        "target_spec": {
            "canonical_json_sha256": canonical_json_sha256(old_target_spec),
            "label_count": len(old_target_spec),
            "historical_reconstruction": r2_config[
                "old_target_reconstruction"
            ],
        },
        "reproduced_metrics": reproduced,
        "historical_metrics": expected,
        "golden_reproduction": {
            "status": "PASS",
            "absolute_tolerance": 1e-12,
        },
    }
    return old_rows, lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expanded-split", type=Path, default=DEFAULT_EXPANDED_SPLIT
    )
    parser.add_argument("--pilot-split", type=Path, default=DEFAULT_PILOT_SPLIT)
    parser.add_argument("--confirm-output", type=Path, default=DEFAULT_CONFIRM)
    parser.add_argument(
        "--student-train-output", type=Path, default=DEFAULT_STUDENT_TRAIN
    )
    parser.add_argument(
        "--teacher-development-output",
        type=Path,
        default=DEFAULT_TEACHER_DEVELOPMENT,
    )
    parser.add_argument(
        "--split-lock-output", type=Path, default=DEFAULT_SPLIT_LOCK
    )
    parser.add_argument(
        "--r2-per-sample", type=Path, default=DEFAULT_R2_PER_SAMPLE
    )
    parser.add_argument(
        "--old-per-sample-output", type=Path, default=DEFAULT_OLD_PER_SAMPLE
    )
    parser.add_argument(
        "--old-lock-output", type=Path, default=DEFAULT_OLD_LOCK
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> dict[str, Any]:
    expanded_split = args.expanded_split.resolve()
    pilot_split = args.pilot_split.resolve()
    confirm_output = args.confirm_output.resolve()
    student_train_output = args.student_train_output.resolve()
    teacher_development_output = args.teacher_development_output.resolve()
    split_lock_output = args.split_lock_output.resolve()
    r2_per_sample = args.r2_per_sample.resolve()
    old_per_sample_output = args.old_per_sample_output.resolve()
    old_lock_output = args.old_lock_output.resolve()

    expanded_rows = read_jsonl(expanded_split)
    pilot_rows = read_jsonl(pilot_split)
    confirm_rows, student_rows, split_audit = build_teacher_confirm(
        expanded_rows, pilot_rows
    )
    write_jsonl(confirm_output, confirm_rows)
    write_jsonl(student_train_output, student_rows)
    development_rows, development_counts = build_teacher_development(
        student_rows
    )
    write_jsonl(teacher_development_output, development_rows)

    script_path = Path(__file__).resolve()
    split_lock = {
        "schema_version": 1,
        "status": "PASS",
        "inputs": {
            relative(expanded_split): sha256(expanded_split),
            relative(pilot_split): sha256(pilot_split),
            relative(R0_ROOT / "manifest_lock.json"): sha256(
                R0_ROOT / "manifest_lock.json"
            ),
        },
        "implementation": {
            "path": relative(script_path),
            "sha256": sha256(script_path),
        },
        "outputs": {
            relative(confirm_output): sha256(confirm_output),
            relative(student_train_output): sha256(student_train_output),
            relative(teacher_development_output): sha256(
                teacher_development_output
            ),
        },
        "audit": {
            **split_audit,
            "teacher_development_count": len(development_rows),
            "teacher_development_label_count": len(development_counts),
            "teacher_development_per_label": development_counts,
            "teacher_development_subsampled": False,
            "teacher_development_matches_student_train_ids": True,
        },
    }
    write_json(split_lock_output, split_lock)

    r2_metrics_path = R2_ROOT / "metrics.json"
    r2_config_path = R2_ROOT / "config.json"
    r2_target_lock_path = R2_ROOT / "target_lock.json"
    r2_manifest_lock_path = R2_ROOT / "manifest_lock.json"
    r2_decision_path = R2_ROOT / "decision.json"
    evaluator_path = INTERSUIT_ROOT / "scripts/evaluate_audio_semantic_reconstruction_r2.py"
    source_paths = [
        r2_per_sample,
        r2_metrics_path,
        r2_config_path,
        r2_target_lock_path,
        r2_manifest_lock_path,
        r2_decision_path,
    ]
    validation_rows = [
        row
        for row in expanded_rows
        if row["diagnostic_split"] == "validation"
    ]
    old_rows, old_lock = build_old_r2_lock(
        read_jsonl(r2_per_sample),
        validation_rows,
        read_json(r2_metrics_path),
        read_json(r2_config_path),
        read_json(r2_target_lock_path),
        source_paths,
        evaluator_path,
    )
    write_jsonl(old_per_sample_output, old_rows)
    old_lock["derived_output"] = {
        "path": relative(old_per_sample_output),
        "sha256": sha256(old_per_sample_output),
    }
    old_lock["implementation"] = {
        "path": relative(script_path),
        "sha256": sha256(script_path),
    }
    write_json(old_lock_output, old_lock)

    result = {
        "status": "PASS",
        "teacher_confirm_count": len(confirm_rows),
        "student_train_count": len(student_rows),
        "old_r2_baseline_count": len(old_rows),
        "outputs": {
            relative(confirm_output): sha256(confirm_output),
            relative(student_train_output): sha256(student_train_output),
            relative(teacher_development_output): sha256(
                teacher_development_output
            ),
            relative(split_lock_output): sha256(split_lock_output),
            relative(old_per_sample_output): sha256(old_per_sample_output),
            relative(old_lock_output): sha256(old_lock_output),
        },
    }
    return result


def main() -> int:
    try:
        result = run(parse_args())
    except Exception as exc:
        print(f"T0 资产构建失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
