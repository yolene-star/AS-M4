#!/usr/bin/env python3
"""使用冻结官方 LongVALE 指标审查 Dynamic Selector V3 P3 canary。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from intersuit.harness.dynamic_selector_v2_metrics import run_longvale_golden
from intersuit.harness.dynamic_selector_v3_evaluation import (
    anomaly_counts,
    official_predictions,
    operational_checks,
    read_jsonl,
    summarize_metrics,
    validate_matrix,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = PROJECT_ROOT / "intersuit/harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/p3_longvale_canary20"
DEFAULT_OFFICIAL_REPO = PROJECT_ROOT / "intersuit/datasets/LongVALE_V2/official_repo"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--official-repo", type=Path, default=DEFAULT_OFFICIAL_REPO)
    return parser.parse_args()


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
