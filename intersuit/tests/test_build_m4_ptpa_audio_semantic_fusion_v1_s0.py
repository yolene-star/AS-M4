from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_m4_ptpa_audio_semantic_fusion_v1_s0.py"
SPEC = importlib.util.spec_from_file_location("build_m4_ptpa_audio_semantic_fusion_v1_s0", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_s0_lock_is_fail_closed_outside_read_only_audit():
    lock = module.build_lock({"evidence": "sha"})

    assert lock["status"] == "s0_audit_authorized"
    assert lock["s0_audit_continuation_allowed"] is True
    assert lock["next_stage_allowed"] is False
    assert lock["permissions"]["s0_read_only_audit_allowed"] is True
    assert lock["permissions"]["s1_manifest_construction_allowed"] is False
    assert lock["permissions"]["gpu_execution_allowed"] is False
    assert lock["permissions"]["feature_extraction_allowed"] is False
    assert lock["permissions"]["semantic_target_training_allowed"] is False
    assert lock["permissions"]["answer_representation_training_allowed"] is False
    assert lock["permissions"]["fusion_training_allowed"] is False
    assert lock["permissions"]["joint_training_allowed"] is False
    assert lock["permissions"]["selector_training_allowed"] is False
    assert lock["permissions"]["confirm_accessed"] is False
    assert lock["permissions"]["formal_test_accessed"] is False


def test_historical_failures_are_not_repackaged_as_candidates():
    lock = module.build_lock({"evidence": "sha"})
    assets = lock["historical_asset_classification"]

    assert assets["selector_p3_full"] == "final_negative_evidence"
    assert assets["legacy_r2_text_targets"] == "failed_negative_evidence_do_not_repackage"
    assert assets["legacy_teacher_t0_v2"] == "development_candidate_not_new_confirm"


def test_joint_training_is_last_in_frozen_priority_order():
    lock = module.build_lock({"evidence": "sha"})

    assert lock["frozen_primary_order"][-1] == "joint_training_last"
    assert lock["frozen_primary_order"][:3] == [
        "semantic_target",
        "answer_representation",
        "fusion_position",
    ]
