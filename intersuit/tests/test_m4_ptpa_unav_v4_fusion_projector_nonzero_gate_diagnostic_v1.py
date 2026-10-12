"""新 fusion projector 非零 gate 诊断合同测试。"""
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'harness/configs/m4_ptpa_unav_v4_fusion_projector_nonzero_gate_diagnostic_v1.json'
def test_contract_is_small_and_fail_closed():
    c=json.loads(CONFIG.read_text())
    assert c['sample_limit']==10
    assert c['gate']==0.005
    assert c['delta_ratio_cap']==0.05
    assert c['permissions']['nonzero_gate_diagnostic_allowed'] is False
    assert c['permissions']['training_allowed'] is False
    assert c['permissions']['formal_test_allowed'] is False
def test_contract_covers_both_new_modes_and_pairing():
    c=json.loads(CONFIG.read_text())
    assert c['conditions']==['behavior_off','bypass_correct','bypass_mismatch','lightweight_correct','lightweight_mismatch']
    assert c['fixed_sources']['adapter_source']=='existing_audio_projector'
    assert c['stop_gates']['mismatch_unique_improvement_must_not_exceed_correct'] is True
