import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/'configs/m4_ptpa_unav_v4_fusion_projector_nonzero_gate_result_lock_v1.json'
def test_new_fusion_nonzero_result_stops_expansion():
    lock=json.loads(LOCK.read_text())
    assert lock['status']=='PASS_TECHNICAL_NO_SELECTIVE_TASK_GAIN_STOP_EXPANSION'
    for mode in lock['modes']:
        result=lock['results'][mode]
        assert result['accuracy']=={'behavior':3,'correct':3,'mismatch':3}
        assert result['unique_improvements']=={'correct':0,'mismatch':0}
    assert all(lock['permissions'].values()) is False
