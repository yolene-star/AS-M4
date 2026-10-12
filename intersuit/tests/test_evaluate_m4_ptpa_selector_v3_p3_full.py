from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_m4_ptpa_selector_v3_p3_full.py"
SPEC = importlib.util.spec_from_file_location("evaluate_m4_ptpa_selector_v3_p3_full", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def payload(video_ids):
    return {
        "per_sample": [
            {
                "video_id": video_id,
                "sentence_id": 0,
                "score": 0.2,
                "sufficient_statistics": [float(index)] * 23,
            }
            for index, video_id in enumerate(video_ids, start=1)
        ]
    }


def test_statistics_matrix_preserves_frozen_physical_video_order():
    result = module.statistics_matrix(payload(["sha256:a", "sha256:b"]), ["sha256:a", "sha256:b"])

    assert result.shape == (2, 23)
    assert result[:, 0].tolist() == [1.0, 2.0]


def test_statistics_matrix_rejects_order_drift():
    with pytest.raises(ValueError, match="顺序/集合漂移"):
        module.statistics_matrix(payload(["sha256:b", "sha256:a"]), ["sha256:a", "sha256:b"])


def test_paired_bootstrap_uses_five_seed_means():
    class MeanFirstColumnScorer:
        def score_corpus(self, statistics):
            return float(np.mean(statistics[:, 0]))

    def matrix(value):
        result = np.zeros((10, 23), dtype=np.float64)
        result[:, 0] = value
        return result

    statistics = {
        "oracle_top8": matrix(0.30),
        "fixed_all": matrix(0.20),
        "current_micro_top8": matrix(0.19),
        "behavior_off": matrix(0.18),
    }
    statistics.update({
        f"random_top8_seed_{seed}": matrix(0.10 + seed * 0.01)
        for seed in range(5)
    })
    statistics.update({
        f"oracle_mismatch_seed_{seed}": matrix(0.12 + seed * 0.01)
        for seed in range(5)
    })
    aggregates = {name: float(values[0, 0]) for name, values in statistics.items()}

    result = module.paired_bootstrap_comparisons(
        statistics,
        aggregates,
        repeats=100,
        seed=20260724,
        scorer=MeanFirstColumnScorer(),
    )

    assert result["oracle_minus_random_mean"]["delta"] == pytest.approx(0.18)
    assert result["oracle_minus_mismatch_mean"]["delta"] == pytest.approx(0.16)
    assert result["oracle_minus_fixed_all"]["ci95_lower"] == pytest.approx(0.10)


def test_paired_bootstrap_rejects_nonpositive_repeats():
    with pytest.raises(ValueError, match="必须为正数"):
        module.paired_bootstrap_comparisons({}, {}, repeats=0, seed=1, scorer=object())


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
