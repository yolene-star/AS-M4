#!/usr/bin/env python3
"""审计 LLP metadata，并生成不授权下载的 train/validation pilot 计划。"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
LLP_ROOT = INTERSUIT_ROOT / "datasets/LLP"
LLP_DATA = LLP_ROOT / "data"
RESPONSIBILITY_LOCK = INTERSUIT_ROOT / "configs/m4_ptpa_audio_dataset_responsibility_v2.json"
AVE_HISTORY = (
    INTERSUIT_ROOT
    / "harness/artifacts/dynamic_selector_d1_ave_official/historical_exposure_inventory.json"
)
DEFAULT_OUTPUT = (
    INTERSUIT_ROOT
    / "harness/artifacts/m4_ptpa_audio_semantic_fusion_v1/llp_metadata_qualification_v2"
)
SPLIT_FILES = {
    "train": "AVVP_train.csv",
    "validation": "AVVP_val_pd.csv",
    "test": "AVVP_test_pd.csv",
}
DENSE_FILES = {
    "audio": "AVVP_eval_audio.csv",
    "visual": "AVVP_eval_visual.csv",
}
PILOT_COUNTS = {"train": 100, "validation": 50}
PILOT_MIN_PER_LABEL = {"train": 4, "validation": 2}
SELECTION_NAMESPACE = "m4-ptpa-llp-metadata-v2-pilot-20260821"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def physical_id(filename: str) -> str:
    parts = filename.rsplit("_", 2)
    if len(parts) != 3:
        raise ValueError(f"LLP filename 不能解析物理 ID/时间边界: {filename}")
    try:
        start_sec, end_sec = float(parts[1]), float(parts[2])
    except ValueError as exc:
        raise ValueError(f"LLP filename 不能解析物理 ID/时间边界: {filename}") from exc
    if not parts[0] or not math.isfinite(start_sec) or not math.isfinite(end_sec):
        raise ValueError(f"LLP filename 物理 ID/时间边界非法: {filename}")
    return parts[0]


def clip_interval(filename: str) -> tuple[float, float]:
    _, start, end = filename.rsplit("_", 2)
    start_sec, end_sec = float(start), float(end)
    if (
        not math.isfinite(start_sec)
        or not math.isfinite(end_sec)
        or start_sec < 0
        or end_sec <= start_sec
    ):
        raise ValueError(f"LLP clip 时间边界非法: {filename}")
    return start_sec, end_sec


def labels(row: dict[str, str]) -> tuple[str, ...]:
    values = tuple(sorted({value for value in row["event_labels"].split(",") if value}))
    if not values:
        raise ValueError(f"LLP row 缺少 event label: {row}")
    return values


def stable_key(split: str, filename: str) -> bytes:
    return hashlib.sha256(f"{SELECTION_NAMESPACE}:{split}:{filename}".encode()).digest()


def verify_responsibility_lock() -> tuple[dict[str, Any], dict[str, str]]:
    lock = read_json(RESPONSIBILITY_LOCK)
    if (
        lock.get("status") != "plan_locked_dataset_qualification_required"
        or lock.get("datasets", {}).get("llp", {}).get("primary_stage") != "s1"
        or lock.get("permissions", {}).get("llp_pilot_download_allowed") is not False
        or lock.get("permissions", {}).get("formal_test_effect_access_allowed") is not False
    ):
        raise ValueError("dataset responsibility v2 权限合同漂移")
    expected = lock["datasets"]["llp"]["metadata_sha256"]
    actual: dict[str, str] = {str(RESPONSIBILITY_LOCK.relative_to(PROJECT_ROOT)): sha256(RESPONSIBILITY_LOCK)}
    for filename, digest in expected.items():
        path = LLP_DATA / filename
        current = sha256(path)
        if current != digest:
            raise ValueError(f"LLP metadata SHA 漂移: {filename}; expected={digest}; actual={current}")
        actual[str(path.relative_to(PROJECT_ROOT))] = current
    return lock, actual


def known_ave_history_ids() -> set[str]:
    payload = read_json(AVE_HISTORY)
    ids = {str(row["youtube_id"]) for row in payload.get("entries", [])}
    if len(ids) != int(payload.get("touched_physical_video_count", -1)):
        raise ValueError("AVE 历史暴露清单数量漂移")
    return ids


def audit_metadata(lock: dict[str, Any], exposure_ids: set[str]) -> tuple[dict[str, Any], dict[str, list[dict[str, str]]]]:
    rows = {split: read_tsv(LLP_DATA / filename) for split, filename in SPLIT_FILES.items()}
    physical = {
        split: {physical_id(row["filename"]) for row in values}
        for split, values in rows.items()
    }
    counts = lock["datasets"]["llp"]["official_split_counts"]
    for split, values in rows.items():
        if len(values) != int(counts[split]) or len(physical[split]) != len(values):
            raise ValueError(f"LLP {split} 行数或物理 ID 唯一性漂移")
    overlaps = {
        "train_validation": len(physical["train"] & physical["validation"]),
        "train_test": len(physical["train"] & physical["test"]),
        "validation_test": len(physical["validation"] & physical["test"]),
    }
    if any(overlaps.values()):
        raise ValueError(f"LLP official split 存在物理重叠: {overlaps}")

    all_labels = {
        split: Counter(label for row in values for label in labels(row))
        for split, values in rows.items()
    }
    if any(len(counter) != 25 for counter in all_labels.values()):
        raise ValueError("LLP official split 未全部覆盖 25 类")

    clean_rows = {
        split: [row for row in values if physical_id(row["filename"]) not in exposure_ids]
        for split, values in rows.items()
    }
    clean_labels = {
        split: Counter(label for row in values for label in labels(row))
        for split, values in clean_rows.items()
    }
    known_overlap = {split: len(rows[split]) - len(clean_rows[split]) for split in rows}
    expected_clean = lock["datasets"]["llp"]["known_ave_history_excluded_counts_prequalification"]
    if {split: len(values) for split, values in clean_rows.items()} != expected_clean:
        raise ValueError("LLP 已知 AVE 历史排除数量与 v2 锁不一致")
    if any(len(counter) != 25 for counter in clean_labels.values()):
        raise ValueError("LLP 排除已知 AVE 历史后不再覆盖全部 25 类")

    dense = {
        modality: {row["filename"] for row in read_tsv(LLP_DATA / filename)}
        for modality, filename in DENSE_FILES.items()
    }
    evaluation_ids = {row["filename"] for split in ("validation", "test") for row in rows[split]}
    if not evaluation_ids.issubset(dense["audio"] | dense["visual"]):
        raise ValueError("LLP validation/test 未被 dense audio/visual 标注并集完整覆盖")

    audit = {
        "official_rows": {split: len(values) for split, values in rows.items()},
        "official_physical_ids": {split: len(values) for split, values in physical.items()},
        "official_split_physical_overlap": overlaps,
        "official_label_count": {split: len(values) for split, values in all_labels.items()},
        "known_ave_history_overlap": known_overlap,
        "known_ave_history_clean_rows_prequalification": {
            split: len(values) for split, values in clean_rows.items()
        },
        "known_ave_history_clean_label_count": {
            split: len(values) for split, values in clean_labels.items()
        },
        "dense_annotation_coverage": {
            "audio_unique_clips": len(dense["audio"]),
            "visual_unique_clips": len(dense["visual"]),
            "validation_test_union_covered": len(evaluation_ids & (dense["audio"] | dense["visual"])),
            "validation_test_expected": len(evaluation_ids),
        },
        "important_boundary": "train labels are weak; only validation/test provide dense annotations",
    }
    return audit, clean_rows


def select_balanced_pilot(
    rows: list[dict[str, str]], *, split: str, target_count: int, minimum_per_label: int
) -> list[dict[str, str]]:
    ordered = sorted(rows, key=lambda row: (stable_key(split, row["filename"]), row["filename"]))
    universe = sorted({label for row in ordered for label in labels(row)})
    selected: list[dict[str, str]] = []
    selected_ids: set[str] = set()
    counts: Counter[str] = Counter()

    while any(counts[label] < minimum_per_label for label in universe):
        candidates = [row for row in ordered if row["filename"] not in selected_ids]
        if not candidates or len(selected) >= target_count:
            raise ValueError(f"LLP {split} pilot 无法满足每类最小数量")
        chosen = min(
            candidates,
            key=lambda row: (
                -sum(counts[label] < minimum_per_label for label in labels(row)),
                stable_key(split, row["filename"]),
                row["filename"],
            ),
        )
        selected.append(chosen)
        selected_ids.add(chosen["filename"])
        counts.update(labels(chosen))

    for row in ordered:
        if len(selected) >= target_count:
            break
        if row["filename"] not in selected_ids:
            selected.append(row)
            selected_ids.add(row["filename"])
            counts.update(labels(row))
    if len(selected) != target_count or min(counts[label] for label in universe) < minimum_per_label:
        raise ValueError(f"LLP {split} pilot 数量/类别门槛失败")
    return selected


def pilot_row(row: dict[str, str], split: str) -> dict[str, Any]:
    clip_id = row["filename"]
    media_id = physical_id(clip_id)
    start_sec, end_sec = clip_interval(clip_id)
    return {
        "sample_id": f"llp:{split}:{clip_id}",
        "dataset": "LLP",
        "official_split": split,
        "clip_id": clip_id,
        "physical_media_id": media_id,
        "start_sec": start_sec,
        "end_sec": end_sec,
        "event_labels": list(labels(row)),
        "source_url": f"https://www.youtube.com/watch?v={media_id}",
        "download_allowed": False,
        "feature_extraction_allowed": False,
        "effect_access_allowed": False,
        "selection_namespace": SELECTION_NAMESPACE,
    }


def build_outputs() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    lock, input_hashes = verify_responsibility_lock()
    exposure_ids = known_ave_history_ids()
    audit, clean_rows = audit_metadata(lock, exposure_ids)
    pilot: list[dict[str, Any]] = []
    for split in ("train", "validation"):
        chosen = select_balanced_pilot(
            clean_rows[split],
            split=split,
            target_count=PILOT_COUNTS[split],
            minimum_per_label=PILOT_MIN_PER_LABEL[split],
        )
        pilot.extend(pilot_row(row, split) for row in chosen)
    if any(row["official_split"] == "test" for row in pilot):
        raise AssertionError("LLP metadata pilot 禁止包含 test")

    pilot_summary = {
        "artifact_kind": "m4_ptpa_llp_metadata_pilot_plan_v2",
        "selection_namespace": SELECTION_NAMESPACE,
        "counts": Counter(row["official_split"] for row in pilot),
        "minimum_per_label": PILOT_MIN_PER_LABEL,
        "test_rows": 0,
        "known_ave_history_overlap": sum(
            row["physical_media_id"] in exposure_ids for row in pilot
        ),
        "download_allowed": False,
        "feature_extraction_allowed": False,
        "effect_access_allowed": False,
    }
    blockers = [
        "full_historical_physical_id_ledger_not_complete",
        "raw_youtube_media_license_boundary_not_locked",
        "pilot_download_launcher_and_post_download_checks_not_reviewed",
    ]
    review = {
        "artifact_kind": "m4_ptpa_llp_metadata_qualification_review_v2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "blocked_before_llp_pilot_download",
        "input_hashes": input_hashes,
        "metadata_audit": audit,
        "pilot_summary": pilot_summary,
        "blockers": blockers,
        "permissions": {
            "metadata_audit_continuation_allowed": True,
            "pilot_manifest_frozen": True,
            "pilot_download_allowed": False,
            "full_download_allowed": False,
            "test_media_access_allowed": False,
            "feature_extraction_allowed": False,
            "gpu_execution_allowed": False,
            "training_allowed": False,
            "effect_access_allowed": False,
        },
        "next_stage_allowed": False,
        "next_stage": "complete_global_history_and_license_review_then_review_pilot_launcher",
    }
    return review, pilot, pilot_summary


def render_review(review: dict[str, Any]) -> str:
    audit = review["metadata_audit"]
    summary = review["pilot_summary"]
    return "\n".join(
        [
            "# LLP metadata qualification v2 Review",
            "",
            f"结论：`{review['status'].upper()}`。metadata 与确定性 pilot 计划通过，媒体下载仍禁止。",
            "",
            "## Metadata 结果",
            "",
            f"- 官方 train/validation/test：`{audit['official_rows']}`。",
            f"- 物理 split 交集：`{audit['official_split_physical_overlap']}`。",
            f"- 已知 AVE 历史重叠：`{audit['known_ave_history_overlap']}`。",
            f"- 排除后候选：`{audit['known_ave_history_clean_rows_prequalification']}`，各 split 仍覆盖 25 类。",
            f"- dense 标注覆盖：`{audit['dense_annotation_coverage']}`。",
            "",
            "## Pilot 计划",
            "",
            f"- 只含 train/validation：`{dict(summary['counts'])}`；test=0。",
            f"- 每类最小数量：`{summary['minimum_per_label']}`。",
            "- download/feature/effect 权限全部为 false。",
            "",
            "## Blocker",
            "",
            *[f"- `{value}`" for value in review["blockers"]],
            "",
            "## 人话结论",
            "",
            "题目清单已经冻结，但还没有证明所有旧实验都没见过这些视频，也没有锁清原始 YouTube 媒体许可和下载后验收器。因此现在只能审查清单，不能开始下载或考试。",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    review, pilot, pilot_summary = build_outputs()
    write_json(args.output_root / "llp_metadata_review.json", review)
    write_jsonl(args.output_root / "llp_pilot_plan_locked_not_allowed.jsonl", pilot)
    write_json(args.output_root / "llp_pilot_plan_summary.json", pilot_summary)
    (args.output_root / "LLP_METADATA_REVIEW_ZH.md").write_text(
        render_review(review), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": review["status"],
                "pilot_rows": len(pilot),
                "output_root": str(args.output_root),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
