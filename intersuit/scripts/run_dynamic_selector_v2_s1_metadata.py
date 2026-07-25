#!/usr/bin/env python3
"""Run the media-free S1 All/Uniform/Random/Oracle localization pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from intersuit.harness.dynamic_selector_v2_localization import (
    aggregate_localization_rows,
    all_indices,
    cluster_bootstrap_delta,
    evaluate_selection,
    fixed_window_timestamps,
    oracle_topk_indices,
    random_topk_indices,
    uniform_topk_indices,
    validate_s1_manifest_records,
)


RANDOM_SEEDS = (20260720, 20260721, 20260722, 20260723, 20260724)
BOOTSTRAP_SEED = 20260724


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--sparse-ratio", type=float, default=0.10)
    parser.add_argument("--bootstrap-repeats", type=int, default=10_000)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.k <= 0:
        raise ValueError("k must be positive")
    if not 0 < args.sparse_ratio <= 1:
        raise ValueError("sparse-ratio must be in (0,1]")
    records = json.loads(args.manifest.read_text(encoding="utf-8"))
    split = validate_s1_manifest_records(records)
    sparse = [record for record in records if float(record["union_duration_ratio"]) <= args.sparse_ratio]
    if not sparse:
        raise ValueError("manifest contains no eligible sparse samples")

    per_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sample_to_video = {}
    for record in sparse:
        sample_id = str(record["sample_id"])
        video_id = str(record["video_id"])
        sample_to_video[sample_id] = video_id
        timestamps = fixed_window_timestamps(float(record["duration"]))
        strategies = {
            "all": all_indices(len(timestamps)),
            "uniform_topk": uniform_topk_indices(len(timestamps), args.k),
            "oracle_topk": oracle_topk_indices(timestamps, record["target_intervals"], args.k),
        }
        for seed in RANDOM_SEEDS:
            strategies[f"random_topk_seed_{seed}"] = random_topk_indices(
                len(timestamps), args.k, seed=seed, sample_id=sample_id
            )
        for strategy, ranked_indices in strategies.items():
            metrics = evaluate_selection(timestamps, record["target_intervals"], ranked_indices)
            per_strategy[strategy].append(
                {
                    "sample_id": sample_id,
                    "video_id": video_id,
                    "event_label": record["event_label"],
                    "ranked_indices": ranked_indices,
                    **metrics.__dict__,
                }
            )

    summaries = {strategy: aggregate_localization_rows(rows) for strategy, rows in sorted(per_strategy.items())}
    random_names = [f"random_topk_seed_{seed}" for seed in RANDOM_SEEDS]
    random_mean_by_sample = {
        sample_id: mean(
            next(row["event_coverage"] for row in per_strategy[name] if row["sample_id"] == sample_id)
            for name in random_names
        )
        for sample_id in sample_to_video
    }
    oracle_by_sample = {
        row["sample_id"]: row["event_coverage"] for row in per_strategy["oracle_topk"]
    }
    uniform_by_sample = {
        row["sample_id"]: row["event_coverage"] for row in per_strategy["uniform_topk"]
    }
    oracle_delta = cluster_bootstrap_delta(
        oracle_by_sample,
        random_mean_by_sample,
        sample_to_video,
        repeats=args.bootstrap_repeats,
        seed=BOOTSTRAP_SEED,
    )
    uniform_delta = cluster_bootstrap_delta(
        uniform_by_sample,
        random_mean_by_sample,
        sample_to_video,
        repeats=args.bootstrap_repeats,
        seed=BOOTSTRAP_SEED,
    )
    random_event_coverage_mean = mean(summaries[name]["event_coverage"] for name in random_names)
    oracle_headroom = summaries["oracle_topk"]["event_coverage"] - random_event_coverage_mean
    uniform_recovery = (
        (summaries["uniform_topk"]["event_coverage"] - random_event_coverage_mean) / oracle_headroom
        if oracle_headroom > 0
        else None
    )
    checks = {
        "oracle_minus_random_ge_20pp": oracle_delta["delta"] >= 0.20,
        "oracle_minus_random_ci_lower_gt_zero": oracle_delta["ci95_lower"] > 0,
        "all_event_coverage_exact_one": abs(summaries["all"]["event_coverage"] - 1.0) <= 1e-12,
        "all_positive_window_recall_exact_one": abs(summaries["all"]["positive_window_recall"] - 1.0) <= 1e-12,
    }
    result = {
        "format_version": 1,
        "stage": "S1_METADATA_ONLY",
        "status": "PASS_ORACLE_HEADROOM" if all(checks.values()) else "FAIL_ORACLE_HEADROOM",
        "scope_note_zh": "仅验证 All/Uniform/Random/Oracle；不含 Energy/Eventness/Temporal，不构成完整 V1 通过。",
        "manifest": str(args.manifest.resolve()),
        "manifest_sha256": sha256(args.manifest),
        "split": split,
        "code_sha256": {
            "runner": sha256(Path(__file__)),
            "localization": sha256(
                Path(__file__).resolve().parents[1]
                / "intersuit"
                / "harness"
                / "dynamic_selector_v2_localization.py"
            ),
            "data_contract": sha256(
                Path(__file__).resolve().parents[1]
                / "intersuit"
                / "harness"
                / "dynamic_selector_v2_data.py"
            ),
        },
        "config": {
            "k": args.k,
            "window_sec": 1.0,
            "hop_sec": 1.0,
            "pad_short": True,
            "sparse_ratio": args.sparse_ratio,
            "random_seeds": list(RANDOM_SEEDS),
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_repeats": args.bootstrap_repeats,
        },
        "eligible_sample_count": len(sparse),
        "eligible_video_count": len(set(sample_to_video.values())),
        "strategy_summaries": summaries,
        "paired_deltas": {
            "oracle_minus_random_mean": oracle_delta,
            "uniform_minus_random_mean": uniform_delta,
        },
        "oracle_headroom": oracle_headroom,
        "uniform_oracle_headroom_recovery": uniform_recovery,
        "checks": checks,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for strategy, rows in sorted(per_strategy.items()):
        write_json(args.output_dir / f"per_sample_{strategy}.json", rows)
    write_json(args.output_dir / "summary.json", result)
    report = [
        "# Dynamic Selector V2 S1 metadata-only 报告",
        "",
        f"状态：**{result['status']}**",
        "",
        "本报告不读取媒体或模型，只验证 All/Uniform/Random/Oracle 定位上界。",
        "Energy/Eventness/Temporal 尚未运行，因此不能判定完整 V1 通过。",
        "",
        "| 策略 | Event Coverage | Positive Recall | mean best tIoU | retention |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for strategy, summary in summaries.items():
        report.append(
            f"| {strategy} | {summary['event_coverage']:.6f} | "
            f"{summary['positive_window_recall']:.6f} | {summary['mean_best_tiou']:.6f} | "
            f"{summary['mean_retention_rate']:.6f} |"
        )
    report.extend(
        [
            "",
            f"- Oracle−Random：{oracle_delta['delta']:.6f} "
            f"(95% CI {oracle_delta['ci95_lower']:.6f}, {oracle_delta['ci95_upper']:.6f})",
            f"- Uniform−Random：{uniform_delta['delta']:.6f} "
            f"(95% CI {uniform_delta['ci95_lower']:.6f}, {uniform_delta['ci95_upper']:.6f})",
            f"- Uniform Oracle headroom recovery：{uniform_recovery}",
        ]
    )
    (args.output_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "summary": str(args.output_dir / "summary.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
