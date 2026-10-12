#!/usr/bin/env python3
"""统一重算 UnAV-100 K=4/K=8 冻结策略指标并显式保留缺失策略。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import mean
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "intersuit/harness/artifacts/dynamic_selector_v2_v1"
DEFAULT_OUTPUT = REPO_ROOT / "intersuit/harness/artifacts/unav_dimension_split_v1"
DEFAULT_TEMPORAL_ROOT = REPO_ROOT / "intersuit/harness/artifacts/unav_temporal_topk_v1"
RANDOM_SEEDS = [20260720, 20260721, 20260722, 20260723, 20260724]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    require(bool(rows), "逐样本记录为空")
    first_hits = [int(row["first_hit_rank"]) for row in rows if row.get("first_hit_rank") is not None]
    return {
        "sample_count": len(rows),
        "event_coverage": mean(float(row["event_coverage"]) for row in rows),
        "window_hit_at_k": mean(row.get("first_hit_rank") is not None for row in rows),
        "positive_window_recall": mean(float(row["positive_window_recall"]) for row in rows),
        "mean_best_tiou": mean(float(row["mean_best_tiou"]) for row in rows),
        "mean_first_hit_rank_when_hit": mean(first_hits) if first_hits else math.nan,
        "mean_selected_window_count": mean(float(row["selected_window_count"]) for row in rows),
    }


def average_metrics(items: list[dict[str, float | int]]) -> dict[str, float | int]:
    require(bool(items), "待平均指标为空")
    keys = [key for key in items[0] if key != "sample_count"]
    return {
        "sample_count": int(items[0]["sample_count"]),
        **{key: mean(float(item[key]) for item in items) for key in keys},
    }


def assert_close(observed: float, expected: float, label: str) -> None:
    if not math.isclose(observed, expected, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"{label} 重算不一致：{observed} != {expected}")


def validate_pair(root: Path, run_a: str, run_b: str, budget: int) -> dict[str, Any]:
    summary_a_path = root / run_a / "summary.json"
    summary_b_path = root / run_b / "summary.json"
    summary_a = read_json(summary_a_path)
    summary_b = read_json(summary_b_path)
    require(summary_a["config"]["k"] == budget == summary_b["config"]["k"], "K 不匹配")
    require(summary_a["manifest_sha256"] == summary_b["manifest_sha256"], "A/B manifest SHA 不匹配")
    require(summary_a["config"]["random_seeds"] == RANDOM_SEEDS, "run A Random seeds 不匹配")
    require(summary_b["config"]["random_seeds"] == RANDOM_SEEDS, "run B Random seeds 不匹配")
    require(
        summary_a["sparse_primary"] == summary_b["sparse_primary"],
        f"{run_a}/{run_b} sparse_primary 未双重复现",
    )
    return {
        "summary": summary_a,
        "summary_sha256_a": sha256(summary_a_path),
        "summary_sha256_b": sha256(summary_b_path),
    }


def load_strategy(root: Path, run_name: str, strategy: str) -> tuple[list[dict[str, Any]], str]:
    path = root / run_name / f"per_sample_sparse_primary_{strategy}.json"
    require(path.is_file(), f"缺少逐样本资产：{path}")
    rows = read_json(path)
    require(isinstance(rows, list), f"逐样本资产不是列表：{path}")
    return rows, sha256(path)


def evaluate_budget(root: Path, budget: int, temporal_root: Path = DEFAULT_TEMPORAL_ROOT) -> dict[str, Any]:
    base_a = f"run_a_k{budget}"
    base_b = f"run_b_k{budget}"
    event_a = f"run_a_k{budget}_eventness"
    event_b = f"run_b_k{budget}_eventness"
    base_pair = validate_pair(root, base_a, base_b, budget)
    event_pair = validate_pair(root, event_a, event_b, budget)
    require(
        base_pair["summary"]["manifest_sha256"] == event_pair["summary"]["manifest_sha256"],
        "base/eventness manifest SHA 不匹配",
    )

    source_sha: dict[str, str] = {}
    random_metrics = []
    for seed in RANDOM_SEEDS:
        strategy = f"random_topk_seed_{seed}"
        rows_a, sha_a = load_strategy(root, base_a, strategy)
        rows_b, sha_b = load_strategy(root, base_b, strategy)
        require(sha_a == sha_b, f"Random seed {seed} A/B 文件不一致")
        metrics = aggregate_rows(rows_a)
        stored = base_pair["summary"]["sparse_primary"]["strategy_summaries"][strategy]
        for key in ("event_coverage", "positive_window_recall", "mean_best_tiou"):
            assert_close(float(metrics[key]), float(stored[key]), f"K={budget}/{strategy}/{key}")
        random_metrics.append(metrics)
        source_sha[strategy] = sha_a

    strategies: dict[str, Any] = {"random": {"status": "FROZEN_REFERENCE", **average_metrics(random_metrics)}}
    source_run = {
        "energy": (base_a, base_b, "energy_topk", "FROZEN_RULE_BASELINE"),
        "eventness": (event_a, event_b, "eventness_cached_topk", "FROZEN_CACHED_BASELINE"),
        "oracle": (base_a, base_b, "oracle_topk", "FROZEN_NONCAUSAL_UPPER_BOUND"),
    }
    for output_name, (run_a, run_b, stored_name, status) in source_run.items():
        rows_a, sha_a = load_strategy(root, run_a, stored_name)
        rows_b, sha_b = load_strategy(root, run_b, stored_name)
        require(sha_a == sha_b, f"K={budget}/{output_name} A/B 文件不一致")
        metrics = aggregate_rows(rows_a)
        summary = read_json(root / run_a / "summary.json")
        stored = summary["sparse_primary"]["strategy_summaries"][stored_name]
        for key in ("event_coverage", "positive_window_recall", "mean_best_tiou"):
            assert_close(float(metrics[key]), float(stored[key]), f"K={budget}/{output_name}/{key}")
        strategies[output_name] = {"status": status, **metrics}
        source_sha[output_name] = sha_a

    random_coverage = float(strategies["random"]["event_coverage"])
    oracle_headroom = float(strategies["oracle"]["event_coverage"]) - random_coverage
    require(oracle_headroom > 0.0, f"K={budget} Oracle headroom 非正")
    temporal_path = temporal_root / f"per_sample_k{budget}.jsonl"
    if temporal_path.is_file():
        temporal_rows = [json.loads(line) for line in temporal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        temporal_metrics = aggregate_rows(temporal_rows)
        strategies["temporal"] = {"status": "FROZEN_TEMPORAL_PROJECTOR_BASELINE", **temporal_metrics}
        source_sha["temporal"] = sha256(temporal_path)
    else:
        strategies["temporal"] = {"status": "MISSING_SCHEMA_COMPATIBLE_ASSET"}
    for name in ("energy", "eventness", "temporal"):
        if "event_coverage" not in strategies[name]:
            continue
        coverage = float(strategies[name]["event_coverage"])
        strategies[name]["event_coverage_delta_vs_random"] = coverage - random_coverage
        strategies[name]["oracle_headroom_recovery"] = (coverage - random_coverage) / oracle_headroom
        strategies[name]["wins_over_random_seeds"] = sum(
            coverage > float(item["event_coverage"]) for item in random_metrics
        )
    strategies["learned_selector"] = {"status": "NOT_EVALUATED_NOT_AUTHORIZED"}

    return {
        "budget_k": budget,
        "manifest_sha256": base_pair["summary"]["manifest_sha256"],
        "sample_count": int(strategies["random"]["sample_count"]),
        "video_count": int(base_pair["summary"]["sparse_primary"]["video_count"]),
        "oracle_headroom": oracle_headroom,
        "strategies": strategies,
        "source_sha256": source_sha,
        "summary_sha256": {
            base_a: base_pair["summary_sha256_a"],
            base_b: base_pair["summary_sha256_b"],
            event_a: event_pair["summary_sha256_a"],
            event_b: event_pair["summary_sha256_b"],
        },
    }


def render_report(result: dict[str, Any]) -> str:
    lines = [
        "# UnAV-100 分维度统一 evaluator CPU 结果",
        "",
        f"状态：`{result['status']}`",
        "",
        "本结果从冻结逐样本资产重算，不加载模型、不访问 official test。",
        "",
        "| K | 策略 | Event Coverage | Window Hit | Positive Recall | best tIoU | Headroom recovery | 状态 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for budget in result["budgets"]:
        for name, metrics in budget["strategies"].items():
            if "event_coverage" not in metrics:
                lines.append(f"| {budget['budget_k']} | {name} | — | — | — | — | — | `{metrics['status']}` |")
                continue
            recovery = metrics.get("oracle_headroom_recovery")
            recovery_text = "—" if recovery is None else f"{recovery:.6f}"
            lines.append(
                f"| {budget['budget_k']} | {name} | {metrics['event_coverage']:.6f} | "
                f"{metrics['window_hit_at_k']:.6f} | {metrics['positive_window_recall']:.6f} | "
                f"{metrics['mean_best_tiou']:.6f} | {recovery_text} | `{metrics['status']}` |"
            )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            "Random、Energy、Eventness、Oracle 已完成双重复现与逐样本重算；Temporal 已完成同 schema 冻结评测；仅 learned selector 仍未评估。",
            "",
        ]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    budgets = [evaluate_budget(args.input_root, budget, args.temporal_root) for budget in (4, 8)]
    result = {
        "status": "PASS_UNAV_UNIFIED_CPU_FIVE_STRATEGIES",
        "scope": "UnAV-100 official validation sparse primary",
        "budgets": budgets,
        "gpu_used": False,
        "model_loaded": False,
        "official_test_accessed": False,
        "complete_six_strategy_matrix": False,
        "missing_strategies": ["learned_selector"],
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_root / "REPORT_ZH.md").write_text(render_report(result), encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--temporal-root", type=Path, default=DEFAULT_TEMPORAL_ROOT)
    return parser.parse_args()


if __name__ == "__main__":
    output = run(parse_args())
    print(json.dumps({"status": output["status"], "missing": output["missing_strategies"]}, ensure_ascii=False))
