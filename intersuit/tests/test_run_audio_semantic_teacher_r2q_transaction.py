from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/run_audio_semantic_teacher_r2q_transaction.py"
)
SPEC = importlib.util.spec_from_file_location("r2q_transaction", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_augmentations_are_order_independent_and_finite():
    waveform = np.linspace(-0.25, 0.25, 48_000, dtype=np.float32)
    for name in MODULE.AUGMENTATIONS:
        first, first_parameters = MODULE.augment_waveform(
            waveform, "AVE:test", 17, name
        )
        second, second_parameters = MODULE.augment_waveform(
            waveform, "AVE:test", 17, name
        )
        assert np.array_equal(first, second)
        assert first_parameters == second_parameters
        assert first.shape == waveform.shape
        assert np.isfinite(first).all()
        assert np.max(np.abs(first)) <= 1.0


def test_confidence_policy_is_monotonic():
    assert MODULE.assign_confidence(0.06, 0.95, 1.0) == "high"
    assert MODULE.assign_confidence(0.03, 0.80, 2.0 / 3.0) == "medium"
    assert MODULE.assign_confidence(0.01, 1.0, 1.0) == "low"


def test_bootstrap_index_hash_is_reproducible():
    assert MODULE.bootstrap_indices_sha(
        196, 17
    ) == MODULE.bootstrap_indices_sha(196, 17)
    assert MODULE.bootstrap_indices_sha(
        196, 17
    ) != MODULE.bootstrap_indices_sha(196, 23)
