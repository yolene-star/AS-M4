#!/usr/bin/env python3
"""执行 Dynamic Selector V3 P3 frozen-100 的正式统计门禁。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from intersuit.harness.dynamic_selector_v2_metrics import (
    OfficialMeteorCorpusScorer,
    run_longvale_golden,
    run_longvale_meteor_per_sample,
)
from intersuit.harness.dynamic_selector_v3_evaluation import (
    MISMATCH_PREFIX,
    RANDOM_PREFIX,
    anomaly_counts,
    official_predictions,
    operational_checks,
    read_jsonl,
    summarize_metrics,
    validate_matrix,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = (
    PROJECT_ROOT
    / "intersuit/harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/p3_longvale_frozen100"
)
DEFAULT_OFFICIAL_REPO = PROJECT_ROOT / "intersuit/datasets/LongVALE_V2/official_repo"
BOOTSTRAP_REPEATS = 10_000
MIN_PRIMARY_DELTA = 0.01


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--official-repo", type=Path, default=DEFAULT_OFFICIAL_REPO)
    return parser.parse_args()


def statistics_matrix(payload: Mapping[str, Any], expected_video_ids: list[str]) -> np.ndarray:
    rows = list(payload["per_sample"])
    if any(int(row["sentence_id"]) != 0 for row in rows):
        raise ValueError("frozen-100 METEOR 必须使用局部 sentence_id=0")
    video_ids = [str(row["video_id"]) for row in rows]
    if len(set(video_ids)) != len(rows):
        raise ValueError("METEOR sufficient statistics physical video 重复")
    if video_ids != expected_video_ids:
        raise ValueError("METEOR per-sample 视频顺序/集合漂移")
    matrix = np.asarray([row["sufficient_statistics"] for row in rows], dtype=np.float64)
    if matrix.shape != (len(rows), 23) or not np.all(np.isfinite(matrix)):
        raise ValueError("METEOR sufficient statistics 必须是有限 N×23 矩阵")
    return matrix


def paired_bootstrap_comparisons(
    statistics: dict[str, np.ndarray],
    official_aggregates: Mapping[str, float],
    *,
    repeats: int,
    seed: int,
    scorer: OfficialMeteorCorpusScorer,
) -> dict[str, dict[str, float]]:
    if repeats <= 0:
        raise ValueError("bootstrap repeats 必须为正数")
    if set(statistics) != set(official_aggregates):
        raise ValueError("METEOR statistics 与 official aggregates 条件集合漂移")
    random_names = sorted(name for name in statistics if name.startswith(RANDOM_PREFIX))
    mismatch_names = sorted(name for name in statistics if name.startswith(MISMATCH_PREFIX))
    if len(random_names) != 5 or len(mismatch_names) != 5:
        raise ValueError("Random/mismatch statistics 必须各有五个 seed")
    shapes = {values.shape for values in statistics.values()}
    if len(shapes) != 1:
        raise ValueError("各条件 METEOR statistics shape 漂移")
    sample_count, width = next(iter(shapes))
    if sample_count == 0 or width != 23:
        raise ValueError("METEOR statistics 必须是非空 N×23")

    observed_controls = {
        "fixed_all": float(official_aggregates["fixed_all"]),
        "random_mean": float(np.mean([official_aggregates[name] for name in random_names])),
        "current_micro_top8": float(official_aggregates["current_micro_top8"]),
        "mismatch_mean": float(np.mean([official_aggregates[name] for name in mismatch_names])),
        "behavior_off": float(official_aggregates["behavior_off"]),
    }
    observed_oracle = float(official_aggregates["oracle_top8"])
    deltas = {name: np.empty(repeats, dtype=np.float64) for name in observed_controls}
    rng = np.random.default_rng(seed)
    for repeat in range(repeats):
        indices = rng.integers(0, sample_count, size=sample_count)
        replicate_scores = {
            name: scorer.score_corpus(values[indices]) for name, values in statistics.items()
        }
        oracle_score = replicate_scores["oracle_top8"]
        controls = {
            "fixed_all": replicate_scores["fixed_all"],
            "random_mean": float(np.mean([replicate_scores[name] for name in random_names])),
            "current_micro_top8": replicate_scores["current_micro_top8"],
            "mismatch_mean": float(np.mean([replicate_scores[name] for name in mismatch_names])),
            "behavior_off": replicate_scores["behavior_off"],
        }
        for name, control in controls.items():
            deltas[name][repeat] = oracle_score - control
    return {
        f"oracle_minus_{name}": {
            "delta": observed_oracle - control,
            "ci95_lower": float(np.quantile(deltas[name], 0.025)),
            "ci95_upper": float(np.quantile(deltas[name], 0.975)),
            "bootstrap_repeats": repeats,
            "bootstrap_seed": seed,
            "scoring_protocol": "official_meteor_corpus_eval_from_resampled_sufficient_statistics",
        }
        for name, control in observed_controls.items()
    }


def anomaly_gate(anomalies: dict[str, dict[str, int]]) -> dict[str, bool]:
    oracle = anomalies["oracle_top8"]
    fixed_all = anomalies["fixed_all"]
    return {
        f"oracle_{name}_not_higher_than_all": int(oracle[name]) <= int(fixed_all[name])
        for name in (
            "empty_output",
            "first_token_eos",
            "max_length_without_eos",
            "repeated_text",
            "nonfinite_output",
        )
    }


def oracle_gate(
    comparisons: dict[str, dict[str, float]],
    anomaly_checks: dict[str, bool],
    operational: dict[str, bool],
) -> dict[str, bool]:
    checks = {
        "oracle_minus_all_at_least_0_01": comparisons["oracle_minus_fixed_all"]["delta"]
        >= MIN_PRIMARY_DELTA,
        "oracle_minus_all_ci95_lower_positive": comparisons["oracle_minus_fixed_all"]["ci95_lower"] > 0,
        "oracle_minus_random_mean_at_least_0_01": comparisons["oracle_minus_random_mean"]["delta"]
        >= MIN_PRIMARY_DELTA,
        "oracle_minus_random_mean_ci95_lower_positive": comparisons["oracle_minus_random_mean"][
            "ci95_lower"
        ]
        > 0,
        "oracle_minus_current_at_least_0_01": comparisons["oracle_minus_current_micro_top8"]["delta"]
        >= MIN_PRIMARY_DELTA,
        "oracle_minus_current_ci95_lower_positive": comparisons["oracle_minus_current_micro_top8"][
            "ci95_lower"
        ]
        > 0,
        "oracle_above_mismatch_mean": comparisons["oracle_minus_mismatch_mean"]["delta"] > 0,
        "oracle_above_behavior_off": comparisons["oracle_minus_behavior_off"]["delta"] > 0,
    }
    checks.update({f"operational_{name}": value for name, value in operational.items()})
    checks.update(anomaly_checks)
    return checks


def render_report(result: dict[str, Any]) -> str:
    comparisons = result["paired_cluster_bootstrap"]
    lines = [
        "# Dynamic Selector V3 P3 frozen-100 最终审查",
        "",
        f"结论：`{result['status']}`。",
        "",
        "## 主指标与成对物理视频 bootstrap",
        "",
    ]
    for name in (
        "oracle_minus_fixed_all",
        "oracle_minus_random_mean",
        "oracle_minus_current_micro_top8",
        "oracle_minus_mismatch_mean",
        "oracle_minus_behavior_off",
    ):
        item = comparisons[name]
        lines.append(
            f"- `{name}`：METEOR {item['delta']:+.6f}，95% CI "
            f"[{item['ci95_lower']:+.6f}, {item['ci95_upper']:+.6f}]"
        )
    lines.extend([
        "",
        "## 门禁",
        "",
        *[f"- `{name}`：{str(value).lower()}" for name, value in result["oracle_gate_checks"].items()],
        "",
        "## 权限结论",
        "",
        f"- deterministic diagnostics allowed：`{str(result['deterministic_stage_allowed']).lower()}`",
        f"- selector training allowed：`{str(result['selector_training_allowed']).lower()}`",
        f"- confirm accessed：`{str(result['confirm_accessed']).lower()}`",
        "",
        "人话解释：只有 Oracle 在相同 8 窗口预算下，稳定且明显胜过 All、Random、当前 Micro Top-8、错配和无音频，才说明“选对窗口”本身值得继续投入。任一主门禁失败都按预注册计划停止 selector 路线，不能靠事后扩样或放宽阈值翻案。",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    lock = json.loads((args.run_root / "p3_full_lock.json").read_text(encoding="utf-8"))
    if lock.get("scope") != "frozen100" or int(lock.get("sample_count", 0)) != 100:
        raise ValueError("P3 full lock scope/sample_count 漂移")
    if lock.get("full_execution_authorized_by_canary") is not True:
        raise ValueError("P3 canary 未授权 frozen-100")
    if lock.get("final_oracle_gate_pending") is not True:
        raise ValueError("P3 final Oracle gate 初始状态漂移")
    if any(lock.get(name) is not False for name in ("p3_full_allowed", "selector_training_allowed", "confirm_accessed")):
        raise ValueError("P3 full 初始权限锁漂移")
    conditions = [str(value) for value in lock["conditions"]]
    annotation = json.loads((args.run_root / "pilot_annotation.json").read_text(encoding="utf-8"))
    annotation_video_ids = list(annotation)
    rows_by_condition = {
        condition: read_jsonl(args.run_root / "predictions" / f"{condition}.jsonl")
        for condition in conditions
    }
    validate_matrix(conditions, rows_by_condition, int(lock["sample_count"]))
    operational = operational_checks(rows_by_condition)

    output_dir = args.run_root / "official_metrics"
    input_dir = output_dir / "official_inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    metrics = {}
    anomalies = {}
    statistics = {}
    per_sample_payloads = {}
    aggregate_consistency = {}
    for condition in conditions:
        input_path = input_dir / f"{condition}.json"
        input_path.write_text(
            json.dumps(
                official_predictions(rows_by_condition[condition], annotation_video_ids),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        metrics[condition] = run_longvale_golden(
            official_repo=args.official_repo,
            annotation_path=args.run_root / "pilot_annotation.json",
            predictions_path=input_path,
        )
        per_sample_payloads[condition] = run_longvale_meteor_per_sample(
            official_repo=args.official_repo,
            annotation_path=args.run_root / "pilot_annotation.json",
            predictions_path=input_path,
        )
        aggregate_consistency[condition] = bool(np.isclose(
            metrics[condition]["METEOR"],
            per_sample_payloads[condition]["METEOR"],
            rtol=0.0,
            atol=1e-12,
        ))
        expected_video_ids = annotation_video_ids
        statistics[condition] = statistics_matrix(per_sample_payloads[condition], expected_video_ids)
        anomalies[condition] = anomaly_counts(rows_by_condition[condition], int(lock["max_new_tokens"]))
    if not all(aggregate_consistency.values()):
        raise ValueError("official aggregate 与 per-sample METEOR scorer 不一致")

    comparisons = paired_bootstrap_comparisons(
        statistics,
        {name: values["METEOR"] for name, values in metrics.items()},
        repeats=BOOTSTRAP_REPEATS,
        seed=int(lock["bootstrap_seed"]),
        scorer=OfficialMeteorCorpusScorer(official_repo=args.official_repo),
    )
    anomaly_checks = anomaly_gate(anomalies)
    gate_checks = oracle_gate(comparisons, anomaly_checks, operational)
    gate_pass = all(gate_checks.values())
    status = "pass_to_deterministic_diagnostics" if gate_pass else "stop_selector_route"
    result = {
        "artifact_kind": "m4_ptpa_dynamic_selector_v3_p3_frozen100_review",
        "status": status,
        "sample_count": int(lock["sample_count"]),
        "condition_count": len(conditions),
        "metrics": metrics,
        "meteor_summary": summarize_metrics(metrics),
        "paired_cluster_bootstrap": comparisons,
        "bootstrap_repeats": BOOTSTRAP_REPEATS,
        "bootstrap_seed": int(lock["bootstrap_seed"]),
        "official_aggregate_statistics_adapter_consistency": aggregate_consistency,
        "bootstrap_scoring_protocol": "official_meteor_corpus_eval_from_resampled_sufficient_statistics",
        "anomalies": anomalies,
        "operational_checks": operational,
        "oracle_gate_checks": gate_checks,
        "final_oracle_gate_passed": gate_pass,
        "final_oracle_gate_pending": False,
        "oracle_decision": "advance_to_deterministic_diagnostics" if gate_pass else "stop_selector_route",
        "selector_route_stopped": not gate_pass,
        "deterministic_stage_allowed": gate_pass,
        "selector_training_allowed": False,
        "confirm_accessed": False,
    }
    (output_dir / "p3_full_meteor_per_sample.json").write_text(
        json.dumps(per_sample_payloads, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "p3_full_review.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.run_root / "P3_FULL_REVIEW_ZH.md").write_text(render_report(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not all(operational.values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
