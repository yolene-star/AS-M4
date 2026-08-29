#!/usr/bin/env python3
"""独立审计 AS-M4-CGF-Valor32K-v1 pilot150 P2 条件 manifest。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


NOISE_TYPES = ("white", "pink", "same_dataset_background")
SNR_DB = (20, 10, 0)
EXPECTED_CONDITIONS = {
    "clean_correct",
    "silence",
    "mismatch",
    "shift_-1.0s",
    "shift_+1.0s",
    *(f"noisy_{noise_type}_{snr}db" for noise_type in NOISE_TYPES for snr in SNR_DB),
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_noise_seed(pair_group_id: str, noise_type: str, snr_db: int) -> int:
    payload = f"{pair_group_id}\0{noise_type}\0{snr_db}".encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest()[:16], 16)


def answer(question: dict[str, Any]) -> str:
    return " ".join(
        str(question["options"][int(question["correct_answer_idx"])]).casefold().split()
    )


def audit_manifest(
    pilot: list[dict[str, Any]], assets: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    pilot_by_id = {str(item["video_id"]): item for item in pilot}
    asset_by_id = {str(item["physical_video_id"]): item for item in assets}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["pair_group_id"])].append(row)

    errors: list[str] = []
    condition_counts = Counter(row["condition"] for row in rows)
    if len(pilot_by_id) != 150:
        errors.append(f"pilot 物理视频数不是 150：{len(pilot_by_id)}")
    if len(asset_by_id) != len(pilot_by_id) or set(asset_by_id) != set(pilot_by_id):
        errors.append("媒体资产与 pilot 物理视频集合不一致")
    if len(groups) != len(pilot_by_id) * 3:
        errors.append(f"pair group 数量错误：{len(groups)}")
    if len({row["record_id"] for row in rows}) != len(rows):
        errors.append("record_id 不唯一")

    mismatch_count = 0
    noise_seed_error_count = 0
    label_error_count = 0
    stage_error_count = 0
    source_error_count = 0
    condition_set_error_count = 0
    for group_id, group in groups.items():
        by_condition = {row["condition"]: row for row in group}
        if len(by_condition) != len(group) or set(by_condition) != EXPECTED_CONDITIONS:
            condition_set_error_count += 1
            continue
        clean = by_condition["clean_correct"]
        video_id = str(clean["physical_video_id"])
        modality = clean["modality"]
        router_target = 0 if modality == "visual" else 1
        pilot_item = pilot_by_id.get(video_id)
        target_asset = asset_by_id.get(video_id)
        if pilot_item is None or target_asset is None:
            source_error_count += 1
            continue

        expected_target_fields = {
            "target_audio_sample_rate_hz": target_asset["audio_sample_rate_hz"],
            "target_audio_channels": target_asset["audio_channels"],
            "target_audio_duration_sec": target_asset["duration_sec"],
            "target_stream_window_count": target_asset["stream_window_count"],
        }
        if any(clean.get(key) != value for key, value in expected_target_fields.items()):
            source_error_count += 1

        for condition, row in by_condition.items():
            is_shift = condition.startswith("shift_")
            if is_shift != (row["stage"] == "second_sync_only"):
                stage_error_count += 1
            if is_shift == bool(row["first_stage_training"]):
                stage_error_count += 1
            if row["router_target"] != router_target:
                label_error_count += 1

        if clean["quality_rank"] != 4 or clean["audio_source_physical_video_id"] != video_id:
            label_error_count += 1
        silence = by_condition["silence"]
        if silence["quality_rank"] != 0 or silence["corruption_recipe"] != {"type": "zeros_like_source"}:
            label_error_count += 1

        for noise_type in NOISE_TYPES:
            for snr_db, quality_rank in ((20, 3), (10, 2), (0, 1)):
                row = by_condition[f"noisy_{noise_type}_{snr_db}db"]
                if row["quality_rank"] != quality_rank:
                    label_error_count += 1
                if row["corruption_seed"] != stable_noise_seed(group_id, noise_type, snr_db):
                    noise_seed_error_count += 1
                if row["audio_source_physical_video_id"] != video_id:
                    source_error_count += 1
                if noise_type == "same_dataset_background":
                    background_id = row["corruption_recipe"].get(
                        "background_physical_video_id"
                    )
                    if background_id == video_id or background_id not in asset_by_id:
                        source_error_count += 1

        mismatch = by_condition["mismatch"]
        mismatch_count += 1
        mismatch_id = str(mismatch["audio_source_physical_video_id"])
        mismatch_item = pilot_by_id.get(mismatch_id)
        mismatch_asset = asset_by_id.get(mismatch_id)
        if mismatch_id == video_id or mismatch_item is None or mismatch_asset is None:
            source_error_count += 1
        else:
            if mismatch_asset.get("official_split") != "train":
                source_error_count += 1
            if (
                mismatch_item["selection_audio_category"]
                != pilot_item["selection_audio_category"]
            ):
                source_error_count += 1
            if answer(mismatch_item["questions"][modality]) == answer(
                pilot_item["questions"][modality]
            ):
                source_error_count += 1
        normalization = mismatch["corruption_recipe"].get("normalization", {})
        expected_normalization = {
            "sample_rate_hz": target_asset["audio_sample_rate_hz"],
            "channels": target_asset["audio_channels"],
            "duration_sec": target_asset["duration_sec"],
            "stream_window_count": target_asset["stream_window_count"],
            "length_policy": "trim_or_zero_pad_to_target",
        }
        if normalization != expected_normalization:
            source_error_count += 1

        expected_relevance = None if router_target == 0 else 1
        for condition, row in by_condition.items():
            if router_target == 0:
                if row["relevance_rank"] is not None:
                    label_error_count += 1
            elif condition in {"mismatch", "silence"}:
                if row["relevance_rank"] != 0:
                    label_error_count += 1
            elif row["relevance_rank"] != expected_relevance:
                label_error_count += 1
            expected_sync = 0 if condition.startswith("shift_") else None
            if row["sync_rank"] != expected_sync:
                label_error_count += 1

    counters = {
        "condition_set_error_count": condition_set_error_count,
        "noise_seed_error_count": noise_seed_error_count,
        "label_error_count": label_error_count,
        "stage_error_count": stage_error_count,
        "source_error_count": source_error_count,
    }
    if any(counters.values()):
        errors.append("逐组条件、标签、seed、阶段或来源审计失败")
    expected_per_condition = len(pilot_by_id) * 3
    if set(condition_counts) != EXPECTED_CONDITIONS or any(
        count != expected_per_condition for count in condition_counts.values()
    ):
        errors.append("全局条件计数不符合每问题 14 条合同")
    return {
        "status": "PASS_P2_INDEPENDENT_AUDIT" if not errors else "FAIL_P2_INDEPENDENT_AUDIT",
        "errors": errors,
        "physical_video_count": len(pilot_by_id),
        "media_asset_count": len(asset_by_id),
        "pair_group_count": len(groups),
        "record_count": len(rows),
        "mismatch_count": mismatch_count,
        "condition_counts": dict(condition_counts),
        **counters,
        "formal_test_accessed": False,
        "training_performed": False,
        "gpu_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-manifest", type=Path, required=True)
    parser.add_argument("--media-assets", type=Path, required=True)
    parser.add_argument("--condition-manifest", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()
    report = audit_manifest(
        load_json(args.pilot_manifest),
        load_json(args.media_assets),
        load_json(args.condition_manifest),
    )
    report["input_sha256"] = {
        "pilot_manifest": sha256_file(args.pilot_manifest),
        "media_assets": sha256_file(args.media_assets),
        "condition_manifest": sha256_file(args.condition_manifest),
    }
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"独立审计：{report['status']}")
    print(f"视频/问题组/记录：{report['physical_video_count']}/{report['pair_group_count']}/{report['record_count']}")
    return 0 if report["status"].startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
