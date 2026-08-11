from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_m4_ptpa_selector_v3_p3_full.py"
SPEC = importlib.util.spec_from_file_location("evaluate_m4_ptpa_selector_v3_p3_full", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_score_map_preserves_frozen_physical_video_order():
    payload = {
        "per_sample": [
            {"video_id": "sha256:a", "sentence_id": 0, "score": 0.2},
            {"video_id": "sha256:b", "sentence_id": 0, "score": 0.4},
        ]
    }

    result = module.score_map(payload, ["sha256:a", "sha256:b"])

    assert result == {"sha256:a": 0.2, "sha256:b": 0.4}


def test_score_map_rejects_order_drift():
    payload = {
        "per_sample": [
            {"video_id": "sha256:b", "sentence_id": 0, "score": 0.4},
            {"video_id": "sha256:a", "sentence_id": 0, "score": 0.2},
        ]
    }

    with pytest.raises(ValueError, match="顺序/集合漂移"):
        module.score_map(payload, ["sha256:a", "sha256:b"])


def test_paired_bootstrap_uses_five_seed_means():
    videos = [f"video-{index}" for index in range(10)]
    score_maps = {
        "oracle_top8": {video: 0.30 for video in videos},
        "fixed_all": {video: 0.20 for video in videos},
        "current_micro_top8": {video: 0.19 for video in videos},
        "behavior_off": {video: 0.18 for video in videos},
    }
    score_maps.update({
        f"random_top8_seed_{seed}": {video: 0.10 + seed * 0.01 for video in videos}
        for seed in range(5)
    })
    score_maps.update({
        f"oracle_mismatch_seed_{seed}": {video: 0.12 + seed * 0.01 for video in videos}
        for seed in range(5)
    })

    result = module.paired_bootstrap_comparisons(score_maps, repeats=100, seed=20260724)

    assert result["oracle_minus_random_mean"]["delta"] == pytest.approx(0.18)
    assert result["oracle_minus_mismatch_mean"]["delta"] == pytest.approx(0.16)
    assert result["oracle_minus_fixed_all"]["ci95_lower"] == pytest.approx(0.10)


def test_oracle_gate_fails_closed_when_current_gain_is_too_small():
    comparisons = {
        "oracle_minus_fixed_all": {"delta": 0.02, "ci95_lower": 0.01},
        "oracle_minus_random_mean": {"delta": 0.02, "ci95_lower": 0.01},
        "oracle_minus_current_micro_top8": {"delta": 0.009, "ci95_lower": 0.001},
        "oracle_minus_mismatch_mean": {"delta": 0.02, "ci95_lower": 0.01},
        "oracle_minus_behavior_off": {"delta": 0.02, "ci95_lower": 0.01},
    }
    anomaly_checks = {"oracle_empty_output_not_higher_than_all": True}
    operational = {"matrix_valid": True}

    checks = module.oracle_gate(comparisons, anomaly_checks, operational)

    assert checks["oracle_minus_current_at_least_0_01"] is False
    assert not all(checks.values())


def test_oracle_gate_passes_only_when_all_effect_and_safety_checks_pass():
    comparisons = {
        "oracle_minus_fixed_all": {"delta": 0.02, "ci95_lower": 0.001},
        "oracle_minus_random_mean": {"delta": 0.03, "ci95_lower": 0.002},
        "oracle_minus_current_micro_top8": {"delta": 0.01, "ci95_lower": 0.0001},
        "oracle_minus_mismatch_mean": {"delta": 0.001, "ci95_lower": -0.01},
        "oracle_minus_behavior_off": {"delta": 0.001, "ci95_lower": -0.01},
    }
    anomaly_checks = {"oracle_empty_output_not_higher_than_all": True}
    operational = {"matrix_valid": True}

    checks = module.oracle_gate(comparisons, anomaly_checks, operational)

    assert all(checks.values())

    anomaly_checks["oracle_empty_output_not_higher_than_all"] = False
    assert not all(module.oracle_gate(comparisons, anomaly_checks, operational).values())
