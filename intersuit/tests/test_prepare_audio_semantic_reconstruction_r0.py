from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "intersuit/scripts/prepare_audio_semantic_reconstruction_r0.py"
SPEC = importlib.util.spec_from_file_location("prepare_audio_semantic_r0", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def row(label_id: int, sample_id: str) -> dict:
    return {
        "label_id": label_id,
        "physical_media_id": f"AVE:{sample_id}",
        "canonical_label": f"class-{label_id}",
    }


def test_expanded_split_is_deterministic_balanced_and_disjoint() -> None:
    rows = [
        row(label_id, f"{label_id}-{sample_index}")
        for label_id in range(2)
        for sample_index in range(10)
    ]
    first = MODULE.split_expanded(rows, seed=123)
    second = MODULE.split_expanded(list(reversed(rows)), seed=123)
    assert first == second
    counts = {}
    for item in first:
        counts[item["diagnostic_split"]] = counts.get(item["diagnostic_split"], 0) + 1
    assert counts == {"train": 12, "validation": 4, "test_locked": 4}
    MODULE.assert_zero_overlap(first, ["train", "validation", "test_locked"])


def test_pilot_singleton_is_reference_only() -> None:
    rows = [row(0, "only"), row(1, "a"), row(1, "b"), row(1, "c")]
    result = MODULE.split_pilot(rows, seed=123)
    singleton = [item for item in result if item["label_id"] == 0]
    assert singleton[0]["diagnostic_split"] == "reference"
    assert sum(item["diagnostic_split"] == "query" for item in result) == 1


def test_zero_overlap_fails_closed() -> None:
    rows = [
        {**row(0, "same"), "diagnostic_split": "train"},
        {**row(0, "same"), "diagnostic_split": "validation"},
    ]
    with pytest.raises(ValueError, match="物理媒体重叠"):
        MODULE.assert_zero_overlap(rows, ["train", "validation"])


def test_source_validation_rejects_unstable_label_mapping(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    video = tmp_path / "a.mp4"
    audio.write_bytes(b"0" * 45)
    video.write_bytes(b"0")
    source = [
        {
            "youtube_id": "sample",
            "label": "0",
            "valid": True,
            "audio_path": str(audio),
            "video_path": str(video),
            "has_video": True,
            "has_audio": True,
            "audio_non_empty": True,
            "split": "train",
        }
    ]
    with pytest.raises(ValueError, match="不一致"):
        MODULE.validate_source_rows(
            source,
            "fixture",
            ["class-a"],
            {"sample": {"class-b"}},
        )
