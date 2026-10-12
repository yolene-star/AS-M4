"""v4表示衰减探针的纯CPU指标测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/probe_m4_ptpa_unav_v4_representation_decay_v1.py"
SPEC = importlib.util.spec_from_file_location("v4_decay_probe", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_vector_metrics_detect_identical_and_orthogonal_means():
    identical = MODULE.vector_metrics(torch.tensor([[1.0, 0.0]]), torch.tensor([[1.0, 0.0]]))
    orthogonal = MODULE.vector_metrics(torch.tensor([[1.0, 0.0]]), torch.tensor([[0.0, 1.0]]))

    assert identical["mean_cosine"] == 1.0
    assert identical["relative_mean_difference"] == 0.0
    assert orthogonal["mean_cosine"] == 0.0
    assert orthogonal["relative_mean_difference"] > 1.0
