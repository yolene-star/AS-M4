"""Frozen data semantics for the Dynamic Window Selector V2 diagnostics."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class UnAVVideoLabelSample:
    """One unambiguous UnAV query target.

    The natural unit is ``(video_id, event_label)`` rather than one annotation
    row.  Every occurrence of the label in the video is a valid answer to the
    event-label query and therefore belongs to the positive interval union.
    """

    sample_id: str
    split: str
    video_id: str
    duration: float
    event_label: str
    label_id: int
    target_intervals: tuple[tuple[float, float], ...]
    annotation_count: int
    union_duration: float
    union_duration_ratio: float

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["target_intervals"] = [list(value) for value in self.target_intervals]
        return payload


def frozen_label_mapping(train_labels: Iterable[str]) -> dict[str, int]:
    labels = sorted({str(value).strip() for value in train_labels if str(value).strip()})
    if not labels:
        raise ValueError("train labels must not be empty")
    return {label: index for index, label in enumerate(labels)}


def build_unav_video_label_samples(
    rows: Iterable[Mapping],
    *,
    split: str,
    label_mapping: Mapping[str, int],
    excluded_video_ids: Iterable[str] = (),
) -> list[UnAVVideoLabelSample]:
    split = str(split).strip().lower()
    if not split:
        raise ValueError("split must not be empty")
    exclusions = {str(value) for value in excluded_video_ids}
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        video_id = str(row["video_id"])
        if video_id in exclusions:
            continue
        label = str(row["event_label"]).strip()
        if label not in label_mapping:
            raise ValueError(f"label is absent from frozen train mapping: {label}")
        duration = float(row["duration"])
        timestamp = row["timestamp"]
        if isinstance(timestamp, (str, bytes)) or not hasattr(timestamp, "__len__") or len(timestamp) != 2:
            raise ValueError(f"invalid timestamp for {video_id}/{label}")
        start, end = float(timestamp[0]), float(timestamp[1])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"invalid duration for {video_id}")
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration + 1e-6):
            raise ValueError(f"invalid interval for {video_id}/{label}: {(start, end)}")
        key = (video_id, label)
        item = grouped.setdefault(key, {"duration": duration, "intervals": []})
        if not math.isclose(float(item["duration"]), duration, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError(f"inconsistent duration for {video_id}")
        item["intervals"].append((start, min(end, duration)))

    samples = []
    for (video_id, label), item in sorted(grouped.items()):
        raw_intervals = tuple(sorted(item["intervals"]))
        merged = merge_intervals(raw_intervals)
        union_duration = sum(end - start for start, end in merged)
        duration = float(item["duration"])
        sample_id = stable_sample_id(split, video_id, label)
        samples.append(
            UnAVVideoLabelSample(
                sample_id=sample_id,
                split=split,
                video_id=video_id,
                duration=duration,
                event_label=label,
                label_id=int(label_mapping[label]),
                target_intervals=merged,
                annotation_count=len(raw_intervals),
                union_duration=union_duration,
                union_duration_ratio=union_duration / duration,
            )
        )
    return samples


def merge_intervals(intervals: Iterable[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    ordered = sorted((float(start), float(end)) for start, end in intervals)
    if not ordered:
        raise ValueError("target intervals must not be empty")
    merged: list[list[float]] = []
    for start, end in ordered:
        if not (math.isfinite(start) and math.isfinite(end) and start < end):
            raise ValueError(f"invalid target interval: {(start, end)}")
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return tuple((start, end) for start, end in merged)


def window_target_mask(
    timestamps: Sequence[Sequence[float]],
    target_intervals: Sequence[Sequence[float]],
) -> list[bool]:
    intervals = merge_intervals((float(value[0]), float(value[1])) for value in target_intervals)
    mask = []
    for timestamp in timestamps:
        if len(timestamp) != 2:
            raise ValueError("window timestamp must contain start/end")
        start, end = float(timestamp[0]), float(timestamp[1])
        if not (math.isfinite(start) and math.isfinite(end) and start < end):
            raise ValueError(f"invalid window timestamp: {(start, end)}")
        mask.append(any(min(end, target_end) > max(start, target_start) for target_start, target_end in intervals))
    return mask


def stable_sample_id(split: str, video_id: str, event_label: str) -> str:
    payload = f"{split}\0{video_id}\0{event_label}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
