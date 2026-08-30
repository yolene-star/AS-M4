from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs/audio_confidence_p3_feature_smoke_v1.json"


def test_feature_smoke_contract_locks_two_records_and_keeps_training_closed():
    contract = json.loads(CONTRACT.read_text())
    scope = contract["feature_precompute_scope"]
    assert contract["status"] == "PASS_CPU_CONTRACT_GPU_SMOKE_AUTHORIZED"
    assert contract["base_code_commit"] == "b0724b5"
    assert contract["authorization"]["feature_precompute"] is True
    assert contract["authorization"]["two_step_training"] is False
    assert scope["physical_gpu"] == 2
    assert scope["cuda_visible_devices"] == "2"
    assert scope["max_records"] == 2
    assert len(scope["selected_record_ids"]) == 2
    assert scope["selected_modalities"] == ["visual", "audio"]
    assert scope["overwrite_existing"] is False
    assert "independent_feature_audit" in contract["required_evidence"]


def test_feature_smoke_contract_records_match_locked_p2_manifest():
    contract = json.loads(CONTRACT.read_text())
    p2_path = (
        ROOT
        / "datasets/VALOR32K_AVQA_V2_AUDIT/p2/as_m4_cgf_valor32k_p2_conditions_v1.json"
    )
    rows = json.loads(p2_path.read_text())
    selected = {
        row["record_id"]: row for row in rows
        if row["record_id"] in contract["feature_precompute_scope"]["selected_record_ids"]
    }
    assert list(selected) == contract["feature_precompute_scope"]["selected_record_ids"]
    assert [selected[key]["modality"] for key in selected] == ["visual", "audio"]
    assert all(selected[key]["condition"] == "clean_correct" for key in selected)
    assert all(selected[key]["first_stage_training"] is True for key in selected)
    assert all(selected[key]["target_stream_window_count"] == 9 for key in selected)
