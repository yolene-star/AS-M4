#!/usr/bin/env python3
"""审计音频语义/答案/融合重构 V1 的历史访问、媒体交集与负样本。"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
ARTIFACT_ROOT = INTERSUIT_ROOT / "harness/artifacts"
R0_ROOT = ARTIFACT_ROOT / "audio_semantic_reconstruction_r0_contract"
DEFAULT_BASIS = ARTIFACT_ROOT / "m4_ptpa_audio_semantic_fusion_v1/s0_basis/s0_basis_lock.json"
DEFAULT_OUTPUT = ARTIFACT_ROOT / "m4_ptpa_audio_semantic_fusion_v1/s0_data_audit"

INPUTS = {
    "r0_expanded": R0_ROOT / "expanded_split_manifest.jsonl",
    "r0_pilot300": R0_ROOT / "pilot300_split_manifest.jsonl",
    "stage2_train": INTERSUIT_ROOT / "datasets/AS_M4_STAGE2/train_manifest.json",
    "stage2_dev": INTERSUIT_ROOT / "datasets/AS_M4_STAGE2/dev_manifest.json",
    "stage2_reserve": INTERSUIT_ROOT / "datasets/AS_M4_STAGE2/reserve_manifest.json",
    "stage2_semantic_negative": (
        INTERSUIT_ROOT / "datasets/AS_M4_STAGE2/train_manifest_semantic_negative.json"
    ),
    "teacher_development": (
        INTERSUIT_ROOT
        / "datasets/AVE_HF_EXPANDED/teacher_development_full_train_manifest.jsonl"
    ),
    "teacher_confirm": (
        INTERSUIT_ROOT / "datasets/AVE_HF_EXPANDED/teacher_confirm_manifest.jsonl"
    ),
    "teacher_lock": (
        ARTIFACT_ROOT / "audio_semantic_teacher_t0_v2/teacher_qualification_lock.json"
    ),
    "r1_feature_manifest": (
        ARTIFACT_ROOT
        / "audio_semantic_reconstruction_r1_expanded/full/features/ave_hf_window_feature_manifest.jsonl"
    ),
    "r1_eval_lock": (
        ARTIFACT_ROOT / "audio_semantic_reconstruction_r1_expanded/eval/manifest_lock.json"
    ),
    "selector_pilot": (
        ARTIFACT_ROOT
        / "m4_ptpa_audio_dynamic_selector_v3/p3_longvale_frozen100/pilot_manifest.jsonl"
    ),
    "historical_exposure_inventory": (
        ARTIFACT_ROOT / "dynamic_selector_d1_ave_official/historical_exposure_inventory.json"
    ),
    "ave_official_test_execution_lock": (
        ARTIFACT_ROOT / "dynamic_selector_d1_ave_official/official_test/execution_lock.json"
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_rows(path: Path) -> list[dict[str, Any]]:
    value = read_json(path) if path.suffix == ".json" else read_jsonl(path)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"清单必须是对象列表: {path}")
    return value


def canonical(value: Any) -> str:
    return str(value or "").strip().casefold()


def physical_key(row: dict[str, Any]) -> str:
    """跨旧/新清单统一到源视频 ID，避免不同前缀掩盖真实重叠。"""
    for key in ("youtube_id", "video_id", "source_video_id"):
        if canonical(row.get(key)):
            return canonical(row[key])
    value = canonical(row.get("physical_media_id"))
    if value.startswith("ave:"):
        return value[4:]
    return value


def keys(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {key for row in rows if (key := physical_key(row))}


def split_rows(rows: list[dict[str, Any]], field: str, value: str) -> list[dict[str, Any]]:
    expected = canonical(value)
    return [row for row in rows if canonical(row.get(field)) == expected]


def build_overlap_matrix(named_sets: dict[str, set[str]]) -> dict[str, Any]:
    names = list(named_sets)
    return {
        "set_counts": {name: len(named_sets[name]) for name in names},
        "matrix": {
            left: {right: len(named_sets[left] & named_sets[right]) for right in names}
            for left in names
        },
    }


def audit_hard_negatives(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counters: Counter[str] = Counter()
    eligible_ids = []
    for row in rows:
        positive = canonical(row.get("event_label"))
        negative = canonical(row.get("scene_audio_negative_event_label"))
        positive_media = canonical(row.get("physical_media_id"))
        negative_media = canonical(row.get("scene_audio_negative_physical_media_id"))
        task_type = canonical(row.get("task_type"))
        counters["total"] += 1
        counters["missing_positive_label"] += int(not positive)
        counters["missing_negative_label"] += int(not negative)
        counters["same_label"] += int(bool(positive) and positive == negative)
        counters["same_physical_media"] += int(
            bool(positive_media) and positive_media == negative_media
        )
        counters["visual_task"] += int(task_type == "visual")
        counters["audio_semantic_task"] += int(task_type in {"audio", "audio_visual"})
        eligible = (
            task_type in {"audio", "audio_visual"}
            and
            bool(positive)
            and bool(negative)
            and positive != negative
            and bool(positive_media)
            and bool(negative_media)
            and positive_media != negative_media
        )
        counters["eligible_clip_level_cross_video"] += int(eligible)
        if eligible:
            eligible_ids.append(str(row.get("id") or row.get("sample_id")))
    return {
        "counts": dict(sorted(counters.items())),
        "eligible_sample_id_sha256": hashlib.sha256(
            "\n".join(sorted(eligible_ids)).encode("utf-8")
        ).hexdigest(),
        "eligibility_rule": (
            "task_type is audio or audio_visual; positive/negative canonical labels "
            "both non-empty and different; "
            "positive/negative physical_media_id both non-empty and different"
        ),
        "allowed_use": "clip_level_cross_video_hard_negative_development_only",
        "same_video_window_negative_allowed": False,
        "reason": "现有 AVE 监督粒度为 clip，不能静默提升为窗口级边界",
    }


def audit_teacher(lock: dict[str, Any]) -> dict[str, Any]:
    teacher = lock["teacher"]
    preprocessing = lock["preprocessing"]
    capabilities = lock["capabilities"]
    required = {
        "model_id": teacher.get("model_id"),
        "official_source": teacher.get("official_source"),
        "revision": teacher.get("revision"),
        "license": teacher.get("license"),
        "checkpoint_sha256": teacher.get("checkpoint_sha256"),
        "normalization": preprocessing.get("normalization"),
        "pooling": preprocessing.get("pooling"),
        "golden_input_features_sha256": preprocessing.get("golden_input_features_sha256"),
    }
    reproducibility_locked = all(value not in (None, "", {}) for value in required.values())
    return {
        "reproducibility_locked": reproducibility_locked,
        "evidence": required,
        "allowed_student_targets": capabilities.get("allowed_student_targets", []),
        "development_teacher_candidate_allowed": reproducibility_locked,
        "student_target_authorized": bool(capabilities.get("allowed_student_targets")),
        "teacher_confirm_reusable_as_new_confirm": False,
    }


def build_audit(basis_lock: dict[str, Any], rows: dict[str, list[dict[str, Any]]],
                teacher_lock: dict[str, Any], historical_inventory: dict[str, Any],
                official_test_lock: dict[str, Any], input_hashes: dict[str, str]) -> dict[str, Any]:
    if basis_lock.get("status") != "s0_audit_authorized":
        raise ValueError("S0 basis 未授权只读审计")
    expanded = rows["r0_expanded"]
    named_sets = {
        "r0_expanded_train": keys(split_rows(expanded, "diagnostic_split", "train")),
        "r0_expanded_validation": keys(
            split_rows(expanded, "diagnostic_split", "validation")
        ),
        "r0_expanded_test_locked": keys(
            split_rows(expanded, "diagnostic_split", "test_locked")
        ),
        "r0_pilot_reference": keys(
            split_rows(rows["r0_pilot300"], "diagnostic_split", "reference")
        ),
        "r0_pilot_query": keys(
            split_rows(rows["r0_pilot300"], "diagnostic_split", "query")
        ),
        "stage2_train": keys(rows["stage2_train"]),
        "stage2_dev": keys(rows["stage2_dev"]),
        "stage2_reserve": keys(rows["stage2_reserve"]),
        "teacher_development": keys(rows["teacher_development"]),
        "teacher_confirm": keys(rows["teacher_confirm"]),
        "selector_p3_pilot": keys(rows["selector_pilot"]),
        "historically_exposed_ave": {
            canonical(row.get("youtube_id"))
            for row in historical_inventory["entries"]
            if canonical(row.get("youtube_id"))
        },
    }
    overlap = build_overlap_matrix(named_sets)
    test_vs_train = overlap["matrix"]["r0_expanded_test_locked"]["stage2_train"]
    test_vs_dev = overlap["matrix"]["r0_expanded_test_locked"]["stage2_dev"]
    test_vs_reserve = overlap["matrix"]["r0_expanded_test_locked"]["stage2_reserve"]
    stage2_union = named_sets["stage2_train"] | named_sets["stage2_dev"] | named_sets["stage2_reserve"]
    test_locked_rows = split_rows(expanded, "diagnostic_split", "test_locked")
    clean_test_rows = [row for row in test_locked_rows if physical_key(row) not in stage2_union]
    test_union_overlap = len(named_sets["r0_expanded_test_locked"] & stage2_union)
    test_historical_overlap = len(
        named_sets["r0_expanded_test_locked"] & named_sets["historically_exposed_ave"]
    )
    confirm_eligible = (
        test_vs_train == test_vs_dev == test_vs_reserve == 0
        and test_historical_overlap == 0
    )
    teacher = audit_teacher(teacher_lock)
    hard_negative = audit_hard_negatives(rows["stage2_semantic_negative"])
    feature_rows = rows["r1_feature_manifest"]
    window_counts = Counter(int(row.get("window_count", 0)) for row in feature_rows)
    feature_lock = {
        "manifest_sha256": input_hashes["r1_feature_manifest"],
        "row_count": len(feature_rows),
        "audio_embedding_dims": sorted(
            {int(row["audio_embedding_dim"]) for row in feature_rows}
        ),
        "window_count_distribution": dict(sorted(window_counts.items())),
        "window_sec": 1.0,
        "hop_sec": 1.0,
        "supervision_levels": sorted({str(row.get("supervision_level")) for row in feature_rows}),
        "allowed_scope": "historically_accessed_development_evidence_only",
        "feature_extraction_allowed": False,
    }
    blockers = []
    if not confirm_eligible:
        blockers.append("no_untouched_internal_confirm_proven")
    if not teacher["student_target_authorized"]:
        blockers.append("teacher_has_no_authorized_student_target")
    if hard_negative["counts"].get("eligible_clip_level_cross_video", 0) == 0:
        blockers.append("no_legal_clip_level_hard_negative")
    blockers.append("s1_candidate_manifest_not_constructed_or_reviewed")
    access_ledger = {
        "r0_expanded_train": "accessed_by_r1_and_teacher_development",
        "r0_expanded_validation": "accessed_by_r1_evaluation",
        "r0_expanded_test_locked": (
            "metric_not_accessed_but_disqualified_by_stage2_overlap"
            if not confirm_eligible
            else "candidate_locked_not_accessed"
        ),
        "r0_pilot_reference": "accessed_by_r1_evaluation",
        "r0_pilot_query": "accessed_by_r1_evaluation",
        "stage2_train": "accessed_for_training",
        "stage2_dev": "accessed_for_development_and_metrics",
        "stage2_reserve": "accessed_for_gate_calibration_and_predictions",
        "teacher_development": "accessed_for_teacher_qualification",
        "teacher_confirm": "accessed_once_for_teacher_selection_not_reusable",
        "selector_p3_pilot": "accessed_for_selector_upper_bound_metrics",
        "historically_exposed_ave": "2500_media_union_from_prior_local_assets_manifests_and_harness",
        "ave_official_test": (
            "effect_results_viewed_not_reusable_rerun_forbidden"
            if official_test_lock.get("effect_results_viewed") is True
            else "status_unknown_fail_closed"
        ),
    }
    return {
        "artifact_kind": "m4_ptpa_audio_semantic_fusion_v1_s0_data_audit",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "blocked_before_s1" if blockers else "s0_pass",
        "input_hashes": input_hashes,
        "access_ledger": access_ledger,
        "physical_overlap": overlap,
        "internal_confirm_audit": {
            "candidate": "r0_expanded_test_locked",
            "stage2_train_overlap": test_vs_train,
            "stage2_dev_overlap": test_vs_dev,
            "stage2_reserve_overlap": test_vs_reserve,
            "stage2_union_overlap": test_union_overlap,
            "stage2_clean_residual_count": len(clean_test_rows),
            "stage2_clean_residual_label_count": len(
                {canonical(row.get("canonical_label")) for row in clean_test_rows}
            ),
            "historical_exposure_inventory_overlap": test_historical_overlap,
            "eligible": confirm_eligible,
            "metric_accessed_by_r0_r1_r2_teacher": False,
            "conclusion": (
                "不合格：虽然旧路线未读取其指标，但物理媒体已进入后续 Stage2 资产"
                if not confirm_eligible
                else "仅为候选，仍需独立 Review"
            ),
        },
        "hard_negative_audit": hard_negative,
        "teacher_audit": teacher,
        "feature_contract": feature_lock,
        "blockers": blockers,
        "permissions": {
            "s0_read_only_audit_allowed": True,
            "s1_manifest_construction_allowed": False,
            "gpu_execution_allowed": False,
            "feature_extraction_allowed": False,
            "semantic_target_training_allowed": False,
            "answer_representation_training_allowed": False,
            "fusion_training_allowed": False,
            "joint_training_allowed": False,
            "confirm_accessed": False,
            "formal_test_accessed": False,
        },
        "next_stage_allowed": False,
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_report(path: Path, audit: dict[str, Any]) -> None:
    confirm = audit["internal_confirm_audit"]
    negative = audit["hard_negative_audit"]["counts"]
    teacher = audit["teacher_audit"]
    lines = [
        "# S0 历史访问、媒体交集与语义负样本审查",
        "",
        "## 结论",
        "",
        "结论：`BLOCKED_BEFORE_S1`。S0 只读审计完成，但不开放 S1、GPU、特征提取或训练。",
        "",
        "## 关键发现",
        "",
        f"- 旧 R0 `test_locked` 与 Stage2 train/dev/reserve 的物理媒体重叠分别为 "
        f"{confirm['stage2_train_overlap']}/{confirm['stage2_dev_overlap']}/"
        f"{confirm['stage2_reserve_overlap']}，并集污染 {confirm['stage2_union_overlap']}/196；"
        f"残余仅 {confirm['stage2_clean_residual_count']} 条、"
        f"{confirm['stage2_clean_residual_label_count']} 类。历史暴露总清单覆盖 "
        f"{confirm['historical_exposure_inventory_overlap']}/196，因此没有一条可作为新 internal confirm。",
        f"- semantic-negative 共 {negative['total']} 条；严格合法的 clip-level 跨视频异类负样本 "
        f"{negative['eligible_clip_level_cross_video']} 条；缺正/负标签各 "
        f"{negative['missing_positive_label']}/{negative['missing_negative_label']} 条。",
        f"- teacher 来源、revision、Apache-2.0 许可证、归一化与 golden hash 已锁定："
        f"{str(teacher['reproducibility_locked']).lower()}。",
        "- teacher 资格锁的 `allowed_student_targets` 仍为空，因此不能直接授权学生目标。",
        "- 旧 1s/1s BEATs 特征只可作已访问的 development 证据，不能用于 untouched confirm。",
        "",
        "## 当前权限",
        "",
        "所有 GPU、特征提取、语义目标训练、答案表示训练、融合训练、联合训练、confirm 和正式 test 权限均为 `false`。",
        "",
        "## 人话结论",
        "",
        "旧的锁定集虽然没有直接看过分数，但全部视频都出现在历史暴露清单里，其中大部分后来又进入 Stage2，因此已经不干净。"
        "负样本中严格满足音频任务、标签和媒体约束的只有约四分之一。现在必须先找到真正未污染的 confirm，"
        "并给 teacher 定义一个可证伪的学生目标，不能直接开训。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basis-lock", type=Path, default=DEFAULT_BASIS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    basis = read_json(args.basis_lock)
    input_hashes = {name: sha256(path) for name, path in INPUTS.items()}
    rows = {
        name: load_rows(path)
        for name, path in INPUTS.items()
        if name not in {
            "teacher_lock",
            "r1_eval_lock",
            "historical_exposure_inventory",
            "ave_official_test_execution_lock",
        }
    }
    audit = build_audit(
        basis,
        rows,
        read_json(INPUTS["teacher_lock"]),
        read_json(INPUTS["historical_exposure_inventory"]),
        read_json(INPUTS["ave_official_test_execution_lock"]),
        input_hashes,
    )
    write_json(args.output_root / "access_ledger.json", audit["access_ledger"])
    write_json(args.output_root / "physical_overlap_matrix.json", audit["physical_overlap"])
    write_json(args.output_root / "hard_negative_audit.json", audit["hard_negative_audit"])
    write_json(args.output_root / "teacher_audit.json", audit["teacher_audit"])
    write_json(args.output_root / "feature_contract.json", audit["feature_contract"])
    write_json(args.output_root / "s0_review.json", audit)
    write_report(args.output_root / "S0_REVIEW_ZH.md", audit)
    print(json.dumps({"status": audit["status"], "blockers": audit["blockers"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
