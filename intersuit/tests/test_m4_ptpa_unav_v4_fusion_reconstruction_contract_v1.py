"""v4 fusion projector 重构 CPU 合同测试。"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs/m4_ptpa_unav_v4_fusion_reconstruction_contract_v1.json"


def test_contract_forbids_effect_expansion_before_cpu_pass():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    forbidden = contract["forbidden"]
    assert all(forbidden[key] for key in ("beats_training", "v4_semantic_training", "m4_training"))
    assert forbidden["gate_increase"] is True
    assert forbidden["formal_test"] is True
    assert forbidden["gpu_expansion"] is True


def test_contract_allows_only_isolated_fusion_modes():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["allowed_modes"] == [
        "identity_fusion",
        "bypass_old_fusion",
        "lightweight_norm_preserving",
    ]
    acceptance = contract["acceptance"]
    assert acceptance["preserve_adapter_relative_difference_min"] == 0.5
    assert acceptance["preserve_adapter_cosine_max"] == 0.95
