#!/usr/bin/env python3
"""只读审计 M4-PTPA 音频路线 S0.5 分阶段 internal confirm 候选。"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
ARTIFACT_ROOT = INTERSUIT_ROOT / "harness/artifacts"
DATASET_ROOT = INTERSUIT_ROOT / "datasets"
DEFAULT_OUTPUT = (
    ARTIFACT_ROOT / "m4_ptpa_audio_semantic_fusion_v1/s05_confirm_candidate_audit"
)

INPUTS = {
    "s0_review": (
        ARTIFACT_ROOT
        / "m4_ptpa_audio_semantic_fusion_v1/s0_data_audit/s0_review.json"
    ),
    "ave_manifest_lock": (
        ARTIFACT_ROOT / "dynamic_selector_d1_ave_official/manifest_lock.json"
    ),
    "ave_historical_exposure": (
        ARTIFACT_ROOT / "dynamic_selector_d1_ave_official/historical_exposure_inventory.json"
    ),
    "ave_train_candidates": (
        ARTIFACT_ROOT / "dynamic_selector_d1_ave_official/train_candidate_manifest.jsonl"
    ),
    "ave_validation_candidates": (
        ARTIFACT_ROOT / "dynamic_selector_d1_ave_official/validation_candidate_manifest.jsonl"
    ),
    "ave_test_candidates": (
        ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_official/test_candidate_manifest.sealed.jsonl"
    ),
    "ave_media_lock": (
        ARTIFACT_ROOT / "dynamic_selector_d1_ave_official_media/media_lock.json"
    ),
    "ave_train_media": (
        ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_official_media/train_media_inventory.jsonl"
    ),
    "ave_label_mapping": (
        ARTIFACT_ROOT / "audio_semantic_reconstruction_r0_contract/label_mapping.json"
    ),
    "ave_validation_summary": (
        ARTIFACT_ROOT / "dynamic_selector_d1_ave_official/validation_summary.json"
    ),
    "ave_test_execution_lock": (
        ARTIFACT_ROOT / "dynamic_selector_d1_ave_official/official_test/execution_lock.json"
    ),
    "longvale_training_annotations": (
        DATASET_ROOT
        / "LongVALE_V2/official_metadata/longvale-annotations-training.json"
    ),
    "longvale_archive_index": DATASET_ROOT / "LongVALE_V2/archive_index.json",
    "longvale_pilot": (
        ARTIFACT_ROOT
        / "m4_ptpa_audio_dynamic_selector_v3/p3_longvale_frozen100/pilot_manifest.jsonl"
    ),
    "longvale_historical_dev": (
        ARTIFACT_ROOT / "dynamic_selector_v2_v2/preflight/oracle_dev.jsonl"
    ),
    "longvale_preflight_lock": (
        ARTIFACT_ROOT / "dynamic_selector_v2_v2/preflight/preflight_lock.json"
    ),
    "longvale_reserved_confirm": (
        ARTIFACT_ROOT
        / "m4_ptpa_audio_dynamic_selector_v3/p1_offline/confirm_one_sparse_event_locked_not_allowed.jsonl"
    ),
    "longvale_p1_lock": (
        ARTIFACT_ROOT / "m4_ptpa_audio_dynamic_selector_v3/p1_offline/p1_lock.json"
    ),
}

MIN_VIDEOS_PER_LABEL_PER_COHORT = 5
AVE_COHORTS = ("confirm_semantic", "confirm_answer")
MIN_LONGVALE_FUSION_VIDEOS = 100


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


def canonical(value: Any) -> str:
    return str(value or "").strip().casefold()


def unique_ids(rows: Iterable[dict[str, Any]], field: str) -> set[str]:
    values = [canonical(row.get(field)) for row in rows]
    if any(not value for value in values):
        raise ValueError(f"清单存在空 {field}")
    if len(values) != len(set(values)):
        raise ValueError(f"清单存在重复 {field}")
    return set(values)


def row_ids(rows: Iterable[dict[str, Any]], field: str) -> set[str]:
    values = [canonical(row.get(field)) for row in rows]
    if any(not value for value in values):
        raise ValueError(f"清单存在空 {field}")
    return set(values)


def candidate_labels(row: dict[str, Any]) -> set[str]:
    annotations = row.get("annotations")
    if not isinstance(annotations, list) or not annotations:
        raise ValueError(f"AVE 候选 {row.get('youtube_id')} 缺少 annotations")
    labels = {canonical(item.get("event_label")) for item in annotations}
    if not labels or "" in labels:
        raise ValueError(f"AVE 候选 {row.get('youtube_id')} 含空标签")
    return labels


def audit_ave(
    candidates: list[dict[str, Any]],
    media_rows: list[dict[str, Any]],
    expected_labels: list[str],
    historical_ids: set[str],
    validation_ids: set[str],
    test_ids: set[str],
) -> dict[str, Any]:
    candidate_ids = unique_ids(candidates, "youtube_id")
    media_ids = unique_ids(media_rows, "youtube_id")
    expected = {canonical(label) for label in expected_labels}
    display = {canonical(label): label for label in expected_labels}
    label_to_ids: dict[str, set[str]] = defaultdict(set)
    unknown_labels: set[str] = set()
    for row in candidates:
        youtube_id = canonical(row["youtube_id"])
        for label in candidate_labels(row):
            if label not in expected:
                unknown_labels.add(label)
            label_to_ids[label].add(youtube_id)
    media_failures = [
        row
        for row in media_rows
        if row.get("status") != "present"
        or not row.get("media_sha256")
        or int(row.get("media_size_bytes") or 0) <= 0
    ]
    counts = {display[label]: len(label_to_ids.get(label, set())) for label in sorted(expected)}
    required_per_label = MIN_VIDEOS_PER_LABEL_PER_COHORT * len(AVE_COHORTS)
    missing_labels = [label for label, count in counts.items() if count == 0]
    insufficient_labels = {
        label: count for label, count in counts.items() if count < required_per_label
    }
    return {
        "candidate_video_count": len(candidate_ids),
        "expected_label_count": len(expected),
        "covered_label_count": sum(count > 0 for count in counts.values()),
        "per_label_video_count": counts,
        "minimum_per_label_per_cohort": MIN_VIDEOS_PER_LABEL_PER_COHORT,
        "cohort_count": len(AVE_COHORTS),
        "required_per_label_for_disjoint_cohorts": required_per_label,
        "missing_labels": sorted(missing_labels),
        "insufficient_labels": insufficient_labels,
        "unknown_labels": sorted(unknown_labels),
        "historical_overlap_count": len(candidate_ids & historical_ids),
        "validation_effect_overlap_count": len(candidate_ids & validation_ids),
        "test_effect_overlap_count": len(candidate_ids & test_ids),
        "media_inventory_count": len(media_ids),
        "media_candidate_exact_match": media_ids == candidate_ids,
        "media_failure_count": len(media_failures),
        "media_sha_present_count": sum(bool(row.get("media_sha256")) for row in media_rows),
        "candidate_capacity_passed": (
            not missing_labels
            and not insufficient_labels
            and not unknown_labels
            and not (candidate_ids & historical_ids)
            and not (candidate_ids & validation_ids)
            and not (candidate_ids & test_ids)
            and media_ids == candidate_ids
            and not media_failures
        ),
        "sealed_manifest_generation_allowed": False,
    }


def archive_video_ids(index: dict[str, Any]) -> set[str]:
    result = []
    for archive in index.get("archives", []):
        result.extend(canonical(row.get("video_id")) for row in archive.get("videos", []))
    if any(not value for value in result):
        raise ValueError("LongVALE archive index 存在空 video_id")
    if len(result) != len(set(result)):
        raise ValueError("LongVALE archive index 存在重复 video_id")
    return set(result)


def audit_longvale(
    annotations: dict[str, Any],
    archive_index: dict[str, Any],
    exposed_rows: list[dict[str, Any]],
    reserved_rows: list[dict[str, Any]],
    local_media_ids: set[str],
    dataset_root: Path,
) -> dict[str, Any]:
    training_ids = {canonical(value) for value in annotations}
    indexed_ids = archive_video_ids(archive_index)
    exposed_ids = row_ids(exposed_rows, "video_id")
    reserved_ids = row_ids(reserved_rows, "video_id")
    candidate_ids = training_ids - exposed_ids - reserved_ids
    local_archives = []
    for archive in archive_index.get("archives", []):
        path = dataset_root / "LongVALE_V2" / str(archive["archive"])
        if (
            path.is_file()
            and path.stat().st_size == int(archive["archive_size"])
            and sha256(path) == archive.get("linked_sha256")
        ):
            local_archives.append(str(path))
    candidate_local_media = candidate_ids & {canonical(value) for value in local_media_ids}
    return {
        "training_annotation_video_count": len(training_ids),
        "archive_index_video_count": len(indexed_ids),
        "archive_index_exact_match": indexed_ids == training_ids,
        "exposed_video_count": len(exposed_ids),
        "reserved_locked_video_count": len(reserved_ids),
        "exposed_reserved_overlap_count": len(exposed_ids & reserved_ids),
        "metadata_candidate_video_count": len(candidate_ids),
        "local_media_video_count": len(local_media_ids),
        "candidate_local_media_count": len(candidate_local_media),
        "archive_count": int(archive_index.get("archive_count", 0)),
        "local_complete_archive_count": len(local_archives),
        "minimum_fusion_confirm_videos": MIN_LONGVALE_FUSION_VIDEOS,
        "metadata_capacity_passed": (
            indexed_ids == training_ids
            and not (exposed_ids & reserved_ids)
            and len(candidate_ids) >= MIN_LONGVALE_FUSION_VIDEOS
        ),
        "media_capacity_passed": (
            len(candidate_local_media) >= MIN_LONGVALE_FUSION_VIDEOS
            or len(local_archives) == int(archive_index.get("archive_count", 0))
        ),
        "reserved_confirm_reuse_allowed": False,
        "sealed_manifest_generation_allowed": False,
    }


def build_review(
    ave: dict[str, Any],
    longvale: dict[str, Any],
    input_hashes: dict[str, str],
) -> dict[str, Any]:
    blockers = []
    if not ave["candidate_capacity_passed"]:
        blockers.append("ave_disjoint_two_cohort_class_capacity_failed")
    if not longvale["metadata_capacity_passed"]:
        blockers.append("longvale_fusion_metadata_capacity_failed")
    if not longvale["media_capacity_passed"]:
        blockers.append("longvale_fusion_candidate_media_unavailable")
    return {
        "artifact_kind": "m4_ptpa_audio_confirm_candidate_s05_review",
        "format_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "blocked_before_confirm_freeze" if blockers else "candidate_audit_passed",
        "input_hashes": input_hashes,
        "ave": ave,
        "longvale": longvale,
        "blockers": blockers,
        "permissions": {
            "confirm_manifest_generation_allowed": not blockers,
            "semantic_confirm_accessed": False,
            "answer_confirm_accessed": False,
            "fusion_confirm_accessed": False,
            "formal_test_accessed": False,
            "gpu_execution_allowed": False,
            "feature_extraction_allowed": False,
            "model_inference_allowed": False,
            "training_allowed": False,
        },
        "next_stage_allowed": not blockers,
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_report(path: Path, review: dict[str, Any]) -> None:
    ave = review["ave"]
    longvale = review["longvale"]
    missing = "、".join(ave["missing_labels"]) or "无"
    lines = [
        "# S0.5 分阶段 internal confirm 候选资格审查",
        "",
        "## 结论",
        "",
        "结论：`BLOCKED_BEFORE_CONFIRM_FREEZE`。只读候选审计完成，但不生成任何 sealed manifest。",
        "",
        "## AVE semantic/answer confirm",
        "",
        f"- 未暴露 official train 候选：{ave['candidate_video_count']} 个，媒体完整："
        f"{ave['media_inventory_count']}/{ave['candidate_video_count']}。",
        f"- 类别覆盖：{ave['covered_label_count']}/{ave['expected_label_count']}；缺失类别：{missing}。",
        f"- 两个物理隔离 cohort 每类各至少 {ave['minimum_per_label_per_cohort']} 个，"
        f"要求候选池每类至少 {ave['required_per_label_for_disjoint_cohorts']} 个；当前不满足。",
        f"- 历史/validation/test 效果访问重叠：{ave['historical_overlap_count']}/"
        f"{ave['validation_effect_overlap_count']}/{ave['test_effect_overlap_count']}。",
        "",
        "## LongVALE fusion confirm",
        "",
        f"- training 元数据：{longvale['training_annotation_video_count']}；排除历史已分配/访问 "
        f"{longvale['exposed_video_count']} 和原 selector 锁定预留 "
        f"{longvale['reserved_locked_video_count']} 后，元数据候选 "
        f"{longvale['metadata_candidate_video_count']}。",
        f"- 候选本地媒体：{longvale['candidate_local_media_count']}；完整本地归档："
        f"{longvale['local_complete_archive_count']}/{longvale['archive_count']}。",
        "- 原 selector 锁定 confirm 不改名、不复用。",
        "",
        "## 当前权限",
        "",
        "confirm manifest、GPU、特征提取、模型推理、训练和所有 confirm/test 访问权限均为 `false`。",
        "",
        "## 人话结论",
        "",
        "AVE 的新视频虽然文件齐全，但类别不齐，无法公平地做两套独立试卷；LongVALE 的新题目元数据很多，"
        "但服务器上没有对应视频。现在不能靠降低标准或拿旧题补齐，必须先补充未暴露 AVE 类别和 LongVALE 媒体。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    s0 = read_json(INPUTS["s0_review"])
    if s0.get("status") != "blocked_before_s1" or s0.get("next_stage_allowed") is not False:
        raise ValueError("S0 fail-closed 输入合同漂移")
    ave_manifest_lock = read_json(INPUTS["ave_manifest_lock"])
    ave_media_lock = read_json(INPUTS["ave_media_lock"])
    validation_summary = read_json(INPUTS["ave_validation_summary"])
    test_lock = read_json(INPUTS["ave_test_execution_lock"])
    longvale_p1_lock = read_json(INPUTS["longvale_p1_lock"])
    longvale_preflight_lock = read_json(INPUTS["longvale_preflight_lock"])
    if ave_manifest_lock.get("audits", {}).get("candidate_historical_overlap") != 0:
        raise ValueError("AVE D1 候选历史交集合同漂移")
    if ave_media_lock.get("status") != "frozen":
        raise ValueError("AVE media lock 未冻结")
    if validation_summary.get("test_accessed") is not False:
        raise ValueError("AVE validation 历史访问合同漂移")
    if test_lock.get("effect_results_viewed") is not True or test_lock.get("rerun_allowed") is not False:
        raise ValueError("AVE official test 一次性访问合同漂移")
    if longvale_p1_lock.get("oracle_confirm_allowed") is not False:
        raise ValueError("LongVALE 原 selector confirm 权限漂移")
    if (
        longvale_preflight_lock.get("status") != "pass"
        or longvale_preflight_lock.get("permissions", {}).get("oracle_confirm") is not False
        or longvale_preflight_lock.get("selection", {}).get("split_sizes")
        != {"oracle_dev": 50, "oracle_pilot": 100, "oracle_confirm": 200}
    ):
        raise ValueError("LongVALE preflight split/权限合同漂移")

    historical = read_json(INPUTS["ave_historical_exposure"])
    historical_ids = {
        canonical(row.get("youtube_id")) for row in historical["entries"]
    }
    validation_rows = read_jsonl(INPUTS["ave_validation_candidates"])
    test_rows = read_jsonl(INPUTS["ave_test_candidates"])
    label_mapping = read_json(INPUTS["ave_label_mapping"])
    ave = audit_ave(
        read_jsonl(INPUTS["ave_train_candidates"]),
        read_jsonl(INPUTS["ave_train_media"]),
        [row["canonical_label"] for row in label_mapping],
        historical_ids,
        unique_ids(validation_rows, "youtube_id"),
        unique_ids(test_rows, "youtube_id"),
    )

    local_media_dir = DATASET_ROOT / "LongVALE_V2/oracle_pilot_media"
    local_media_ids = {path.stem for path in local_media_dir.glob("*.mp4")}
    longvale = audit_longvale(
        read_json(INPUTS["longvale_training_annotations"]),
        read_json(INPUTS["longvale_archive_index"]),
        [
            *read_jsonl(INPUTS["longvale_historical_dev"]),
            *read_jsonl(INPUTS["longvale_pilot"]),
        ],
        read_jsonl(INPUTS["longvale_reserved_confirm"]),
        local_media_ids,
        DATASET_ROOT,
    )
    input_hashes = {name: sha256(path) for name, path in INPUTS.items()}
    review = build_review(ave, longvale, input_hashes)
    write_json(args.output_root / "ave_candidate_capacity.json", ave)
    write_json(args.output_root / "longvale_candidate_capacity.json", longvale)
    write_json(args.output_root / "s05_review.json", review)
    write_report(args.output_root / "S05_REVIEW_ZH.md", review)
    print(json.dumps({"status": review["status"], "blockers": review["blockers"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
