#!/usr/bin/env python3
"""运行 UnAV-100 validation 媒体 pilot 的 Energy/Onset S1 定位实验。"""

from __future__ import annotations

import argparse
import hashlib
import json
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
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_json(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode:
        raise ValueError(proc.stderr.strip()[-1000:])
    return json.loads(proc.stdout)


def probe_media(path: Path, expected_duration: float, tolerance: float) -> dict[str, Any]:
    payload = run_json(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=codec_type,codec_name,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ]
    )
    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration = float((payload.get("format") or {}).get("duration") or 0.0)
    reasons = []
    if video is None:
        reasons.append("missing_video_stream")
    if audio is None:
        reasons.append("missing_audio_stream")
    if abs(duration - expected_duration) > tolerance:
        reasons.append("duration_mismatch")
    return {
        "status": "accepted" if not reasons else "rejected",
        "reasons": reasons,
        "bytes": path.stat().st_size,
        "media_sha256": sha256(path),
        "actual_duration": duration,
        "expected_duration": expected_duration,
        "duration_error": duration - expected_duration,
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_sample_rate": int(audio.get("sample_rate") or 0) if audio else None,
        "audio_channels": int(audio.get("channels") or 0) if audio else None,
    }


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
        raise ValueError(f"audio decode failed for {path.name}: {proc.stderr.decode(errors='replace')[-1000:]}")
    waveform = np.frombuffer(proc.stdout, dtype="<f4").copy()
    expected_samples = int(round(expected_duration * SAMPLE_RATE))
    if len(waveform) < expected_samples:
        waveform = np.pad(waveform, (0, expected_samples - len(waveform)), mode="constant")
    return waveform[:expected_samples]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-videos", type=int, default=32)
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--sparse-ratio", type=float, default=0.10)
    parser.add_argument("--duration-tolerance", type=float, default=0.25)
    parser.add_argument("--bootstrap-repeats", type=int, default=10_000)
    args = parser.parse_args()

    records = json.loads(args.manifest.read_text(encoding="utf-8"))
    if validate_s1_manifest_records(records) != "validation":
        raise ValueError("S1 media pilot must use validation")
    allowlist = json.loads(args.allowlist.read_text(encoding="utf-8"))
    if allowlist.get("artifact_kind") != "dynamic_selector_v2_s1_validation_media_pilot_allowlist":
        raise ValueError("invalid media-pilot allowlist")
    media_audit = []
    selected = []
    for entry in allowlist.get("entries", []):
        path = args.media_root / f"{entry['video_id']}.mp4"
        if not path.is_file() or path.stat().st_size == 0:
            continue
        audit = probe_media(path, float(entry["duration"]), args.duration_tolerance)
        audit.update({"selection_rank": entry["selection_rank"], "video_id": entry["video_id"], "path": str(path.resolve())})
        media_audit.append(audit)
        if audit["status"] == "accepted":
            selected.append((entry, path))
        if len(selected) == args.target_videos:
            break
    if len(selected) != args.target_videos:
        write_json(args.output_dir / "media_audit.json", {"status": "FAIL", "entries": media_audit})
        raise ValueError(f"only {len(selected)} accepted media files for target={args.target_videos}")

    scores_by_video = {}
    for entry, path in selected:
        waveform = decode_audio(path, float(entry["duration"]))
        timestamps = fixed_window_timestamps(float(entry["duration"]))
        scores_by_video[str(entry["video_id"])] = {
            "energy": energy_scores(waveform, SAMPLE_RATE, timestamps),
            "onset": onset_scores(waveform, SAMPLE_RATE, timestamps),
        }
    selected_ids = set(scores_by_video)
    sparse = [
        record
        for record in records
        if record["video_id"] in selected_ids and float(record["union_duration_ratio"]) <= args.sparse_ratio
    ]
    per_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sample_to_video = {}
    for record in sparse:
        sample_id, video_id = str(record["sample_id"]), str(record["video_id"])
        sample_to_video[sample_id] = video_id
        timestamps = fixed_window_timestamps(float(record["duration"]))
        strategies = {
            "all": all_indices(len(timestamps)),
            "uniform_topk": uniform_topk_indices(len(timestamps), args.k),
            "energy_topk": topk_from_scores(scores_by_video[video_id]["energy"], args.k),
            "onset_topk": topk_from_scores(scores_by_video[video_id]["onset"], args.k),
            "oracle_topk": oracle_topk_indices(timestamps, record["target_intervals"], args.k),
        }
        for seed in RANDOM_SEEDS:
            strategies[f"random_topk_seed_{seed}"] = random_topk_indices(
                len(timestamps), args.k, seed=seed, sample_id=sample_id
            )
        for strategy, indices in strategies.items():
            metrics = evaluate_selection(timestamps, record["target_intervals"], indices)
            per_strategy[strategy].append(
                {"sample_id": sample_id, "video_id": video_id, "event_label": record["event_label"], "ranked_indices": indices, **metrics.__dict__}
            )

    summaries = {name: aggregate_localization_rows(rows) for name, rows in sorted(per_strategy.items())}
    random_names = [f"random_topk_seed_{seed}" for seed in RANDOM_SEEDS]
    random_mean_by_sample = {
        sample_id: mean(
            next(row["event_coverage"] for row in per_strategy[name] if row["sample_id"] == sample_id)
            for name in random_names
        )
        for sample_id in sample_to_video
    }
    deltas = {}
    wins = {}
    for strategy in ("energy_topk", "onset_topk", "oracle_topk", "uniform_topk"):
        values = {row["sample_id"]: row["event_coverage"] for row in per_strategy[strategy]}
        deltas[f"{strategy}_minus_random_mean"] = cluster_bootstrap_delta(
            values, random_mean_by_sample, sample_to_video, repeats=args.bootstrap_repeats, seed=BOOTSTRAP_SEED
        )
        wins[strategy] = sum(
            summaries[strategy]["event_coverage"] > summaries[name]["event_coverage"] for name in random_names
        )
    deterministic_pass = any(
        deltas[f"{strategy}_minus_random_mean"]["delta"] >= 0.05
        and deltas[f"{strategy}_minus_random_mean"]["ci95_lower"] > 0
        and wins[strategy] >= 4
        for strategy in ("energy_topk", "onset_topk")
    )
    result = {
        "format_version": 1,
        "stage": "S1_MEDIA_PILOT",
        "status": "PASS_MEDIA_QUALIFICATION",
        "deterministic_signal_status": "PASS_PILOT_SIGNAL" if deterministic_pass else "FAIL_PILOT_SIGNAL",
        "scope_note_zh": "仅 32 个 validation 物理视频的媒体资格与方向性 Energy/Onset pilot；不含 Eventness，不能替代完整 V1。",
        "manifest_sha256": sha256(args.manifest),
        "allowlist_sha256": sha256(args.allowlist),
        "code_sha256": {
            "runner": sha256(Path(__file__)),
            "audio": sha256(
                Path(__file__).resolve().parents[1] / "intersuit" / "harness" / "dynamic_selector_v2_audio.py"
            ),
            "localization": sha256(
                Path(__file__).resolve().parents[1] / "intersuit" / "harness" / "dynamic_selector_v2_localization.py"
            ),
        },
        "config": {
            "target_videos": args.target_videos,
            "eligible_samples": len(sparse),
            "k": args.k,
            "window_sec": 1.0,
            "hop_sec": 1.0,
            "sample_rate": SAMPLE_RATE,
            "energy": "log mean-square waveform energy",
            "onset": "mean positive log-spectral flux; frame=400; hop=160",
            "random_seeds": list(RANDOM_SEEDS),
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_repeats": args.bootstrap_repeats,
        },
        "strategy_summaries": summaries,
        "paired_deltas": deltas,
        "wins_over_random_seeds": wins,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        args.output_dir / "media_audit.json",
        {
            "status": "PASS",
            "selected_accepted_count": len(selected),
            "audited_count": len(media_audit),
            "rejected_count": sum(row["status"] != "accepted" for row in media_audit),
            "entries": media_audit,
        },
    )
    write_json(args.output_dir / "summary.json", result)
    for strategy, rows in sorted(per_strategy.items()):
        write_json(args.output_dir / f"per_sample_{strategy}.json", rows)
    report = [
        "# Dynamic Selector V2 S1 媒体 pilot 报告",
        "",
        f"媒体资格：**{result['status']}**",
        f"确定性方向信号：**{result['deterministic_signal_status']}**",
        "",
        "该结果只覆盖 32 个 validation 物理视频，不构成完整 V1 晋级。",
        "",
        "| 策略 | Event Coverage | Positive Recall | mean best tIoU |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, summary in summaries.items():
        report.append(f"| {name} | {summary['event_coverage']:.6f} | {summary['positive_window_recall']:.6f} | {summary['mean_best_tiou']:.6f} |")
    for name, delta in deltas.items():
        report.append(f"- {name}: {delta['delta']:.6f}, 95% CI [{delta['ci95_lower']:.6f}, {delta['ci95_upper']:.6f}]")
    (args.output_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "deterministic_signal_status": result["deterministic_signal_status"], "summary": str(args.output_dir / 'summary.json')}, ensure_ascii=False))


if __name__ == "__main__":
    main()
