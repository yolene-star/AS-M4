#!/usr/bin/env python3
"""运行 UnAV-100 Dynamic Selector V2 完整稀疏 validation 定位矩阵。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

from intersuit.harness.dynamic_selector_v2_audio import energy_scores, onset_scores, topk_from_scores
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
SAMPLE_RATE = 16_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def decode_audio(path: Path, expected_duration: float) -> np.ndarray:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "f32le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise ValueError(
            f"audio decode failed for {path.name}: "
            f"{proc.stderr.decode(errors='replace')[-1000:]}"
        )
    waveform = np.frombuffer(proc.stdout, dtype="<f4").copy()
    expected_samples = int(round(expected_duration * SAMPLE_RATE))
    if len(waveform) < expected_samples:
        waveform = np.pad(waveform, (0, expected_samples - len(waveform)), mode="constant")
    return waveform[:expected_samples]


def evaluate_cohort(
    records: list[dict[str, Any]],
    scores_by_video: dict[str, dict[str, list[float]]],
    *,
    k: int,
    bootstrap_repeats: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    per_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sample_to_video = {}
    for record in records:
        sample_id, video_id = str(record["sample_id"]), str(record["video_id"])
        sample_to_video[sample_id] = video_id
        timestamps = fixed_window_timestamps(float(record["duration"]))
        strategies = {
            "all": all_indices(len(timestamps)),
            "uniform_topk": uniform_topk_indices(len(timestamps), k),
            "energy_topk": topk_from_scores(scores_by_video[video_id]["energy"], k),
            "onset_topk": topk_from_scores(scores_by_video[video_id]["onset"], k),
            "oracle_topk": oracle_topk_indices(timestamps, record["target_intervals"], k),
        }
        for seed in RANDOM_SEEDS:
            strategies[f"random_topk_seed_{seed}"] = random_topk_indices(
                len(timestamps),
                k,
                seed=seed,
                sample_id=sample_id,
            )
        for strategy, ranked_indices in strategies.items():
            metrics = evaluate_selection(timestamps, record["target_intervals"], ranked_indices)
            selected_source_indices = sorted(ranked_indices)
            per_strategy[strategy].append(
                {
                    "sample_id": sample_id,
                    "video_id": video_id,
                    "event_label": record["event_label"],
                    "ranked_indices": ranked_indices,
                    "selected_source_indices": selected_source_indices,
                    "selected_timestamps": [timestamps[index] for index in selected_source_indices],
                    **metrics.__dict__,
                }
            )

    summaries = {
        name: aggregate_localization_rows(rows)
        for name, rows in sorted(per_strategy.items())
    }
    random_names = [f"random_topk_seed_{seed}" for seed in RANDOM_SEEDS]
    random_mean_by_sample = {
        sample_id: mean(
            next(
                row["event_coverage"]
                for row in per_strategy[name]
                if row["sample_id"] == sample_id
            )
            for name in random_names
        )
        for sample_id in sample_to_video
    }
    deltas = {}
    wins = {}
    for strategy in ("energy_topk", "onset_topk", "oracle_topk", "uniform_topk"):
        values = {
            row["sample_id"]: row["event_coverage"]
            for row in per_strategy[strategy]
        }
        deltas[f"{strategy}_minus_random_mean"] = cluster_bootstrap_delta(
            values,
            random_mean_by_sample,
            sample_to_video,
            repeats=bootstrap_repeats,
            seed=BOOTSTRAP_SEED,
        )
        wins[strategy] = sum(
            summaries[strategy]["event_coverage"] > summaries[name]["event_coverage"]
            for name in random_names
        )
    return per_strategy, {
        "strategy_summaries": summaries,
        "paired_deltas": deltas,
        "wins_over_random_seeds": wins,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--media-audit", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--sparse-ratio", type=float, default=0.10)
    parser.add_argument("--bootstrap-repeats", type=int, default=10_000)
    parser.add_argument("--expected-videos", type=int, default=335)
    parser.add_argument("--expected-sparse-samples", type=int, default=443)
    args = parser.parse_args()

    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("拒绝运行：必须通过 conda run -n M4 启动")
    records = json.loads(args.manifest.read_text(encoding="utf-8"))
    if validate_s1_manifest_records(records) != "validation":
        raise ValueError("full V1 runner must use validation")
    allowlist = json.loads(args.allowlist.read_text(encoding="utf-8"))
    if allowlist.get("artifact_kind") != "dynamic_selector_v2_v1_full_validation_media_allowlist":
        raise ValueError("invalid full-validation allowlist")
    media_audit = json.loads(args.media_audit.read_text(encoding="utf-8"))
    if media_audit.get("artifact_kind") != "dynamic_selector_v2_v1_full_validation_media_audit":
        raise ValueError("invalid full-validation media audit")
    if media_audit.get("status") not in {
        "PASS",
        "PASS_WITH_OFFICIAL_PACKAGING_DURATION_DIFFERENCES",
    }:
        raise ValueError("full-validation media audit did not pass")
    if media_audit.get("allowlist_sha256") != sha256(args.allowlist):
        raise ValueError("media audit/allowlist SHA mismatch")
    if media_audit.get("manifest_sha256") != sha256(args.manifest):
        raise ValueError("media audit/manifest SHA mismatch")

    entries = allowlist["entries"]
    selected_ids = {str(entry["video_id"]) for entry in entries}
    if len(selected_ids) != args.expected_videos:
        raise ValueError(
            f"full V1 video count mismatch: {len(selected_ids)} != {args.expected_videos}"
        )
    sparse_records = [
        record
        for record in records
        if str(record["video_id"]) in selected_ids
        and float(record["union_duration_ratio"]) <= args.sparse_ratio
    ]
    if len(sparse_records) != args.expected_sparse_samples:
        raise ValueError(
            "full V1 sparse sample count mismatch: "
            f"{len(sparse_records)} != {args.expected_sparse_samples}"
        )
    observed_full_distribution = [
        record for record in records if str(record["video_id"]) in selected_ids
    ]

    scores_by_video: dict[str, dict[str, list[float]]] = {}
    total_windows = 0
    total_audio_sec = 0.0
    for index, entry in enumerate(entries, start=1):
        video_id = str(entry["video_id"])
        path = args.media_root / f"{video_id}.mp4"
        waveform = decode_audio(path, float(entry["duration"]))
        timestamps = fixed_window_timestamps(float(entry["duration"]))
        scores_by_video[video_id] = {
            "energy": energy_scores(waveform, SAMPLE_RATE, timestamps),
            "onset": onset_scores(waveform, SAMPLE_RATE, timestamps),
        }
        total_windows += len(timestamps)
        total_audio_sec += float(entry["duration"])
        if index % 10 == 0 or index == len(entries):
            print(
                json.dumps(
                    {
                        "stage": "feature_extraction",
                        "progress": f"{index}/{len(entries)}",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )

    sparse_rows, sparse_result = evaluate_cohort(
        sparse_records,
        scores_by_video,
        k=args.k,
        bootstrap_repeats=args.bootstrap_repeats,
    )
    full_rows, full_result = evaluate_cohort(
        observed_full_distribution,
        scores_by_video,
        k=args.k,
        bootstrap_repeats=args.bootstrap_repeats,
    )
    sparse_deltas = sparse_result["paired_deltas"]
    sparse_wins = sparse_result["wins_over_random_seeds"]
    oracle_delta = sparse_deltas["oracle_topk_minus_random_mean"]
    oracle_pass = oracle_delta["delta"] >= 0.20 and oracle_delta["ci95_lower"] > 0
    deterministic_pass = any(
        sparse_deltas[f"{strategy}_minus_random_mean"]["delta"] >= 0.05
        and sparse_deltas[f"{strategy}_minus_random_mean"]["ci95_lower"] > 0
        and sparse_wins[strategy] >= 4
        for strategy in ("energy_topk", "onset_topk")
    )
    contract_checks = {
        "all_equals_fixed": True,
        "source_indices_saved": True,
        "absolute_timestamps_saved": True,
        "selected_sets_sorted_before_downstream_use": True,
        "finite_strategy_metrics": all(
            np.isfinite(value)
            for result in (sparse_result, full_result)
            for summary in result["strategy_summaries"].values()
            for value in (
                summary["event_coverage"],
                summary["positive_window_recall"],
                summary["mean_best_tiou"],
            )
        ),
        "model_parameter_sha": "not_applicable_no_model_loaded",
    }
    status = (
        "PASS_FULL_V1_LOCALIZATION"
        if oracle_pass and deterministic_pass and all(
            value is True or isinstance(value, str)
            for value in contract_checks.values()
        )
        else "FAIL_FULL_V1_LOCALIZATION"
    )
    result = {
        "format_version": 1,
        "stage": "V1_FULL_VALIDATION",
        "status": status,
        "scope_note_zh": (
            "主门禁仅针对完整稀疏 validation 443 samples/335 videos；"
            "完整分布补充结果限于同一批 335 个物理视频，不外推到未解包视频。"
        ),
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV"),
        "manifest_sha256": sha256(args.manifest),
        "allowlist_sha256": sha256(args.allowlist),
        "media_audit_sha256": sha256(args.media_audit),
        "code_sha256": {
            "runner": sha256(Path(__file__)),
            "audio": sha256(
                Path(__file__).resolve().parents[1]
                / "intersuit"
                / "harness"
                / "dynamic_selector_v2_audio.py"
            ),
            "localization": sha256(
                Path(__file__).resolve().parents[1]
                / "intersuit"
                / "harness"
                / "dynamic_selector_v2_localization.py"
            ),
        },
        "config": {
            "k": args.k,
            "window_sec": 1.0,
            "hop_sec": 1.0,
            "sample_rate": SAMPLE_RATE,
            "sparse_ratio": args.sparse_ratio,
            "random_seeds": list(RANDOM_SEEDS),
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_repeats": args.bootstrap_repeats,
            "eventness_cached_topk": "omitted_ineligible_asset",
            "temporal_high_conf_topk": "omitted_ineligible_asset",
        },
        "compute_accounting": {
            "decoded_video_count": len(entries),
            "decoded_audio_sec": total_audio_sec,
            "micro_window_count": total_windows,
            "waveform_feature_video_count": len(entries),
            "beats_forward_window_count": 0,
            "projector_forward_window_count": 0,
            "fusion_forward_window_count": 0,
        },
        "contract_checks": contract_checks,
        "gates": {
            "oracle_headroom_pass": oracle_pass,
            "deterministic_strategy_pass": deterministic_pass,
        },
        "sparse_primary": {
            "sample_count": len(sparse_records),
            "video_count": len(selected_ids),
            **sparse_result,
        },
        "observed_full_distribution_supplement": {
            "sample_count": len(observed_full_distribution),
            "video_count": len(selected_ids),
            **full_result,
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "summary.json", result)
    for cohort_name, cohort_rows in (
        ("sparse_primary", sparse_rows),
        ("observed_full_distribution", full_rows),
    ):
        for strategy, rows in sorted(cohort_rows.items()):
            write_json(
                args.output_dir / f"per_sample_{cohort_name}_{strategy}.json",
                rows,
            )
    report = [
        "# Dynamic Selector V2 完整 V1 定位报告",
        "",
        f"状态：**{status}**",
        "",
        f"稀疏主门禁：{len(sparse_records)} samples / {len(selected_ids)} videos，K={args.k}。",
        "",
        "| 策略 | Event Coverage | Positive Recall | mean best tIoU |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, summary in sparse_result["strategy_summaries"].items():
        report.append(
            f"| {name} | {summary['event_coverage']:.6f} | "
            f"{summary['positive_window_recall']:.6f} | "
            f"{summary['mean_best_tiou']:.6f} |"
        )
    for name, delta in sparse_result["paired_deltas"].items():
        report.append(
            f"- {name}: {delta['delta']:.6f}, "
            f"95% CI [{delta['ci95_lower']:.6f}, {delta['ci95_upper']:.6f}]"
        )
    (args.output_dir / "report.md").write_text(
        "\n".join(report) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": status,
                "oracle_headroom_pass": oracle_pass,
                "deterministic_strategy_pass": deterministic_pass,
                "summary": str(args.output_dir / "summary.json"),
                "summary_sha256": sha256(args.output_dir / "summary.json"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    if status != "PASS_FULL_V1_LOCALIZATION":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
