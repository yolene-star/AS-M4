"""Metadata-only localization baselines for Dynamic Window Selector V2.

These helpers intentionally do not read media, features, questions, or model
weights.  They establish the fixed-window All/Uniform/Random/Oracle upper-bound
contract before Energy/Eventness/Temporal strategies are allowed to run.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean
from typing import Any, Mapping, Sequence

import numpy as np

from .dynamic_selector_v2_data import merge_intervals, window_target_mask


@dataclass(frozen=True)
class SampleLocalizationMetrics:
    event_coverage: float
    positive_window_recall: float
    mean_best_tiou: float
    first_hit_rank: int | None
    selected_window_count: int
    valid_window_count: int


def validate_s1_manifest_records(records: Any) -> str:
    """Validate the metadata-only S1 input and return its single split.

    S1 is deliberately limited to train/validation.  Refusing test here makes
    the plan's held-out gate executable rather than relying on operator memory.
    """

    if not isinstance(records, list) or not records:
        raise ValueError("manifest must be a non-empty JSON list")
    required = {
        "sample_id",
        "split",
        "video_id",
        "duration",
        "event_label",
        "target_intervals",
        "union_duration_ratio",
    }
    sample_ids: set[str] = set()
    splits: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValueError(f"manifest record {index} must be an object")
        missing = sorted(required - set(record))
        if missing:
            raise ValueError(f"manifest record {index} missing fields: {missing}")
        sample_id = str(record["sample_id"])
        if not sample_id:
            raise ValueError(f"manifest record {index} has empty sample_id")
        if sample_id in sample_ids:
            raise ValueError(f"manifest contains duplicate sample_id: {sample_id}")
        sample_ids.add(sample_id)
        splits.add(str(record["split"]).lower())
    if len(splits) != 1:
        raise ValueError(f"manifest must contain exactly one split, got: {sorted(splits)}")
    split = next(iter(splits))
    if split == "test":
        raise ValueError("S1 metadata runner refuses test before the V3 validation/review gate")
    if split not in {"train", "validation", "val"}:
        raise ValueError(f"unsupported S1 split: {split}")
    return "validation" if split == "val" else split


def aggregate_localization_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("localization rows must not be empty")
    first_hits = [row["first_hit_rank"] for row in rows if row["first_hit_rank"] is not None]
    return {
        "sample_count": len(rows),
        "video_count": len({row["video_id"] for row in rows}),
        "event_coverage": mean(row["event_coverage"] for row in rows),
        "positive_window_recall": mean(row["positive_window_recall"] for row in rows),
        "mean_best_tiou": mean(row["mean_best_tiou"] for row in rows),
        "mean_first_hit_rank_when_hit": mean(first_hits) if first_hits else None,
        "miss_count": sum(row["first_hit_rank"] is None for row in rows),
        "mean_selected_window_count": mean(row["selected_window_count"] for row in rows),
        "mean_valid_window_count": mean(row["valid_window_count"] for row in rows),
        "mean_retention_rate": mean(
            row["selected_window_count"] / row["valid_window_count"] for row in rows
        ),
    }


def cluster_bootstrap_delta(
    left: Mapping[str, float],
    right: Mapping[str, float],
    sample_to_video: Mapping[str, str],
    *,
    repeats: int,
    seed: int,
) -> dict[str, float]:
    if repeats <= 0:
        raise ValueError("bootstrap repeats must be positive")
    if set(left) != set(right) or set(left) != set(sample_to_video):
        raise ValueError("paired bootstrap inputs must have identical sample ids")
    by_video: dict[str, list[str]] = defaultdict(list)
    for sample_id, video_id in sample_to_video.items():
        by_video[video_id].append(sample_id)
    videos = sorted(by_video)
    deltas = np.asarray([left[key] - right[key] for key in sorted(left)], dtype=np.float64)
    rng = np.random.default_rng(seed)
    bootstrap = np.empty(repeats, dtype=np.float64)
    for index in range(repeats):
        sampled = rng.choice(videos, size=len(videos), replace=True)
        values = [left[sample_id] - right[sample_id] for video in sampled for sample_id in by_video[video]]
        bootstrap[index] = float(np.mean(values))
    return {
        "delta": float(deltas.mean()),
        "ci95_lower": float(np.quantile(bootstrap, 0.025)),
        "ci95_upper": float(np.quantile(bootstrap, 0.975)),
        "bootstrap_repeats": repeats,
        "bootstrap_seed": seed,
    }


def fixed_window_timestamps(
    duration: float,
    *,
    window_sec: float = 1.0,
    hop_sec: float = 1.0,
) -> list[list[float]]:
    duration = float(duration)
    window_sec = float(window_sec)
    hop_sec = float(hop_sec)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be finite and positive")
    if not math.isfinite(window_sec) or window_sec <= 0:
        raise ValueError("window_sec must be finite and positive")
    if not math.isfinite(hop_sec) or hop_sec <= 0:
        raise ValueError("hop_sec must be finite and positive")
    timestamps = []
    start = 0.0
    while start < duration - 1e-12:
        timestamps.append([start, min(start + window_sec, duration)])
        start += hop_sec
    return timestamps


def all_indices(num_windows: int) -> list[int]:
    return list(range(_validate_num_windows(num_windows)))


def uniform_topk_indices(num_windows: int, k: int) -> list[int]:
    num_windows = _validate_num_windows(num_windows)
    k = _effective_k(num_windows, k)
    if k == num_windows:
        return list(range(num_windows))
    if k == 1:
        return [(num_windows - 1) // 2]
    raw = [round(index * (num_windows - 1) / (k - 1)) for index in range(k)]
    selected = []
    for value in raw:
        if value not in selected:
            selected.append(value)
    if len(selected) < k:
        selected.extend(index for index in range(num_windows) if index not in selected)
    return selected[:k]


def random_topk_indices(num_windows: int, k: int, *, seed: int, sample_id: str) -> list[int]:
    num_windows = _validate_num_windows(num_windows)
    k = _effective_k(num_windows, k)
    rng = random.Random(f"{int(seed)}\0{sample_id}")
    return rng.sample(range(num_windows), k)


def oracle_topk_indices(
    timestamps: Sequence[Sequence[float]],
    target_intervals: Sequence[Sequence[float]],
    k: int,
) -> list[int]:
    if not timestamps:
        raise ValueError("timestamps must not be empty")
    k = _effective_k(len(timestamps), k)
    intervals = merge_intervals((float(value[0]), float(value[1])) for value in target_intervals)
    scored = []
    for index, timestamp in enumerate(timestamps):
        start, end = _validate_timestamp(timestamp)
        overlap = sum(
            max(0.0, min(end, target_end) - max(start, target_start))
            for target_start, target_end in intervals
        )
        scored.append((-overlap, index))
    scored.sort()
    return [index for _negative_overlap, index in scored[:k]]


def evaluate_selection(
    timestamps: Sequence[Sequence[float]],
    target_intervals: Sequence[Sequence[float]],
    ranked_indices: Sequence[int],
) -> SampleLocalizationMetrics:
    if not timestamps:
        raise ValueError("timestamps must not be empty")
    intervals = merge_intervals((float(value[0]), float(value[1])) for value in target_intervals)
    selected = _validate_indices(ranked_indices, len(timestamps))
    positive_mask = window_target_mask(timestamps, intervals)
    positive_count = sum(positive_mask)
    selected_positive = sum(positive_mask[index] for index in selected)
    target_duration = sum(end - start for start, end in intervals)
    covered_duration = _covered_target_duration(
        [_validate_timestamp(timestamps[index]) for index in selected],
        intervals,
    )
    best_tious = []
    for target_start, target_end in intervals:
        best_tious.append(
            max(
                (_temporal_iou(_validate_timestamp(timestamps[index]), (target_start, target_end)) for index in selected),
                default=0.0,
            )
        )
    first_hit = next((rank for rank, index in enumerate(selected, start=1) if positive_mask[index]), None)
    return SampleLocalizationMetrics(
        event_coverage=covered_duration / target_duration,
        positive_window_recall=selected_positive / positive_count if positive_count else 0.0,
        mean_best_tiou=sum(best_tious) / len(best_tious),
        first_hit_rank=first_hit,
        selected_window_count=len(selected),
        valid_window_count=len(timestamps),
    )


def _covered_target_duration(
    selected_windows: Sequence[tuple[float, float]],
    target_intervals: Sequence[tuple[float, float]],
) -> float:
    intersections = []
    for window_start, window_end in selected_windows:
        for target_start, target_end in target_intervals:
            start = max(window_start, target_start)
            end = min(window_end, target_end)
            if end > start:
                intersections.append((start, end))
    if not intersections:
        return 0.0
    return sum(end - start for start, end in merge_intervals(intersections))


def _temporal_iou(left: tuple[float, float], right: tuple[float, float]) -> float:
    intersection = max(0.0, min(left[1], right[1]) - max(left[0], right[0]))
    union = (left[1] - left[0]) + (right[1] - right[0]) - intersection
    return intersection / union if union > 0 else 0.0


def _validate_timestamp(timestamp: Sequence[float]) -> tuple[float, float]:
    if len(timestamp) != 2:
        raise ValueError("timestamp must contain start/end")
    start, end = float(timestamp[0]), float(timestamp[1])
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise ValueError(f"invalid timestamp: {(start, end)}")
    return start, end


def _validate_num_windows(num_windows: int) -> int:
    num_windows = int(num_windows)
    if num_windows <= 0:
        raise ValueError("num_windows must be positive")
    return num_windows


def _effective_k(num_windows: int, k: int) -> int:
    k = int(k)
    if k <= 0:
        raise ValueError("k must be positive")
    return min(k, num_windows)


def _validate_indices(indices: Sequence[int], num_windows: int) -> list[int]:
    values = [int(value) for value in indices]
    if len(values) != len(set(values)):
        raise ValueError("selected indices must be unique")
    if any(value < 0 or value >= num_windows for value in values):
        raise ValueError("selected index is out of range")
    return values
