#!/usr/bin/env python3
"""构建 M4-PTPA 音频语义/答案/融合重构 V1 的 S0 basis lock。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
ARTIFACT_ROOT = INTERSUIT_ROOT / "harness/artifacts"
DEFAULT_OUTPUT = ARTIFACT_ROOT / "m4_ptpa_audio_semantic_fusion_v1/s0_basis"
SELECTOR_REVIEW = (
    ARTIFACT_ROOT
    / "m4_ptpa_audio_dynamic_selector_v3/p3_longvale_frozen100/official_metrics/p3_full_review.json"
)
LEGACY_BASIS = INTERSUIT_ROOT / "configs/audio_semantic_reconstruction_basis_lock.json"
LEGACY_R0_LOCK = ARTIFACT_ROOT / "audio_semantic_reconstruction_r0_contract/manifest_lock.json"
LEGACY_R2_DECISION = ARTIFACT_ROOT / "audio_semantic_reconstruction_r2_targets/decision.json"
LEGACY_R2_METRICS = ARTIFACT_ROOT / "audio_semantic_reconstruction_r2_targets/metrics.json"
LEGACY_TEACHER_LOCK = ARTIFACT_ROOT / "audio_semantic_teacher_t0_v2/teacher_qualification_lock.json"
LEGACY_TEACHER_DECISION = ARTIFACT_ROOT / "audio_semantic_teacher_t0_v2/decision.json"
EXPECTED_SHA256 = {
    SELECTOR_REVIEW: "6f1291e204d4697e2458ee8107343244ba595a3c553befeb0cfc6e0e18de43fd",
    LEGACY_BASIS: "f9550c93e85ce9652389ed241ee07bb4c9994f5fc449cdb92005fa8a8ecdd48e",
    LEGACY_R0_LOCK: "d1a1a02f49f739139a3033808ccf69131083d5cbd37ecd13393400ee475d1729",
    LEGACY_R2_DECISION: "15b7e74cac13cf49c924d2d1f9f29cb56a4b5ebad9d628cb355d6f90b7fa36c8",
    LEGACY_R2_METRICS: "f9d699bf52ebcf7567559c8f67060c86593f16f0abce726d9bf6d234bf5427ed",
    LEGACY_TEACHER_LOCK: "d5d7523e7170a368d203d2aad3d3a78711f97972475511b2feb3300e47490ce9",
    LEGACY_TEACHER_DECISION: "6a1b5d089ce971522de72b539a234e55db3243b1565b545f0cfb85ad8ca8a392",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify_inputs() -> dict[str, str]:
    actual = {}
    for path, expected in EXPECTED_SHA256.items():
        digest = sha256(path)
        if digest != expected:
            raise ValueError(f"S0 输入 SHA 漂移: {path}; expected={expected}; actual={digest}")
        actual[str(path.relative_to(PROJECT_ROOT))] = digest
    selector = read_json(SELECTOR_REVIEW)
    if (
        selector.get("status") != "stop_selector_route"
        or selector.get("final_oracle_gate_passed") is not False
        or selector.get("selector_route_stopped") is not True
        or selector.get("selector_training_allowed") is not False
        or selector.get("confirm_accessed") is not False
    ):
        raise ValueError("selector 最终失败权限合同漂移")
    legacy_r2 = read_json(LEGACY_R2_DECISION)
    if legacy_r2.get("status") != "FAIL" or legacy_r2.get("passing_legal_targets") != []:
        raise ValueError("旧 R2 反证合同漂移")
    teacher = read_json(LEGACY_TEACHER_DECISION)
    if teacher.get("status") != "PASS" or teacher.get("validation_accessed") is not False:
        raise ValueError("旧 teacher 开发资格合同漂移")
    return actual


def build_lock(input_hashes: dict[str, str]) -> dict[str, Any]:
    return {
        "artifact_kind": "m4_ptpa_audio_semantic_fusion_v1_s0_basis_lock",
        "format_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "route": "audio_semantic_fusion_reconstruction_v1",
        "base_model": "M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1",
        "status": "s0_audit_authorized",
        "input_hashes": input_hashes,
        "historical_asset_classification": {
            "selector_p3_full": "final_negative_evidence",
            "legacy_r0_data_contract": "read_only_basis_requires_reaudit",
            "legacy_r2_text_targets": "failed_negative_evidence_do_not_repackage",
            "legacy_teacher_t0_v2": "development_candidate_not_new_confirm",
        },
        "frozen_primary_order": [
            "semantic_target",
            "answer_representation",
            "fusion_position",
            "temporal_target",
            "single_module_upper_bounds",
            "joint_training_last",
        ],
        "permissions": {
            "s0_read_only_audit_allowed": True,
            "s1_manifest_construction_allowed": False,
            "gpu_execution_allowed": False,
            "feature_extraction_allowed": False,
            "semantic_target_training_allowed": False,
            "answer_representation_training_allowed": False,
            "fusion_training_allowed": False,
            "joint_training_allowed": False,
            "selector_training_allowed": False,
            "deterministic_selector_allowed": False,
            "confirm_accessed": False,
            "formal_test_accessed": False,
        },
        "known_blockers": [
            "missing_complete_historical_split_access_ledger",
            "missing_physical_media_overlap_matrix",
            "hard_negative_label_conflict_not_audited",
            "teacher_license_and_normalization_not_locked",
            "new_internal_confirm_eligibility_not_proven",
            "s1_feature_manifest_not_locked",
        ],
        "next_stage": "s0_data_access_negative_contract_audit",
        "s0_audit_continuation_allowed": True,
        "next_stage_allowed": False,
        "selector_route_must_remain_stopped": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    input_hashes = verify_inputs()
    lock = build_lock(input_hashes)
    write_json(args.output_root / "s0_basis_lock.json", lock)
    review = {
        "artifact_kind": "m4_ptpa_audio_semantic_fusion_v1_s0_basis_review",
        "status": "pass_for_s0_only",
        "blockers": lock["known_blockers"],
        "majors": [],
        "minors": [],
        "evidence": {"input_hashes": input_hashes},
        "next_stage": lock["next_stage"],
        "s0_audit_continuation_allowed": True,
        "next_stage_allowed": False,
        "gpu_execution_allowed": False,
        "training_allowed": False,
        "confirm_accessed": False,
        "formal_test_accessed": False,
    }
    write_json(args.output_root / "s0_basis_review.json", review)
    print(json.dumps({"status": review["status"], "output_root": str(args.output_root)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
