"""UnAV-100 S1 metadata ledger 与 canary 计划测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prepare_m4_ptpa_unav_s1_pilot_v1.py"
SPEC = importlib.util.spec_from_file_location("prepare_m4_ptpa_unav_s1_pilot_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_stable_key_is_deterministic_and_seeded():
    assert module.stable_key(7, "x", "video") == module.stable_key(7, "x", "video")
    assert module.stable_key(7, "x", "video") != module.stable_key(8, "x", "video")


def test_real_metadata_exclusion_and_canary_plan_are_deterministic():
    contract = ROOT / "configs/m4_ptpa_unav_s1_reconstruction_v1.json"
    first = module.build_outputs(contract)
    second = module.build_outputs(contract)

    assert first["summary"] == second["summary"]
    assert first["plan_rows"] == second["plan_rows"]
    assert first["summary"]["status"] == "PASS_METADATA_LOCKED_PROBE_ONLY"
    assert first["summary"]["eligible"] == {
        "train": {"excluded": 263, "remaining": 6226, "labels": 100, "min_unique_videos_per_label": 22},
        "validation": {"excluded": 417, "remaining": 1717, "labels": 100, "min_unique_videos_per_label": 6},
        "test": {"excluded": 69, "remaining": 2098, "labels": 100, "min_unique_videos_per_label": 12},
    }
    assert first["summary"]["pilot"]["rows"] == 20
    assert first["summary"]["pilot"]["split_counts"] == {"train": 10, "validation": 10}
    assert first["summary"]["pilot"]["test_rows"] == 0
    assert all(row["historical_overlap"] is False for row in first["plan_rows"])
    assert all(row["media_download_allowed"] is False for row in first["plan_rows"])


def test_duration_and_complexity_bins_cover_boundaries():
    assert module.duration_bin(29.9) == "short_lt30"
    assert module.duration_bin(30.0) == "medium_30_45"
    assert module.duration_bin(45.0) == "long_ge45"
    assert module.complexity_bin(1) == "single_event"
    assert module.complexity_bin(2) == "multi_event"
