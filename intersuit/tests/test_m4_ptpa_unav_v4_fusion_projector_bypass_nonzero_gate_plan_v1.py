import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PLAN=ROOT/'harness/configs/m4_ptpa_unav_v4_fusion_projector_bypass_nonzero_gate_plan_v1.jsonl'
def test_bypass_plan_is_nonzero_and_paired():
    rows=[json.loads(x) for x in PLAN.read_text().splitlines()]
    correct,mismatch=rows[1:]
    assert correct['env']==mismatch['env']
    assert 'AS_M4_FORCE_AUDIO_GATE' not in correct['env']
    assert correct['env']['AS_M4_INFERENCE_SIMPLE_AUDIO_GATE']=='0.005'
    assert correct['env']['AS_M4_FUSION_PROJECTOR_MODE']=='bypass'
    assert correct['audio_condition']=='correct' and mismatch['audio_condition']=='mismatched'
