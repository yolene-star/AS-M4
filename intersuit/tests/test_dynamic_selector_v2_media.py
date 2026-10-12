from __future__ import annotations

import csv

import pytest

from intersuit.harness.dynamic_selector_v2_media import (
    SourceClip,
    build_full_validation_media_allowlist,
    build_media_pilot,
    load_source_clip_csv,
)


def _record(sample_id, video_id, *, split="validation", duration=20.0, label="event", annotations=1):
    return {
        "sample_id": sample_id,
        "split": split,
        "video_id": video_id,
        "duration": duration,
        "event_label": label,
        "target_intervals": [[1.0, 2.0]],
        "union_duration_ratio": 0.05,
        "annotation_count": annotations,
    }


def test_source_csv_rejects_duplicate_video_ids(tmp_path):
    path = tmp_path / "source.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows([["video", 0, 20, "validation"], ["video", 0, 20, "validation"]])
    with pytest.raises(ValueError, match="duplicate"):
        load_source_clip_csv(path)


def test_media_pilot_is_deterministic_and_validation_only():
    records = [
        _record("a", "short-simple", label="a"),
        _record("b", "short-complex", label="b", annotations=2),
        _record("c", "medium-simple", duration=40.0, label="c"),
        _record("d", "medium-complex", duration=40.0, label="d", annotations=2),
        _record("e", "long-simple", duration=60.0, label="e"),
        _record("f", "long-complex", duration=60.0, label="f", annotations=2),
    ]
    clips = {
        record["video_id"]: SourceClip(record["video_id"], 30.0, 30.0 + record["duration"], "validation")
        for record in records
    }
    first = build_media_pilot(records, clips, limit=6)
    assert first == build_media_pilot(records, clips, limit=6)
    assert {entry["duration_bin"] for entry in first} == {"short_lt30", "medium_30_50", "long_ge50"}
    assert {entry["complexity_bin"] for entry in first} == {
        "single_label_single_occurrence",
        "multi_label_or_occurrence",
    }

    test_records = [{**record, "split": "test"} for record in records]
    with pytest.raises(ValueError, match="refuses test"):
        build_media_pilot(test_records, clips, limit=1)


def test_media_pilot_rejects_source_duration_mismatch():
    records = [_record("a", "video")]
    clips = {"video": SourceClip("video", 0.0, 19.0, "validation")}
    with pytest.raises(ValueError, match="duration mismatch"):
        build_media_pilot(records, clips, limit=1)


def test_full_validation_allowlist_contains_every_sparse_video_in_stable_order():
    records = [
        _record("b1", "video-b", label="b"),
        _record("a2", "video-a", label="c", annotations=2),
        _record("a1", "video-a", label="a"),
        {**_record("dense", "video-dense"), "union_duration_ratio": 0.20},
    ]
    clips = {
        "video-a": SourceClip("video-a", 10.0, 30.0, "validation"),
        "video-b": SourceClip("video-b", 20.0, 40.0, "val"),
        "video-dense": SourceClip("video-dense", 0.0, 20.0, "validation"),
    }
    entries = build_full_validation_media_allowlist(records, clips)
    assert [entry["video_id"] for entry in entries] == ["video-a", "video-b"]
    assert entries[0]["sample_ids"] == ["a1", "a2"]
    assert entries[0]["sample_count"] == 2
    assert [entry["selection_rank"] for entry in entries] == [1, 2]


def test_full_validation_allowlist_refuses_training():
    records = [{**_record("a", "video"), "split": "train"}]
    clips = {"video": SourceClip("video", 0.0, 20.0, "train")}
    with pytest.raises(ValueError, match="must use validation"):
        build_full_validation_media_allowlist(records, clips)
