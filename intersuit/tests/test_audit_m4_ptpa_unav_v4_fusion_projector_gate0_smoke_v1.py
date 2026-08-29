"""新 fusion projector gate0 结果审计器测试。"""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / 'harness/artifacts/m4_ptpa_unav_v4_fusion_projector_gate0_smoke_v1'

def test_audit_real_smoke_passes(tmp_path):
    out = tmp_path / 'audit.json'
    subprocess.run([sys.executable, str(ROOT/'scripts/audit_m4_ptpa_unav_v4_fusion_projector_gate0_smoke_v1.py'), '--root', str(ART), '--output', str(out)], check=True)
    result = json.loads(out.read_text())
    assert result['status'] == 'PASS_FUSION_PROJECTOR_GATE0_EXACT_ROLLBACK'
    assert all(result['checks'].values())
    assert result['m4_effect_claim_allowed'] is False
