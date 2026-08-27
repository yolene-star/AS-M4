"""UnAV S1-R强时间语义重构计划合同测试。"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs/m4_ptpa_unav_s1r_plan_v1.json"


def load_contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_historical_llp_s1_fail_is_immutable():
    historical = load_contract()["historical_s1"]

    assert historical["status"] == "FAIL_UNCHANGED"
    assert historical["must_not_relabel_as_pass"] is True
    assert historical["must_not_change_thresholds"] is True


def test_unav_split_roles_keep_validation_once_and_test_sealed():
    roles = load_contract()["data_roles"]

    assert roles["train"]["role"].startswith("development_")
    assert roles["validation"]["effect_access_once"] is True
    assert roles["validation"]["currently_effect_locked"] is True
    assert roles["test"]["media_extraction_allowed"] is False
    assert roles["test"]["feature_extraction_allowed"] is False
    assert roles["test"]["effect_access_allowed"] is False


def test_strong_timing_and_negative_contract_prevent_label_leakage():
    contract = load_contract()
    timing = contract["strong_timing_contract"]

    assert timing["overlapping_events_are_multilabel"] is True
    assert timing["overlapping_valid_event_must_not_be_used_as_negative"] is True
    assert timing["same_video_negative_requires_no_active_official_event"] is True
    assert timing["clip_label_promotion_to_all_windows_forbidden"] is True
    assert contract["negative_contract"]["mismatch"].endswith("different_canonical_label")


def test_s1r_gates_and_permissions_are_fail_closed():
    contract = load_contract()
    gates = contract["gates"]
    permissions = contract["permissions"]

    assert gates["macro_recall_at_1_min"] == 0.21
    assert gates["balanced_accuracy_min"] == 0.21
    assert gates["margin_ci95_lower_strictly_positive"] is True
    assert gates["correct_above_mismatch"] is True
    assert gates["test_effect_accessed"] is False
    assert all(value is False for value in permissions.values())
    assert contract["next_stage_allowed"] is False
