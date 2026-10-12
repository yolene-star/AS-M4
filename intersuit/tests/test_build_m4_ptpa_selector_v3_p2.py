from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_m4_ptpa_selector_v3_p2.py"
SPEC = importlib.util.spec_from_file_location("build_m4_ptpa_selector_v3_p2", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def rows():
    return [
        {
            "sample_id": "a",
            "video_id": "a",
            "duration_sec": 10.0,
            "event_start_sec": 2.0,
            "event_end_sec": 4.0,
            "physical_media_sha256": "a" * 64,
            "prompt": "Q1",
            "caption": "A1",
        },
        {
            "sample_id": "b",
            "video_id": "b",
            "duration_sec": 20.0,
            "event_start_sec": 10.0,
            "event_end_sec": 14.0,
            "physical_media_sha256": "b" * 64,
            "prompt": "Q2",
            "caption": "A2",
        },
    ]


def test_oracle_is_diagnostic_and_random_is_seeded():
    oracle = module.condition_samples("external_oracle_top8", rows())[0]
    random = module.condition_samples("external_random_top8", rows())[0]

    assert oracle["scene_audio_selection_policy"] == "oracle_topk"
    assert oracle["scene_audio_selection_diagnostic"] is True
    assert oracle["scene_audio_selection_top_k"] == 8
    assert random["scene_audio_selection_policy"] == "random_topk"
    assert random["scene_audio_selection_seed"] == module.CONTROL_SEED


def test_fixed_all_has_no_accidental_top_k():
    sample = module.condition_samples("fixed_all", rows())[0]

    assert sample["scene_audio_selection_policy"] == "fixed_all"
    assert "scene_audio_selection_top_k" not in sample


def test_current_micro_preserves_internal_selector_path():
    sample = module.condition_samples("current_micro_top8", rows())[0]

    assert "scene_audio_selection_policy" not in sample
    assert sample["audio_sha256"] == "a" * 64
    assert sample["scene_audio_window_sec"] == 1.0
    assert sample["scene_audio_hop_sec"] == 1.0


def test_mismatch_interval_maps_to_other_physical_duration():
    first, second = module.condition_samples("oracle_mismatch", rows())

    assert first["scene_audio_target_intervals"][0] == pytest.approx([4.0, 8.0])
    assert second["scene_audio_target_intervals"][0] == pytest.approx([5.0, 7.0])
    assert first["physical_media_id"] != second["physical_media_id"]


def test_only_gate0_and_behavior_force_zero_gate():
    assert module.experiment_env("behavior_off")["AS_M4_FORCE_AUDIO_GATE"] == "0"
    assert module.experiment_env("gate0")["AS_M4_FORCE_AUDIO_GATE"] == "0"
    assert "AS_M4_FORCE_AUDIO_GATE" not in module.experiment_env("external_oracle_top8")


def test_p2_generation_budget_is_large_enough_for_caption_eos_review():
    assert module.MAX_NEW_TOKENS == 128
