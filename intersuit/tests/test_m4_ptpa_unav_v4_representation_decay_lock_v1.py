"""v4表示衰减结果锁测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "configs/m4_ptpa_unav_v4_representation_decay_lock_v1.json"


def test_decay_result_sha_and_diagnosis():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    result = ROOT.parent / lock["result_path"]
    digest = hashlib.sha256(result.read_bytes()).hexdigest()

    assert digest == lock["result_sha256"]
    assert lock["diagnosis"]["v4_semantic_collapse"] is False
    assert lock["diagnosis"]["adapter_partial_compression"] is True
    assert lock["diagnosis"]["fusion_projector_primary_collapse"] is True
    assert lock["diagnosis"]["tiny_fixed_gate_compounds_loss"] is True
    assert lock["adapter_scale"]["bias_dominance_rejected"] is True


def test_decay_lock_stops_expansion_but_allows_isolated_plan():
    permissions = json.loads(LOCK.read_text(encoding="utf-8"))["permissions"]

    assert permissions["full_dev_allowed"] is False
    assert permissions["formal_test_allowed"] is False
    assert permissions["training_allowed"] is False
    assert permissions["gate_increase_allowed"] is False
    assert permissions["isolated_fusion_reconstruction_plan_allowed"] is True
