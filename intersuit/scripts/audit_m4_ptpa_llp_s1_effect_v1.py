#!/usr/bin/env python3
"""独立复核 LLP S1 effect 输出的计数、视频等权指标和负样本合法性。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_RESULT = INTERSUIT_ROOT / "datasets/LLP_PILOT_MEDIA_V2/s1_effect_eval_v1"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/m4_ptpa_llp_s1_effect_eval_v1.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def video_equal_mean(rows: list[dict[str, Any]], key: str) -> float:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get(key) is not None:
            grouped[row["physical_media_id"]].append(float(row[key]))
    if not grouped:
        raise ValueError(f"无法独立复算空指标：{key}")
    return mean(mean(values) for values in grouped.values())


def dense_labels(path: Path) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if float(row["offset"]) > float(row["onset"]):
                grouped[row["filename"]].add(row["event_labels"])
    return dict(grouped)


def weak_labels(path: Path) -> dict[str, set[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {
            row["filename"]: set(row["event_labels"].split(","))
            for row in csv.DictReader(handle, delimiter="\t")
        }


def assert_close(observed: float, expected: float, label: str) -> None:
    if not math.isclose(observed, expected, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"独立复算不一致 {label}: {observed} != {expected}")


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    metrics_path = args.result_root / "metrics.json"
    per_query_path = args.result_root / "per_query.jsonl"
    prototype_path = args.result_root / "prototype_selection.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    per_query = read_jsonl(per_query_path)
    prototypes = json.loads(prototype_path.read_text(encoding="utf-8"))
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    feature_manifest = Path(contract["inputs"]["feature_manifest"]["path"])
    if not feature_manifest.is_absolute():
        feature_manifest = REPO_ROOT / feature_manifest
    feature_rows = read_jsonl(feature_manifest)
    by_media = {row["physical_media_id"]: row for row in feature_rows}
    train_ids = {
        row["physical_media_id"] for row in feature_rows if row["official_split"] == "train"
    }
    validation_ids = {
        row["physical_media_id"] for row in feature_rows if row["official_split"] == "validation"
    }
    dense_path = REPO_ROOT / contract["inputs"]["validation_dense_audio"]["path"]
    weak_path = REPO_ROOT / contract["inputs"]["train_weak_labels"]["path"]
    dense = dense_labels(dense_path)
    weak = weak_labels(weak_path)
    dense_by_media = {
        media_id: dense[by_media[media_id]["sample_id"].split(":", 2)[2]]
        for media_id in validation_ids
    }
    expected_groups = len(contract["representations"]) * len(
        contract["prototype_sampling"]["fixed_seeds"]
    )
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in per_query:
        grouped[(row["representation"], int(row["seed"]))].append(row)
    if len(per_query) != expected_groups * metrics["query_count"]:
        raise ValueError("per_query 总行数不匹配")
    if len(grouped) != expected_groups:
        raise ValueError("representation/seed 组数不匹配")

    recalculated = {}
    mismatch_label_conflicts = 0
    acoustic_label_conflicts = 0
    same_class_label_failures = 0
    same_video_index_failures = 0
    for (representation, seed), rows in sorted(grouped.items()):
        if len(rows) != 99 or len({row["physical_media_id"] for row in rows}) != 50:
            raise ValueError(f"query/video 计数不匹配：{representation}/{seed}")
        label_total = Counter(row["target_label"] for row in rows)
        label_correct = Counter(row["target_label"] for row in rows if row["correct"])
        macro = mean(label_correct[label] / label_total[label] for label in sorted(label_total))
        margin = video_equal_mean(rows, "semantic_margin")
        mismatch_diff = video_equal_mean(rows, "correct_minus_mismatch_target_score")
        rank_first = video_equal_mean(rows, "rank_first_value")
        stored = next(
            item
            for item in metrics["representations"][representation]["per_seed"]
            if item["seed"] == seed
        )
        assert_close(macro, stored["macro_recall_at_1"], "macro")
        assert_close(margin, stored["semantic_margin_mean"], "margin")
        assert_close(mismatch_diff, stored["correct_minus_mismatch_target_score_mean"], "mismatch")
        assert_close(rank_first, stored["rank_first"], "rank_first")
        recalculated[f"{representation}:{seed}"] = {
            "macro_recall_at_1": macro,
            "semantic_margin_mean_video_equal": margin,
            "correct_minus_mismatch_target_score_mean_video_equal": mismatch_diff,
            "rank_first_video_equal": rank_first,
        }
        for row in rows:
            target = row["target_label"]
            mismatch_id = row["mismatch_media_id"]
            acoustic_id = row["acoustic_hard_negative_media_id"]
            mismatch_label_conflicts += int(target in dense_by_media[mismatch_id])
            acoustic_label_conflicts += int(target in dense_by_media[acoustic_id])
            same_id = row["cross_video_same_class_media_id"]
            if same_id is not None:
                same_class_label_failures += int(target not in dense_by_media[same_id])
            index = row["same_video_high_rms_negative_window"]
            if index is not None and not 0 <= int(index) < 10:
                same_video_index_failures += 1

    prototype_failures = 0
    expected_fraction = float(contract["prototype_sampling"]["per_label_fraction"])
    minimum = int(contract["prototype_sampling"]["minimum_per_label"])
    for representation, seeds in prototypes.items():
        for seed, labels in seeds.items():
            for label, selected_ids in labels.items():
                candidates = [
                    media_id
                    for media_id in train_ids
                    if label in weak[by_media[media_id]["sample_id"].split(":", 2)[2]]
                ]
                expected_count = min(
                    len(candidates), max(minimum, int(math.floor(len(candidates) * expected_fraction)))
                )
                prototype_failures += int(len(selected_ids) != expected_count)
                prototype_failures += sum(media_id not in candidates for media_id in selected_ids)
                prototype_failures += int(len(selected_ids) != len(set(selected_ids)))

    status = "PASS" if all(
        value == 0
        for value in (
            mismatch_label_conflicts, acoustic_label_conflicts,
            same_class_label_failures, same_video_index_failures, prototype_failures,
        )
    ) else "FAIL"
    audit = {
        "status": status,
        "metrics_status_reproduced": metrics["status"],
        "per_query_rows": len(per_query),
        "representation_seed_groups": len(grouped),
        "recalculated": recalculated,
        "mismatch_target_label_conflicts": mismatch_label_conflicts,
        "acoustic_hard_negative_target_label_conflicts": acoustic_label_conflicts,
        "same_class_label_failures": same_class_label_failures,
        "same_video_index_failures": same_video_index_failures,
        "prototype_selection_failures": prototype_failures,
        "formal_test_rows": 0,
        "training_performed": False,
        "input_sha256": {
            "metrics.json": sha256(metrics_path),
            "per_query.jsonl": sha256(per_query_path),
            "prototype_selection.json": sha256(prototype_path),
            "contract": sha256(args.contract),
        },
    }
    audit_path = args.result_root / "independent_audit.json"
    report_path = args.result_root / "INDEPENDENT_AUDIT_ZH.md"
    write_json(audit_path, audit)
    report_path.write_text(
        "\n".join(
            [
                "# LLP S1 效果评测独立审计", "",
                f"- 审计状态：`{status}`",
                f"- 复现的效果结论：`{metrics['status']}`",
                f"- per-query 行：{len(per_query)}；representation×seed：{len(grouped)}。",
                f"- mismatch 标签冲突：{mismatch_label_conflicts}。",
                f"- acoustic hard-negative 标签冲突：{acoustic_label_conflicts}。",
                f"- same-class 标签失败：{same_class_label_failures}。",
                f"- prototype 选择失败：{prototype_failures}。",
                "- 已独立复算 macro Recall@1、视频等权 semantic margin、correct-mismatch score 和 rank-first。",
                "- 正式 test 行数为 0；未训练任何参数。", "",
            ]
        ),
        encoding="utf-8",
    )
    manifest_lock_path = args.result_root / "manifest_lock.json"
    manifest_lock = json.loads(manifest_lock_path.read_text(encoding="utf-8"))
    manifest_lock["outputs"][audit_path.name] = sha256(audit_path)
    manifest_lock["outputs"][report_path.name] = sha256(report_path)
    write_json(manifest_lock_path, manifest_lock)
    return audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    return parser.parse_args()


if __name__ == "__main__":
    result = run(parse_args())
    print(json.dumps({"status": result["status"], "metrics": result["metrics_status_reproduced"]}, ensure_ascii=False))
