from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate_audio_semantic_reconstruction_r1.py"
SPEC = importlib.util.spec_from_file_location("evaluate_audio_semantic_r1", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_sample_weighted_mean_respects_partial_tail() -> None:
    embeddings = torch.tensor([[1.0, 0.0], [0.0, 1.0]])
    result = MODULE.sample_weighted_mean(
        embeddings, torch.tensor([16000, 4000])
    )
    assert torch.allclose(result, torch.tensor([0.8, 0.2]))


def test_robust_top_m_uses_high_rms_windows() -> None:
    embeddings = torch.tensor([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]])
    result = MODULE.robust_top_m_mean(
        embeddings,
        torch.tensor([16000, 16000, 16000]),
        torch.tensor([0.1, 0.9, 0.8]),
        top_m=2,
    )
    assert torch.allclose(result, torch.tensor([-0.5, 0.5]))


def test_centroid_golden_fixture_is_perfect() -> None:
    reference = [
        {"sample_id": "a1", "canonical_label": "a", "rep": torch.tensor([1.0, 0.0])},
        {"sample_id": "b1", "canonical_label": "b", "rep": torch.tensor([0.0, 1.0])},
    ]
    query = [
        {"sample_id": "a2", "canonical_label": "a", "rep": torch.tensor([0.9, 0.1])},
        {"sample_id": "b2", "canonical_label": "b", "rep": torch.tensor([0.1, 0.9])},
    ]
    metrics, per_sample = MODULE.evaluate_centroids(
        reference,
        query,
        "rep",
        ["a", "b"],
        bootstrap_seed=17,
    )
    assert metrics["macro_class_recall_at_1"] == 1.0
    assert metrics["rank_first"] == 1.0
    assert metrics["margin_bootstrap_95_ci"][0] > 0
    assert all(row["correct"] for row in per_sample)


def test_bootstrap_is_seed_deterministic() -> None:
    values = [0.1, 0.2, 0.3, 0.4]
    assert MODULE.bootstrap_mean_ci(values, 17, 100) == MODULE.bootstrap_mean_ci(
        values, 17, 100
    )
