from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from intersuit.train.audio_semantic_m4_adapter import summarize_counterfactual_nll


SCRIPT = Path(__file__).parents[1] / "scripts/compare_audio_semantic_m4_adapter_evals.py"
SPEC = importlib.util.spec_from_file_location("compare_adapter_evals", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_compare_adapter_evals_executes_registered_gate(tmp_path):
    pre_rows=[{'pair_group_id':'g','condition_nll':{'video_only':2.,'correct':2.2,'mismatch':2.1,'silence':2.2}}]
    post_rows=[{'pair_group_id':'g','condition_nll':{'video_only':2.,'correct':1.5,'mismatch':2.2,'silence':2.}}]
    baseline={'evaluation_kind':'initialization','manifest_sha256':'m','adapter_sha256_before':'a','metrics':summarize_counterfactual_nll(pre_rows)}
    post={'evaluation_kind':'checkpoint','manifest_sha256':'m','adapter_sha256_before':'b','checkpoint_sha256':'c','metrics':summarize_counterfactual_nll(post_rows)}
    thresholds={
        'post_ordering_accuracy_min':1.,'ordering_accuracy_delta_min':1.,
        'post_mismatch_margin_mean_min':.7,'mismatch_margin_mean_delta_min':.8,
        'correct_vs_video_accuracy_delta_min':1.,'video_benefit_mean_delta_min':.7,
        'correct_mean_nll_reduction_min':.7,'post_silence_abs_gap_max':0.,
        'silence_abs_gap_delta_max':0.,
    }
    contract={'evaluation':{'group_count':1,'thresholds':thresholds}}
    bp=tmp_path/'b.json'; pp=tmp_path/'p.json'; cp=tmp_path/'c.json'
    bp.write_text(json.dumps(baseline),encoding='utf-8'); pp.write_text(json.dumps(post),encoding='utf-8'); cp.write_text(json.dumps(contract),encoding='utf-8')
    result=MODULE.compare_evaluations(baseline_summary=bp,post_summary=pp,contract_path=cp)
    assert result['status']=='PASS_ADAPTER_CANARY20_METRICS' and all(result['checks'].values())
