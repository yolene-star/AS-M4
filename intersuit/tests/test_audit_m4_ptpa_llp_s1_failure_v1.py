"""LLP S1 failure analysis 独立审计器的 CPU tests。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_m4_ptpa_llp_s1_failure_v1.py"
SPEC = importlib.util.spec_from_file_location("audit_m4_ptpa_llp_s1_failure_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_normalize_is_finite_and_unit_length():
    value = module.normalize(torch.tensor([3.0, 4.0]))
    assert torch.isfinite(value).all()
    assert torch.linalg.vector_norm(value).item() == 1.0


def test_prototype_geometry_counts_all_pairs_and_cooccurrence():
    labels = ["A", "B", "C"]
    selected = {"A": ["a"], "B": ["b"], "C": ["c"]}
    vectors = {
        "a": torch.tensor([1.0, 0.0]),
        "b": torch.tensor([1.0, 0.0]),
        "c": torch.tensor([0.0, 1.0]),
    }
    weak = {"a": {"A", "B"}, "b": {"B"}, "c": {"C"}}
    result = module.prototype_geometry(labels, selected, vectors, weak)
    assert result["pair_count"] == 3
    assert result["cooccurring_pair_count"] == 1
    assert result["noncooccurring_pair_count"] == 2
    assert result["cooccurring_cosine_mean"] == 1.0
