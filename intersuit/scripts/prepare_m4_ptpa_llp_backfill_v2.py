#!/usr/bin/env python3
"""为 LLP pilot 的永久失效媒体生成确定性、效果锁定的同 split backfill。"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
SCRIPT_ROOT = INTERSUIT_ROOT / "scripts"
ARTIFACT_ROOT = INTERSUIT_ROOT / "harness/artifacts/m4_ptpa_audio_semantic_fusion_v1"
PILOT_ROOT = ARTIFACT_ROOT / "llp_metadata_qualification_v2"
ORIGINAL_PLAN = PILOT_ROOT / "llp_pilot_plan_locked_not_allowed.jsonl"
MEDIA_ROOT = INTERSUIT_ROOT / "datasets/LLP_PILOT_MEDIA_V2"
ORIGINAL_RESULTS = MEDIA_ROOT / "pilot150_download_results.jsonl"
DEFAULT_OUTPUT = MEDIA_ROOT / "backfill_v2"
EXPECTED_ORIGINAL_PLAN_SHA256 = "4df243662d9b98a049b80fd881052781ea4bbfa1f30ae1e420aafab84e5f3ff9"
EXPECTED_BACKFILL_COUNTS = {"train": 7, "validation": 4}


def load_metadata_module():
    path = SCRIPT_ROOT / "qualify_m4_ptpa_llp_metadata_v2.py"
    spec = importlib.util.spec_from_file_location("qualify_m4_ptpa_llp_metadata_v2_for_backfill", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def failure_class(error: str) -> str:
    if "Private video" in error:
        return "private"
    if "Video unavailable" in error:
        return "unavailable"
    if "confirm your age" in error:
        return "age_restricted"
    return "nonpermanent_or_unknown"


def select_backfill_candidates(
    clean_rows: list[dict[str, str]],
    *,
    split: str,
    excluded_clip_ids: set[str],
    count: int,
    stable_key,
) -> list[dict[str, str]]:
    candidates = [row for row in clean_rows if row["filename"] not in excluded_clip_ids]
    ordered = sorted(candidates, key=lambda row: (stable_key(split, row["filename"]), row["filename"]))
    if len(ordered) < count:
        raise ValueError(f"LLP {split} backfill 候选不足: {len(ordered)} < {count}")
    return ordered[:count]


def select_coverage_repair_candidates(
    clean_rows: list[dict[str, str]],
    *,
    split: str,
    excluded_clip_ids: set[str],
    target_count: int,
    base_counts: Counter[str],
    minimum_per_label: int,
    stable_key,
    labels,
) -> tuple[list[dict[str, str]], Counter[str]]:
    ordered = sorted(
        [row for row in clean_rows if row["filename"] not in excluded_clip_ids],
        key=lambda row: (stable_key(split, row["filename"]), row["filename"]),
    )
    universe = sorted({label for row in clean_rows for label in labels(row)})
    counts = Counter(base_counts)
    selected: list[dict[str, str]] = []
    selected_ids: set[str] = set()
    while any(counts[label] < minimum_per_label for label in universe):
        candidates = [row for row in ordered if row["filename"] not in selected_ids]
        if not candidates or len(selected) >= target_count:
            raise ValueError(f"LLP {split} coverage repair 无法在 {target_count} 条内满足门槛")
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
    if len(selected) != target_count:
        raise ValueError(f"LLP {split} coverage repair 数量不足")
    return selected, counts


def build_outputs() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metadata = load_metadata_module()
    if metadata.sha256(ORIGINAL_PLAN) != EXPECTED_ORIGINAL_PLAN_SHA256:
        raise ValueError("LLP original pilot SHA 漂移")
    original = read_jsonl(ORIGINAL_PLAN)
    result_by_sample = {row["sample_id"]: row for row in read_jsonl(ORIGINAL_RESULTS)}
    existing_media = {path.stem for path in (MEDIA_ROOT / "video").glob("*.mp4")}
    missing = [row for row in original if row["physical_media_id"] not in existing_media]
    reasons = {
        row["sample_id"]: failure_class(str(result_by_sample.get(row["sample_id"], {}).get("error", "")))
        for row in missing
    }
    if Counter(row["official_split"] for row in missing) != Counter(EXPECTED_BACKFILL_COUNTS):
        raise ValueError("LLP missing split 数量与冻结 backfill 预期不一致")
    if any(value not in {"private", "unavailable", "age_restricted"} for value in reasons.values()):
        raise ValueError(f"LLP backfill 包含非永久失败: {reasons}")

    lock, _ = metadata.verify_responsibility_lock()
    _, ave_clean = metadata.audit_metadata(lock, metadata.known_ave_history_ids())
    history_ids, _ = metadata.full_historical_youtube_ids()
    clean_rows = {
        split: [
            row
            for row in rows
            if metadata.physical_id(row["filename"]).casefold() not in history_ids
        ]
        for split, rows in ave_clean.items()
    }
    original_clip_ids = {row["clip_id"] for row in original}
    replacements: dict[str, list[dict[str, str]]] = {}
    for split, count in EXPECTED_BACKFILL_COUNTS.items():
        replacements[split] = select_backfill_candidates(
            clean_rows[split],
            split=split,
            excluded_clip_ids=original_clip_ids,
            count=count,
            stable_key=metadata.stable_key,
        )

    queues = {split: iter(values) for split, values in replacements.items()}
    backfill: list[dict[str, Any]] = []
    for failed in missing:
        split = failed["official_split"]
        replacement = metadata.pilot_row(next(queues[split]), split)
        replacement.update(
            {
                "backfill_for_sample_id": failed["sample_id"],
                "backfill_for_physical_media_id": failed["physical_media_id"],
                "backfill_reason": reasons[failed["sample_id"]],
                "backfill_selection_rule": "next stable-key candidate in same official split after original frozen pilot exclusions",
                "download_allowed": False,
                "feature_extraction_allowed": False,
                "effect_access_allowed": False,
            }
        )
        backfill.append(replacement)
    if len(backfill) != 11 or len({row["physical_media_id"] for row in backfill}) != 11:
        raise AssertionError("LLP backfill 数量或物理 ID 唯一性失败")
    if any(row["clip_id"] in original_clip_ids for row in backfill):
        raise AssertionError("LLP backfill 与 original pilot 重叠")
    summary = {
        "artifact_kind": "m4_ptpa_llp_pilot_backfill_plan_v2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "backfill11_plan_ready_download_locked",
        "original_plan_sha256": EXPECTED_ORIGINAL_PLAN_SHA256,
        "original_rows": 150,
        "qualified_original_media": len(existing_media & {row["physical_media_id"] for row in original}),
        "permanent_missing": len(missing),
        "failure_class_counts": dict(Counter(reasons.values())),
        "backfill_counts": dict(Counter(row["official_split"] for row in backfill)),
        "backfill_rows": len(backfill),
        "historical_overlap": sum(row["physical_media_id"].casefold() in history_ids for row in backfill),
        "original_pilot_overlap": sum(row["clip_id"] in original_clip_ids for row in backfill),
        "content_or_effect_based_selection": False,
        "download_allowed": False,
        "feature_extraction_allowed": False,
        "effect_access_allowed": False,
        "next_stage_allowed": False,
    }
    return backfill, summary


def build_replacement_outputs(
    target_plan: Path,
    target_results: Path,
    expected_target_sha256: str,
    excluded_plans: list[Path],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metadata = load_metadata_module()
    if metadata.sha256(ORIGINAL_PLAN) != EXPECTED_ORIGINAL_PLAN_SHA256:
        raise ValueError("LLP original pilot SHA 漂移")
    if metadata.sha256(target_plan) != expected_target_sha256:
        raise ValueError("LLP target backfill plan SHA 漂移")
    target = read_jsonl(target_plan)
    result_by_sample = {row["sample_id"]: row for row in read_jsonl(target_results)}
    existing_media = {path.stem for path in (MEDIA_ROOT / "video").glob("*.mp4")}
    missing = [row for row in target if row["physical_media_id"] not in existing_media]
    reasons = {
        row["sample_id"]: failure_class(str(result_by_sample.get(row["sample_id"], {}).get("error", "")))
        for row in missing
    }
    if not missing:
        raise ValueError("LLP target backfill 没有缺失媒体")
    if any(value not in {"private", "unavailable", "age_restricted"} for value in reasons.values()):
        raise ValueError(f"LLP replacement 包含非永久失败: {reasons}")

    lock, _ = metadata.verify_responsibility_lock()
    _, ave_clean = metadata.audit_metadata(lock, metadata.known_ave_history_ids())
    history_ids, _ = metadata.full_historical_youtube_ids()
    clean_rows = {
        split: [
            row
            for row in rows
            if metadata.physical_id(row["filename"]).casefold() not in history_ids
        ]
        for split, rows in ave_clean.items()
    }
    all_excluded_plans = [ORIGINAL_PLAN, target_plan, *excluded_plans]
    excluded_clip_ids = {
        row["clip_id"]
        for path in all_excluded_plans
        for row in read_jsonl(path)
    }
    counts = Counter(row["official_split"] for row in missing)
    replacements = {
        split: select_backfill_candidates(
            clean_rows[split],
            split=split,
            excluded_clip_ids=excluded_clip_ids,
            count=count,
            stable_key=metadata.stable_key,
        )
        for split, count in counts.items()
    }
    queues = {split: iter(values) for split, values in replacements.items()}
    rows: list[dict[str, Any]] = []
    for failed in missing:
        split = failed["official_split"]
        replacement = metadata.pilot_row(next(queues[split]), split)
        replacement.update(
            {
                "backfill_for_sample_id": failed["sample_id"],
                "backfill_for_physical_media_id": failed["physical_media_id"],
                "backfill_reason": reasons[failed["sample_id"]],
                "backfill_selection_rule": "next stable-key candidate in same official split after all prior plan exclusions",
                "download_allowed": False,
                "feature_extraction_allowed": False,
                "effect_access_allowed": False,
            }
        )
        rows.append(replacement)
    if len({row["physical_media_id"] for row in rows}) != len(rows):
        raise AssertionError("LLP replacement 物理 ID 不唯一")
    summary = {
        "artifact_kind": "m4_ptpa_llp_pilot_backfill_replacement_plan_v2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "replacement_plan_ready_download_locked",
        "target_plan_sha256": expected_target_sha256,
        "target_rows": len(target),
        "qualified_target_media": len(target) - len(missing),
        "permanent_missing": len(missing),
        "failure_class_counts": dict(Counter(reasons.values())),
        "replacement_counts": dict(Counter(row["official_split"] for row in rows)),
        "replacement_rows": len(rows),
        "historical_overlap": sum(row["physical_media_id"].casefold() in history_ids for row in rows),
        "prior_plan_overlap": sum(row["clip_id"] in excluded_clip_ids for row in rows),
        "content_or_effect_based_selection": False,
        "download_allowed": False,
        "feature_extraction_allowed": False,
        "effect_access_allowed": False,
        "next_stage_allowed": False,
    }
    return rows, summary


def build_coverage_repair_outputs() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metadata = load_metadata_module()
    if metadata.sha256(ORIGINAL_PLAN) != EXPECTED_ORIGINAL_PLAN_SHA256:
        raise ValueError("LLP original pilot SHA 漂移")
    original = read_jsonl(ORIGINAL_PLAN)
    existing_media = {path.stem for path in (MEDIA_ROOT / "video").glob("*.mp4")}
    fixed = [row for row in original if row["physical_media_id"] in existing_media]
    if Counter(row["official_split"] for row in fixed) != Counter({"train": 93, "validation": 46}):
        raise ValueError("LLP coverage repair 固定原媒体 split 数量漂移")

    lock, _ = metadata.verify_responsibility_lock()
    _, ave_clean = metadata.audit_metadata(lock, metadata.known_ave_history_ids())
    history_ids, _ = metadata.full_historical_youtube_ids()
    clean_rows = {
        split: [
            row
            for row in rows
            if metadata.physical_id(row["filename"]).casefold() not in history_ids
        ]
        for split, rows in ave_clean.items()
    }
    original_clip_ids = {row["clip_id"] for row in original}
    round1_plan = read_jsonl(MEDIA_ROOT / "backfill_v2/llp_pilot_backfill11_plan_locked.jsonl")
    round1_results = {
        row["sample_id"]: row for row in read_jsonl(MEDIA_ROOT / "backfill11_download_results.jsonl")
    }
    known_failed_clip_ids = {
        row["clip_id"]
        for row in round1_plan
        if round1_results.get(row["sample_id"], {}).get("status") != "pass"
    }
    prior_coverage_plan_path = MEDIA_ROOT / "backfill_v2/llp_pilot_coverage_repair11_plan_locked.jsonl"
    prior_coverage_results_path = MEDIA_ROOT / "coverage_repair11_download_results.jsonl"
    if prior_coverage_plan_path.is_file() and prior_coverage_results_path.is_file():
        prior_coverage_results = {
            row["sample_id"]: row for row in read_jsonl(prior_coverage_results_path)
        }
        known_failed_clip_ids.update(
            row["clip_id"]
            for row in read_jsonl(prior_coverage_plan_path)
            if prior_coverage_results.get(row["sample_id"], {}).get("status") != "pass"
        )
    excluded = original_clip_ids | known_failed_clip_ids
    target_counts = {"train": 7, "validation": 4}
    minimums = {"train": 4, "validation": 2}
    selected: dict[str, list[dict[str, str]]] = {}
    projected_counts: dict[str, Counter[str]] = {}
    for split in ("train", "validation"):
        base = Counter(
            label
            for row in fixed
            if row["official_split"] == split
            for label in row["event_labels"]
        )
        selected[split], projected_counts[split] = select_coverage_repair_candidates(
            clean_rows[split],
            split=split,
            excluded_clip_ids=excluded,
            target_count=target_counts[split],
            base_counts=base,
            minimum_per_label=minimums[split],
            stable_key=metadata.stable_key,
            labels=metadata.labels,
        )
    rows: list[dict[str, Any]] = []
    for split in ("train", "validation"):
        for candidate in selected[split]:
            row = metadata.pilot_row(candidate, split)
            row.update(
                {
                    "coverage_repair": True,
                    "coverage_repair_rule": "maximize unmet label minima then stable-key tie-break; fill remainder by stable-key",
                    "download_allowed": False,
                    "feature_extraction_allowed": False,
                    "effect_access_allowed": False,
                }
            )
            rows.append(row)
    existing_selected = sum(row["physical_media_id"] in existing_media for row in rows)
    projected_below = {
        split: {
            label: count
            for label, count in projected_counts[split].items()
            if count < minimums[split]
        }
        for split in projected_counts
    }
    summary = {
        "artifact_kind": "m4_ptpa_llp_pilot_coverage_repair_plan_v2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "coverage_repair11_plan_ready_download_locked",
        "original_plan_sha256": EXPECTED_ORIGINAL_PLAN_SHA256,
        "fixed_original_rows": len(fixed),
        "repair_counts": target_counts,
        "repair_rows": len(rows),
        "existing_reusable_rows": existing_selected,
        "new_download_rows": len(rows) - existing_selected,
        "projected_min_per_label": {
            split: min(projected_counts[split].values()) for split in projected_counts
        },
        "projected_below_required": projected_below,
        "historical_overlap": sum(row["physical_media_id"].casefold() in history_ids for row in rows),
        "original_pilot_overlap": sum(row["clip_id"] in original_clip_ids for row in rows),
        "known_failed_backfill_overlap": sum(row["clip_id"] in known_failed_clip_ids for row in rows),
        "known_failed_candidate_count": len(known_failed_clip_ids),
        "content_or_effect_based_selection": False,
        "download_allowed": False,
        "feature_extraction_allowed": False,
        "effect_access_allowed": False,
        "next_stage_allowed": False,
    }
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--target-plan", type=Path)
    parser.add_argument("--target-results", type=Path)
    parser.add_argument("--expected-target-sha256")
    parser.add_argument("--exclude-plan", type=Path, action="append", default=[])
    parser.add_argument("--label", default="backfill11")
    parser.add_argument("--coverage-repair", action="store_true")
    args = parser.parse_args()
    if args.coverage_repair:
        rows, summary = build_coverage_repair_outputs()
    elif args.target_plan is None:
        rows, summary = build_outputs()
    else:
        if args.target_results is None or not args.expected_target_sha256:
            raise ValueError("replacement round 必须提供 target results 和 expected target SHA")
        rows, summary = build_replacement_outputs(
            args.target_plan,
            args.target_results,
            args.expected_target_sha256,
            args.exclude_plan,
        )
    write_jsonl(args.output_root / f"llp_pilot_{args.label}_plan_locked.jsonl", rows)
    write_json(args.output_root / f"llp_pilot_{args.label}_review.json", summary)
    print(json.dumps({"status": summary["status"], "rows": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
