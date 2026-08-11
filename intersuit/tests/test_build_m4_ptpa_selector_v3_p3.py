from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_m4_ptpa_selector_v3_p3.py"
SPEC = importlib.util.spec_from_file_location("build_m4_ptpa_selector_v3_p3", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def row(video: str, duration: float, start: float, end: float):
    return {
        "sample_id": f"sample:{video}",
        "video_id": video,
        "duration_sec": duration,
        "event_start_sec": start,
        "event_end_sec": end,
        "event_index": 0,
        "prompt": "Describe.",
        "caption": "Caption.",
    }


def test_condition_matrix_is_exactly_four_core_plus_five_plus_five():
    names = module.condition_names()

    assert len(names) == 14
    assert names[:4] == ["behavior_off", "fixed_all", "current_micro_top8", "oracle_top8"]
    assert sum(name.startswith("random_top8_seed_") for name in names) == 5
    assert sum(name.startswith("oracle_mismatch_seed_") for name in names) == 5


def test_canary_freeze_is_order_independent_and_unique():
    rows = [row(f"v{index:03d}", 10.0, 1.0, 2.0) for index in range(100)]

    first = module.freeze_canary(rows)
    replay = module.freeze_canary(list(reversed(rows)))

    assert first == replay
    assert len(first) == 20
    assert len({item["video_id"] for item in first}) == 20


def test_mismatch_sample_uses_source_identity_and_mapped_interval():
    target = row("target", 10.0, 2.0, 4.0)
    source = row("source", 20.0, 0.0, 1.0)
    sample = module.build_sample(
        "oracle_mismatch_seed_20260720",
        target,
        source,
        {"target": "a" * 64, "source": "b" * 64},
    )

    assert sample["scene_audio_path"].endswith("source.mp4")
    assert sample["scene_audio_expected_duration_sec"] == 20.0
    assert sample["audio_sha256"] == "b" * 64
    assert sample["physical_media_id"] == f"sha256:{'a' * 64}"
    assert sample["mismatch_source_physical_media_id"] == f"sha256:{'b' * 64}"
    assert sample["scene_audio_target_intervals"][0] == pytest.approx([4.0, 8.0])
    assert sample["scene_audio_selection_diagnostic"] is True


def test_random_seed_and_oracle_permissions_are_explicit():
    target = row("target", 10.0, 2.0, 4.0)
    sha = {"target": "a" * 64}
    random_sample = module.build_sample("random_top8_seed_20260721", target, target, sha)
    oracle_sample = module.build_sample("oracle_top8", target, target, sha)

    assert random_sample["scene_audio_selection_seed"] == 20260721
    assert "scene_audio_selection_diagnostic" not in random_sample
    assert oracle_sample["scene_audio_selection_diagnostic"] is True
