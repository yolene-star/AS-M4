"""LLP 真实 M4 teacher-forced runner 的 CPU contract tests。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_llp_modality_teacher_forced_v1.py"
SPEC = importlib.util.spec_from_file_location("run_llp_modality_teacher_forced_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def group_rows(base_id="x"):
    common = {
        "base_id": base_id, "sample_id": "s", "physical_media_id": "s", "video_path": "v.mp4",
        "window_start": 2.0, "window_end": 3.0, "modality_role": "audio_only", "event_label": "Speech",
        "correct_audio_path": "s.wav", "mismatch_sample_id": "m", "mismatch_audio_path": "m.wav",
        "mismatch_window_start": 4.0, "mismatch_window_end": 5.0,
    }
    return [
        {**common, "eval_id": f"{base_id}:{query}:{condition}", "query_type": query, "condition": condition}
        for query, conditions in (
            ("audio_presence", ("correct_audio", "mismatch_audio", "video_only", "silence")),
            ("visual_presence", ("video_only", "correct_audio", "mismatch_audio")),
        )
        for condition in conditions
    ]


def test_group_manifest_requires_exact_seven_rows():
    groups = module.group_manifest(group_rows())
    assert len(groups) == 1
    assert len(groups[0]["rows"]) == 7
    with pytest.raises(ValueError, match="不完整"):
        module.group_manifest(group_rows()[:-1])


def test_audio_request_maps_source_window_and_condition():
    rows = {row["condition"]: row for row in group_rows() if row["query_type"] == "audio_presence"}
    correct, debug = module.audio_request(rows["correct_audio"])
    assert correct["scene_audio_selected_indices"] == [4]
    assert debug == "original"
    mismatch, _ = module.audio_request(rows["mismatch_audio"])
    assert mismatch["scene_audio_selected_indices"] == [8]
    silence, debug = module.audio_request(rows["silence"])
    assert silence["scene_audio_selected_indices"] == [4]
    assert debug == "silence"
    assert module.audio_request(rows["video_only"]) == (None, "original")


def test_non_half_second_window_fails_closed():
    row = group_rows()[0]
    row["window_start"] = 2.25
    with pytest.raises(ValueError, match="0.5 秒对齐窗口"):
        module.audio_request(row)
