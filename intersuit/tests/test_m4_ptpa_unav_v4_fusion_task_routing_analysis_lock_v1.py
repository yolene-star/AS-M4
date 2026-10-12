import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/'configs/m4_ptpa_unav_v4_fusion_task_routing_analysis_lock_v1.json'
def test_task_routing_analysis_stops_blind_expansion():
    lock=json.loads(LOCK.read_text())
    assert lock['status']=='FIXED_RESIDUAL_LACKS_QUERY_ROUTING'
    assert lock['evidence']['audio_task_prediction_changes_bypass']==0
    assert lock['evidence']['audio_task_prediction_changes_lightweight']==0
    assert lock['evidence']['lightweight_to_bypass_residual_ratio']>30
    assert lock['code_path']['question_features_computed_for_simple_residual'] is False
    assert lock['diagnosis']['missing_query_task_routing_supported'] is True
    assert lock['permissions']['query_conditioned_cpu_plan_allowed'] is True
    assert lock['permissions']['training_allowed'] is False
