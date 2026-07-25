"""Deterministic media-pilot construction for Dynamic Selector V2 S1."""

from __future__ import annotations

import csv
import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .dynamic_selector_v2_localization import validate_s1_manifest_records


@dataclass(frozen=True)
class SourceClip:
    video_id: str
    source_start_sec: float
    source_end_sec: float
    split: str

    @property
    def duration(self) -> float:
        return self.source_end_sec - self.source_start_sec


def load_source_clip_csv(path: Path) -> dict[str, SourceClip]:
    clips: dict[str, SourceClip] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row_index, row in enumerate(csv.reader(handle), start=1):
            if len(row) != 4:
                raise ValueError(f"source CSV row {row_index} must contain four columns")
            video_id, raw_start, raw_end, split = row
            if not video_id or video_id in clips:
                raise ValueError(f"invalid or duplicate source video id at row {row_index}: {video_id!r}")
            start, end = float(raw_start), float(raw_end)
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
                raise ValueError(f"invalid source interval at row {row_index}: {(start, end)}")
            clips[video_id] = SourceClip(video_id, start, end, split.lower())
    if not clips:
        raise ValueError("source CSV is empty")
    return clips


def build_media_pilot(
    records: Any,
    source_clips: Mapping[str, SourceClip],
    *,
    limit: int,
    sparse_ratio: float = 0.10,
    seed: int = 20260724,
) -> list[dict[str, Any]]:
    split = validate_s1_manifest_records(records)
    if split != "validation":
        raise ValueError("media qualification pilot must use validation, not training")
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not 0 < sparse_ratio <= 1:
        raise ValueError("sparse_ratio must be in (0,1]")

    by_video: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if float(record["union_duration_ratio"]) <= sparse_ratio:
            by_video[str(record["video_id"])].append(dict(record))
    if len(by_video) < limit:
        raise ValueError(f"only {len(by_video)} eligible videos for requested limit={limit}")

    candidates: dict[str, dict[str, Any]] = {}
    for video_id, samples in by_video.items():
        clip = source_clips.get(video_id)
        if clip is None:
            raise ValueError(f"source CSV missing validation video: {video_id}")
        if clip.split not in {"validation", "val"}:
            raise ValueError(f"source split mismatch for {video_id}: {clip.split}")
        durations = {float(sample["duration"]) for sample in samples}
        if len(durations) != 1:
            raise ValueError(f"manifest duration mismatch within video: {video_id}")
        duration = next(iter(durations))
        if not math.isclose(duration, clip.duration, rel_tol=0.0, abs_tol=0.05):
            raise ValueError(
                f"source/manifest duration mismatch for {video_id}: {clip.duration} vs {duration}"
            )
        duration_bin = "short_lt30" if duration < 30 else "medium_30_50" if duration < 50 else "long_ge50"
        complex_event = len(samples) > 1 or any(int(sample["annotation_count"]) > 1 for sample in samples)
        candidates[video_id] = {
            "video_id": video_id,
            "split": "validation",
            "source_start_sec": clip.source_start_sec,
            "source_end_sec": clip.source_end_sec,
            "duration": duration,
            "duration_bin": duration_bin,
            "complexity_bin": "multi_label_or_occurrence" if complex_event else "single_label_single_occurrence",
            "sample_ids": sorted(str(sample["sample_id"]) for sample in samples),
            "event_labels": sorted({str(sample["event_label"]) for sample in samples}),
            "sample_count": len(samples),
            "max_annotation_count": max(int(sample["annotation_count"]) for sample in samples),
        }

    strata = [
        (duration_bin, complexity_bin)
        for duration_bin in ("short_lt30", "medium_30_50", "long_ge50")
        for complexity_bin in ("single_label_single_occurrence", "multi_label_or_occurrence")
    ]
    pools = {
        stratum: [item for item in candidates.values() if (item["duration_bin"], item["complexity_bin"]) == stratum]
        for stratum in strata
    }
    selected: list[dict[str, Any]] = []
    covered_labels: set[str] = set()
    while len(selected) < limit:
        progressed = False
        for stratum in strata:
            pool = pools[stratum]
            if not pool or len(selected) >= limit:
                continue
            item = min(
                pool,
                key=lambda value: (
                    -len(set(value["event_labels"]) - covered_labels),
                    -int(value["sample_count"]),
                    -int(value["max_annotation_count"]),
                    _stable_tie(seed, str(value["video_id"])),
                ),
            )
            pool.remove(item)
            selected.append(item)
            covered_labels.update(item["event_labels"])
            progressed = True
        if not progressed:
            raise ValueError("candidate pools exhausted before reaching requested limit")
    return [{"selection_rank": index, **item} for index, item in enumerate(selected, start=1)]


def _stable_tie(seed: int, video_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{video_id}".encode("utf-8")).hexdigest()
