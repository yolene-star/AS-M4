#!/usr/bin/env python3
"""使用冻结官方 LongVALE 指标审查 Dynamic Selector V3 P3 canary。"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from intersuit.harness.dynamic_selector_v2_metrics import run_longvale_golden


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = PROJECT_ROOT / "intersuit/harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/p3_longvale_canary20"
DEFAULT_OFFICIAL_REPO = PROJECT_ROOT / "intersuit/datasets/LongVALE_V2/official_repo"
RANDOM_PREFIX = "random_top8_seed_"
MISMATCH_PREFIX = "oracle_mismatch_seed_"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--official-repo", type=Path, default=DEFAULT_OFFICIAL_REPO)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def finite_tree(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(finite_tree(item) for item in value.values())
    if isinstance(value, list):
        return all(finite_tree(item) for item in value)
    return True


def has_degenerate_repetition(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    sentences = [part.strip(" .,!?:;\t\n") for part in re.split(r"[.!?\n]+", normalized)]
    sentences = [part for part in sentences if len(part.split()) >= 4]
    if any(left == right for left, right in zip(sentences, sentences[1:])):
        return True
    tokens = normalized.split()
    spans = [tuple(tokens[index : index + 8]) for index in range(max(0, len(tokens) - 7))]
    return len(spans) != len(set(spans))


def official_predictions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map each frozen one-event video to its local annotation index zero."""

    return [
        {
            "video_id": str(row["physical_media_id"]),
            "task": "seg_captioning",
            "query_id": 0,
            "answer": str(row["prediction"]),
            "info": {"sentence_id": 0},
        }
        for row in rows
    ]


def anomaly_counts(rows: list[dict[str, Any]], max_new_tokens: int) -> dict[str, int]:
    def token_debug(row: dict[str, Any]) -> dict[str, Any]:
        return ((row.get("generation_debug") or {}).get("tokens") or {})

    return {
        "empty_output": sum(not str(row.get("prediction", "")).strip() for row in rows),
        "first_token_eos": sum(bool(token_debug(row).get("first_token_is_eos")) for row in rows),
        "max_length_without_eos": sum(
            int(token_debug(row).get("new_token_count") or 0) == max_new_tokens
            and int(token_debug(row).get("eos_token_id") or -1)
            not in list(token_debug(row).get("last_20_new_token_ids") or [])
            for row in rows
        ),
        "repeated_text": sum(has_degenerate_repetition(str(row.get("prediction", ""))) for row in rows),
        "nonfinite_output": sum(not finite_tree(row) for row in rows),
    }


def validate_matrix(
    conditions: list[str],
    rows_by_condition: dict[str, list[dict[str, Any]]],
    expected_count: int,
) -> None:
    reference_ids = None
    for condition in conditions:
        rows = rows_by_condition.get(condition)
        if rows is None or len(rows) != expected_count:
            raise ValueError(f"{condition} prediction 数量异常")
        ids = [str(row["sample_id"]) for row in rows]
        if len(set(ids)) != expected_count:
            raise ValueError(f"{condition} sample_id 不唯一")
        if any(row.get("experiment_id") != condition for row in rows):
            raise ValueError(f"{condition} experiment_id 漂移")
        if reference_ids is None:
            reference_ids = ids
        elif ids != reference_ids:
            raise ValueError(f"{condition} 样本顺序/集合漂移")


def operational_checks(rows_by_condition: dict[str, list[dict[str, Any]]]) -> dict[str, bool]:
    all_rows = rows_by_condition["fixed_all"]
    oracle_rows = rows_by_condition["oracle_top8"]
    mismatch_names = sorted(name for name in rows_by_condition if name.startswith(MISMATCH_PREFIX))
    all_true = all(
        (row["generation_debug"]["tokens"]["audio_input"]["selection_trace"]["selected_window_count"]
         == row["generation_debug"]["tokens"]["audio_input"]["full_window_count"])
        for row in all_rows
    )
    oracle_eight = all(
        row["generation_debug"]["tokens"]["audio_input"]["selection_trace"]["selected_window_count"] == 8
        for row in oracle_rows
    )
    mismatch_nonself = all(
        row.get("mismatch_source_physical_media_id")
        and row.get("mismatch_source_physical_media_id") != row.get("physical_media_id")
        for name in mismatch_names for row in rows_by_condition[name]
    )
    return {
        "true_all_selected_equals_full": all_true,
        "oracle_selected_count_is_eight": oracle_eight,
        "mismatch_physical_nonself": mismatch_nonself,
        "no_empty_output": all(str(row.get("prediction", "")).strip() for rows in rows_by_condition.values() for row in rows),
        "all_rows_finite": all(finite_tree(row) for rows in rows_by_condition.values() for row in rows),
    }


