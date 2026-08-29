"""v4/raw物理视频级bootstrap CI的CPU单元测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_unav_s1r_audio_projector_v4_ci.py"
SPEC = importlib.util.spec_from_file_location("v4_ci_audit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _arrays():
    return {
        "video_ids": np.asarray(["a", "b", "c"]),
        "labels": np.asarray(["x", "y"]),
        "counts": np.asarray([[2, 0], [0, 2], [1, 1]], dtype=np.int32),
        "raw_correct": np.asarray([[1, 0], [0, 1], [1, 0]], dtype=np.int32),
        "projected_correct": np.asarray([[2, 0], [0, 1], [1, 1]], dtype=np.int32),
        "raw_margin": np.asarray([0.1, 0.2, 0.3]),
        "projected_margin": np.asarray([0.2, 0.3, 0.4]),
        "preservation_numerator": np.asarray([1, 1, 1], dtype=np.int32),
        "preservation_denominator": np.asarray([1, 1, 2], dtype=np.int32),
        "video_label_queries": np.asarray([1, 1, 2], dtype=np.int32),
    }


def test_point_metrics_use_macro_class_recall_and_video_mean_margin():
    result = MODULE.point_metrics(_arrays())

    assert np.isclose(result["raw_macro_recall_at_1"], 0.5)
    assert np.isclose(result["projected_macro_recall_at_1"], 5.0 / 6.0)
    assert np.isclose(result["raw_margin"], 0.2)
    assert np.isclose(result["margin_delta"], 0.1)
    assert result["validation_event_queries"] == 6
    assert result["validation_video_label_queries"] == 4
    assert result["raw_correct_preservation_rate"] == 0.75


def test_bootstrap_is_seed_deterministic(tmp_path):
    cache = tmp_path / "cache.npz"
    np.savez_compressed(cache, **_arrays())

    first = MODULE.bootstrap_cache(cache, seed=17, replicates=50, batch_size=13)
    second = MODULE.bootstrap_cache(cache, seed=17, replicates=50, batch_size=13)

    assert first["ci95"] == second["ci95"]
    assert first["cluster_unit"] == "physical_video"
    assert first["test_accessed"] is False
