"""v4非零gate极小诊断合同测试。"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "harness/configs/m4_ptpa_unav_v4_nonzero_gate_diagnostic_v1.json"


def _load() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_nonzero_contract_is_small_dev_only_and_no_training():
    contract = _load()
    permissions = contract["permissions"]

    assert contract["dataset"]["sample_limit"] == 10
    assert contract["dataset"]["test_accessed"] is False
    assert permissions["nonzero_gate_diagnostic_allowed"] is True
    assert permissions["full_dev_allowed"] is False
    assert permissions["formal_test_access_allowed"] is False
    assert permissions["training_allowed"] is False
    assert permissions["beats_unfreeze_allowed"] is False


def test_correct_and_mismatch_have_identical_locked_runtime():
    contract = _load()
    correct, mismatch = contract["experiments"][1:]

    assert correct["audio_condition"] == "correct"
    assert mismatch["audio_condition"] == "mismatched"
    assert correct["env"] == mismatch["env"]
    assert correct["env"]["AS_M4_INFERENCE_SIMPLE_AUDIO_GATE"] == "0.005"
    assert correct["env"]["AS_M4_AUDIO_DELTA_RATIO_CAP"] == "0.05"
    assert correct["env"]["AS_M4_SCENE_AUDIO_V4_ADAPTER_SOURCE"] == "existing_audio_projector"


def test_stop_gates_and_claims_fail_closed():
    contract = _load()
    gates = contract["stop_gates"]
    claims = contract["claim_boundary"]

    assert gates["empty_prediction_count_max"] == 0
    assert gates["first_token_eos_count_max"] == 0
    assert gates["nonfinite_diagnostic_count_max"] == 0
    assert gates["delta_to_video_ratio_max"] == 0.05
    assert gates["frozen_runtime_sha_unchanged_required"] is True
    assert claims["diagnostic_only"] is True
    assert claims["m4_effect_claim_allowed"] is False
    assert claims["training_next_stage_automatically_allowed"] is False
