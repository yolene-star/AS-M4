"""新 fusion projector gate0 结果锁测试。"""
from __future__ import annotations
import hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/'configs/m4_ptpa_unav_v4_fusion_projector_gate0_result_lock_v1.json'
def digest(p:Path)->str:
    h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
def test_locked_artifacts_and_permissions():
    lock=json.loads(LOCK.read_text())
    assert lock['status']=='PASS_FUSION_PROJECTOR_GATE0_EXACT_ROLLBACK'
    for item in lock['artifacts'].values(): assert digest(ROOT.parent/item['path'])==item['sha256']
    assert lock['checks']['gate0_exact_zero'] is True
    assert lock['checks']['m4_effect_claim_allowed'] is False
    assert lock['checks']['nonzero_gate_automatically_allowed'] is False
    assert lock['execution']['training_performed'] is False
    assert lock['execution']['formal_test_accessed'] is False