def summarize_metrics(metrics: dict[str, dict[str, float]]) -> dict[str, Any]:
    random_names = sorted(name for name in metrics if name.startswith(RANDOM_PREFIX))
    mismatch_names = sorted(name for name in metrics if name.startswith(MISMATCH_PREFIX))
    if len(random_names) != 5 or len(mismatch_names) != 5:
        raise ValueError("Random/mismatch 条件必须各有五个 seed")
    oracle = metrics["oracle_top8"]["METEOR"]
    random_mean = sum(metrics[name]["METEOR"] for name in random_names) / 5
    mismatch_mean = sum(metrics[name]["METEOR"] for name in mismatch_names) / 5
    return {
        "oracle": oracle,
        "fixed_all": metrics["fixed_all"]["METEOR"],
        "current_micro_top8": metrics["current_micro_top8"]["METEOR"],
        "behavior_off": metrics["behavior_off"]["METEOR"],
        "random_mean": random_mean,
        "mismatch_mean": mismatch_mean,
        "oracle_minus_fixed_all": oracle - metrics["fixed_all"]["METEOR"],
        "oracle_minus_current_micro_top8": oracle - metrics["current_micro_top8"]["METEOR"],
        "oracle_minus_behavior_off": oracle - metrics["behavior_off"]["METEOR"],
        "oracle_minus_random_mean": oracle - random_mean,
        "oracle_minus_mismatch_mean": oracle - mismatch_mean,
    }


def main() -> None:
    args = parse_args()
    lock = json.loads((args.run_root / "p3_canary_lock.json").read_text(encoding="utf-8"))
    conditions = [str(value) for value in lock["conditions"]]
    if lock.get("p3_full_allowed") is not False or lock.get("selector_training_allowed") is not False:
        raise ValueError("P3 canary 初始权限锁漂移")
    rows_by_condition = {
        condition: read_jsonl(args.run_root / "predictions" / f"{condition}.jsonl")
        for condition in conditions
    }
    validate_matrix(conditions, rows_by_condition, int(lock["sample_count"]))
    checks = operational_checks(rows_by_condition)

    output_dir = args.run_root / "official_metrics"
    input_dir = output_dir / "official_inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    metrics = {}
    anomalies = {}
    for condition in conditions:
        input_path = input_dir / f"{condition}.json"
        input_path.write_text(
            json.dumps(official_predictions(rows_by_condition[condition]), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        metrics[condition] = run_longvale_golden(
            official_repo=args.official_repo,
            annotation_path=args.run_root / "canary_annotation.json",
            predictions_path=input_path,
        )
        anomalies[condition] = anomaly_counts(rows_by_condition[condition], int(lock["max_new_tokens"]))
    operational_pass = all(checks.values())
    result = {
        "artifact_kind": "m4_ptpa_dynamic_selector_v3_p3_canary_review",
        "status": "pass_to_frozen_100" if operational_pass else "fail_blocked",
        "sample_count": int(lock["sample_count"]),
        "condition_count": len(conditions),
        "metrics": metrics,
        "meteor_summary": summarize_metrics(metrics),
        "anomalies": anomalies,
        "operational_checks": checks,
        "canary_is_statistical_oracle_gate": False,
        "oracle_decision": "defer_to_frozen_100" if operational_pass else "blocked",
        "p3_full_allowed": operational_pass,
        "selector_training_allowed": False,
        "confirm_accessed": False,
    }
    (output_dir / "p3_canary_review.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not operational_pass:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
