#!/usr/bin/env python3
"""使用冻结 LongVALE 官方指标评测 selector V2 oracle pilot。"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from intersuit.harness.dynamic_selector_v2_metrics import run_longvale_golden


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PILOT_ROOT = PROJECT_ROOT / "intersuit/harness/artifacts/dynamic_selector_v2_v2/oracle_pilot"
DEFAULT_OUTPUT_DIR = DEFAULT_PILOT_ROOT / "official_metrics"
RANDOM_PREFIX = "random_topk_seed_"
MISMATCH_PREFIX = "oracle_mismatch_seed_"
DETERMINISTIC_CANDIDATES = (
    "uniform_topk",
    "energy_topk",
    "onset_topk",
    "eventness_cached_topk",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-root", type=Path, default=DEFAULT_PILOT_ROOT)
    parser.add_argument("--official-repo", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def finite_tree(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(finite_tree(item) for item in value.values())
    if isinstance(value, list):
        return all(finite_tree(item) for item in value)
    return True


def has_degenerate_repetition(text: str) -> bool:
    """Detect only strong repetition: an adjacent sentence or an 8-token span."""

    normalized = " ".join(text.lower().split())
    sentences = [part.strip(" .,!?:;\t\n") for part in re.split(r"[.!?\n]+", normalized)]
    sentences = [part for part in sentences if len(part.split()) >= 4]
    if any(left == right for left, right in zip(sentences, sentences[1:])):
        return True
    tokens = normalized.split()
    spans = [tuple(tokens[index : index + 8]) for index in range(max(0, len(tokens) - 7))]
    return len(spans) != len(set(spans))


def anomaly_counts(rows: list[dict]) -> dict[str, int]:
    return {
        "empty_output": sum(not str(row.get("prediction", "")).strip() for row in rows),
        "first_token_eos": sum(
            bool(((row.get("generation_debug") or {}).get("tokens") or {}).get("first_token_is_eos"))
            for row in rows
        ),
        "repeated_text": sum(has_degenerate_repetition(str(row.get("prediction", ""))) for row in rows),
        "nonfinite_output": sum(
            not finite_tree(
                {
                    "diagnostics": row.get("as_m4_diagnostics"),
                    "gate": row.get("gate"),
                    "delta_to_video_ratio": row.get("delta_to_video_ratio"),
                }
            )
            for row in rows
        ),
    }


def official_predictions(rows: list[dict]) -> list[dict]:
    return [
        {
            "video_id": str(row["physical_media_id"]),
            "task": "seg_captioning",
            "query_id": 0,
            "answer": str(row["prediction"]),
            "info": {"sentence_id": int(row["sentence_id"])},
        }
        for row in rows
    ]


def validate_prediction_matrix(
    *,
    conditions: list[str],
    rows_by_condition: dict[str, list[dict]],
    expected_count: int,
) -> None:
    if set(rows_by_condition) != set(conditions):
        raise ValueError("prediction 条件集合与 matrix lock 不一致")
    reference_ids: list[str] | None = None
    for condition in conditions:
        rows = rows_by_condition[condition]
        if len(rows) != expected_count:
            raise ValueError(f"{condition} 预测数量 {len(rows)} != {expected_count}")
        ids = [str(row["sample_id"]) for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{condition} 存在重复 sample_id")
        if any(row.get("experiment_id") != condition for row in rows):
            raise ValueError(f"{condition} experiment_id 漂移")
        if reference_ids is None:
            reference_ids = ids
        elif ids != reference_ids:
            raise ValueError(f"{condition} 样本顺序或集合与基准条件不一致")


def evaluate_pilot_gate(metrics: dict[str, dict[str, float]], anomalies: dict[str, dict[str, int]]) -> dict:
    required = {"text_only", "all", "oracle_topk"}
    if not required.issubset(metrics):
        raise ValueError("缺少 pilot 核心条件指标")
    random_names = sorted(name for name in metrics if name.startswith(RANDOM_PREFIX))
    mismatch_names = sorted(name for name in metrics if name.startswith(MISMATCH_PREFIX))
    if len(random_names) != 5 or len(mismatch_names) != 5:
        raise ValueError("Random 或 Oracle-mismatch 必须恰好包含五个固定 seed")

    oracle = metrics["oracle_topk"]["METEOR"]
    random_mean = sum(metrics[name]["METEOR"] for name in random_names) / len(random_names)
    mismatch_mean = sum(metrics[name]["METEOR"] for name in mismatch_names) / len(mismatch_names)
    oracle_anomalies = anomalies["oracle_topk"]
    all_anomalies = anomalies["all"]
    anomaly_delta_vs_all = {
        name: int(oracle_anomalies.get(name, 0)) - int(all_anomalies.get(name, 0))
        for name in sorted(set(oracle_anomalies) | set(all_anomalies))
    }
    anomaly_pass = all(delta <= 0 for delta in anomaly_delta_vs_all.values())
    checks = {
        "oracle_minus_all_ge_0_01": oracle - metrics["all"]["METEOR"] >= 0.01,
        "oracle_minus_random_mean_ge_0_01": oracle - random_mean >= 0.01,
        "oracle_gt_mismatch_mean": oracle > mismatch_mean,
        "oracle_gt_text_only": oracle > metrics["text_only"]["METEOR"],
        "oracle_has_no_output_degradation": anomaly_pass,
    }
    eligible = [
        name
        for name in DETERMINISTIC_CANDIDATES
        if name in metrics and metrics[name]["METEOR"] > random_mean
    ]
    eligible.sort(key=lambda name: (-metrics[name]["METEOR"], DETERMINISTIC_CANDIDATES.index(name)))
    return {
        "status": "pass" if all(checks.values()) else "stop",
        "checks": checks,
        "meteor": {
            "oracle": oracle,
            "all": metrics["all"]["METEOR"],
            "text_only": metrics["text_only"]["METEOR"],
            "random_mean": random_mean,
            "mismatch_mean": mismatch_mean,
            "oracle_minus_all": oracle - metrics["all"]["METEOR"],
            "oracle_minus_random_mean": oracle - random_mean,
            "oracle_minus_mismatch_mean": oracle - mismatch_mean,
            "oracle_minus_text_only": oracle - metrics["text_only"]["METEOR"],
        },
        "eligible_deterministic_strategies": eligible,
        "locked_deterministic_strategy": eligible[0] if eligible else None,
        "oracle_anomaly_delta_vs_all": anomaly_delta_vs_all,
        "next_action": "review_then_unlock_confirm" if all(checks.values()) else "stop_selector_route",
    }


def main() -> None:
    args = parse_args()
    lock = json.loads((args.pilot_root / "matrix_lock.json").read_text(encoding="utf-8"))
    if lock.get("status") != "pass" or lock.get("confirm_allowed") is not False:
        raise ValueError("matrix lock 不是冻结的 pilot-only 状态")
    conditions = [str(item) for item in lock["conditions"]]
    rows_by_condition = {
        condition: read_jsonl(args.pilot_root / "predictions" / f"{condition}.jsonl")
        for condition in conditions
    }
    validate_prediction_matrix(
        conditions=conditions,
        rows_by_condition=rows_by_condition,
        expected_count=int(lock["sample_count"]),
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    official_input_dir = args.output_dir / "official_inputs"
    official_input_dir.mkdir(parents=True, exist_ok=True)
    annotation_path = args.pilot_root / "pilot_annotation.json"
    metrics = {}
    anomalies = {}
    for condition in conditions:
        input_path = official_input_dir / f"{condition}.json"
        input_path.write_text(
            json.dumps(official_predictions(rows_by_condition[condition]), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        metrics[condition] = run_longvale_golden(
            official_repo=args.official_repo,
            annotation_path=annotation_path,
            predictions_path=input_path,
        )
        anomalies[condition] = anomaly_counts(rows_by_condition[condition])

    result = {
        "artifact_kind": "dynamic_selector_v2_longvale_oracle_pilot_gate",
        "sample_count": int(lock["sample_count"]),
        "condition_count": len(conditions),
        "metrics": metrics,
        "anomalies": anomalies,
        "gate": evaluate_pilot_gate(metrics, anomalies),
        "confirm_unlocked": False,
        "learned_selector_training_allowed": False,
    }
    (args.output_dir / "pilot_gate.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
