#!/usr/bin/env python3
"""构建并审计 AS-M4-CGF-Valor32K-v1 pilot150 的 P2 配对资产。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


DATASET_VERSION = "AS-M4-CGF-Valor32K-v1-pilot150"
FIRST_STAGE_NOISE_TYPES = ("white", "pink", "same_dataset_background")
FIRST_STAGE_SNR_DB = (20, 10, 0)
SECOND_STAGE_SHIFTS_SEC = (-1.0, 1.0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hex(*parts: object) -> str:
    payload = "\0".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def parse_media_audit(path: Path) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            relative_path, raw_fields = raw_line.split("\t", 1)
        except ValueError as error:
            raise ValueError(f"媒体审计第 {line_number} 行缺少制表符") from error
        fields: dict[str, str] = {}
        codec_types: list[str] = []
        for item in raw_fields.split(";"):
            if not item:
                continue
            key, value = item.split("=", 1)
            if key == "codec_type":
                codec_types.append(value)
            else:
                fields[key] = value
        fields["codec_types"] = ",".join(codec_types)
        if relative_path in records:
            raise ValueError(f"媒体审计路径重复：{relative_path}")
        records[relative_path] = fields
    return records


def resolve_media_path(video_id: str, media_paths: Iterable[Path]) -> Path:
    matches = [path for path in media_paths if path.name.startswith(f"{video_id}_")]
    if len(matches) != 1:
        raise ValueError(f"{video_id} 的媒体匹配数应为 1，实际为 {len(matches)}")
    return matches[0]


def build_media_assets(
    pilot: list[dict[str, Any]], media_root: Path, audit_path: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    audit = parse_media_audit(audit_path)
    media_paths = sorted(path for path in media_root.rglob("*") if path.is_file())
    assets: list[dict[str, Any]] = []
    for item in pilot:
        video_id = str(item["video_id"])
        path = resolve_media_path(video_id, media_paths)
        relative_path = path.relative_to(media_root).as_posix()
        fields = audit.get(relative_path)
        if fields is None:
            raise ValueError(f"媒体审计缺少路径：{relative_path}")
        codecs = set(fields.get("codec_types", "").split(","))
        duration = float(fields["duration"])
        sample_rate = int(fields["sample_rate"])
        channels = int(fields["channels"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"无效媒体时长：{relative_path}")
        if not {"audio", "video"}.issubset(codecs):
            raise ValueError(f"媒体缺少音频或视频流：{relative_path}")
        if sample_rate <= 0 or channels <= 0:
            raise ValueError(f"媒体音频参数无效：{relative_path}")
        assets.append(
            {
                "physical_video_id": video_id,
                "official_split": "train",
                "relative_media_path": relative_path,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "duration_sec": duration,
                "audio_sample_rate_hz": sample_rate,
                "audio_channels": channels,
                "stream_window_count": max(1, math.floor((duration - 2.0) / 1.0) + 1),
                "has_video_stream": True,
                "has_audio_stream": True,
            }
        )
    assets.sort(key=lambda row: row["physical_video_id"])
    report = {
        "asset_count": len(assets),
        "unique_physical_video_ids": len({row["physical_video_id"] for row in assets}),
        "all_have_audio_video": all(
            row["has_audio_stream"] and row["has_video_stream"] for row in assets
        ),
        "total_size_bytes": sum(row["size_bytes"] for row in assets),
        "duration_sec": {
            "min": min(row["duration_sec"] for row in assets),
            "max": max(row["duration_sec"] for row in assets),
            "mean": sum(row["duration_sec"] for row in assets) / len(assets),
        },
        "sample_rate_counts": dict(Counter(row["audio_sample_rate_hz"] for row in assets)),
        "channel_counts": dict(Counter(row["audio_channels"] for row in assets)),
    }
    return assets, report


def question_answer(question: dict[str, Any]) -> str:
    index = int(question["correct_answer_idx"])
    options = question["options"]
    if index < 0 or index >= len(options):
        raise ValueError(f"问题 {question['id']} 的答案索引越界")
    return str(options[index])


def normalized_answer(question: dict[str, Any]) -> str:
    return " ".join(question_answer(question).casefold().split())


def build_mismatch_mapping(
    pilot: list[dict[str, Any]], modality: str, seed: str
) -> tuple[dict[str, str], int]:
    by_id = {str(item["video_id"]): item for item in pilot}
    mapping: dict[str, str] = {}
    category_fallback_count = 0
    for source_id, source in sorted(by_id.items()):
        source_question = source["questions"][modality]
        source_answer = normalized_answer(source_question)
        same_category = [
            target_id
            for target_id, target in by_id.items()
            if target_id != source_id
            and target["selection_audio_category"] == source["selection_audio_category"]
            and normalized_answer(target["questions"][modality]) != source_answer
        ]
        candidates = same_category
        if not candidates:
            category_fallback_count += 1
            candidates = [
                target_id
                for target_id, target in by_id.items()
                if target_id != source_id
                and normalized_answer(target["questions"][modality]) != source_answer
            ]
        if not candidates:
            raise ValueError(f"{source_id}/{modality} 没有答案不同的 mismatch 候选")
        mapping[source_id] = min(
            candidates, key=lambda target_id: stable_hex(seed, modality, source_id, target_id)
        )
    return mapping, category_fallback_count


def condition_row(
    base: dict[str, Any], condition: str, audio_asset: dict[str, Any], **updates: Any
) -> dict[str, Any]:
    row = dict(base)
    row.update(
        {
            "condition": condition,
            "audio_source_physical_video_id": audio_asset["physical_video_id"],
            "audio_source_relative_media_path": audio_asset["relative_media_path"],
            "audio_source_sha256": audio_asset["sha256"],
        }
    )
    row.update(updates)
    row["record_id"] = stable_hex(
        row["dataset_version"], row["pair_group_id"], condition
    )[:24]
    return row


def build_condition_manifest(
    pilot: list[dict[str, Any]], assets: list[dict[str, Any]], seed: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    asset_by_id = {row["physical_video_id"]: row for row in assets}
    mappings = {}
    mismatch_category_fallbacks = {}
    for modality in ("visual", "audio", "audio-visual"):
        mappings[modality], mismatch_category_fallbacks[modality] = build_mismatch_mapping(
            pilot, modality, seed
        )

    rows: list[dict[str, Any]] = []
    for item in pilot:
        video_id = str(item["video_id"])
        correct_asset = asset_by_id[video_id]
        for modality in ("visual", "audio", "audio-visual"):
            question = item["questions"][modality]
            question_id = str(question["id"])
            pair_group_id = stable_hex(DATASET_VERSION, video_id, question_id)[:24]
            router_group_id = stable_hex(DATASET_VERSION, video_id, "router")[:24]
            router_target = 0 if modality == "visual" else 1
            base = {
                "dataset_version": DATASET_VERSION,
                "split": "train_pilot150",
                "physical_video_id": video_id,
                "question_id": question_id,
                "pair_group_id": pair_group_id,
                "router_group_id": router_group_id,
                "video_relative_media_path": correct_asset["relative_media_path"],
                "video_sha256": correct_asset["sha256"],
                "target_audio_sample_rate_hz": correct_asset["audio_sample_rate_hz"],
                "target_audio_channels": correct_asset["audio_channels"],
                "target_audio_duration_sec": correct_asset["duration_sec"],
                "target_stream_window_count": correct_asset["stream_window_count"],
                "question": question["question"],
                "answer": question_answer(question),
                "options": question["options"],
                "modality": modality,
                "category": question["category"],
                "router_target": router_target,
                "window_sec": 2.0,
                "hop_sec": 1.0,
                "stream_trigger_sec": 2.0,
            }
            rows.append(
                condition_row(
                    base,
                    "clean_correct",
                    correct_asset,
                    stage="first",
                    corruption_recipe={"type": "identity"},
                    corruption_seed=None,
                    quality_rank=4,
                    relevance_rank=1 if router_target else None,
                    sync_rank=None,
                    first_stage_training=True,
                )
            )
            for noise_type in FIRST_STAGE_NOISE_TYPES:
                for snr_db in FIRST_STAGE_SNR_DB:
                    noise_seed = int(stable_hex(pair_group_id, noise_type, snr_db)[:16], 16)
                    recipe: dict[str, Any] = {
                        "type": "additive_noise",
                        "noise_type": noise_type,
                        "snr_db": snr_db,
                    }
                    if noise_type == "same_dataset_background":
                        background_id = mappings[modality][video_id]
                        background = asset_by_id[background_id]
                        recipe.update(
                            {
                                "background_physical_video_id": background_id,
                                "background_relative_media_path": background[
                                    "relative_media_path"
                                ],
                                "background_sha256": background["sha256"],
                            }
                        )
                    rows.append(
                        condition_row(
                            base,
                            f"noisy_{noise_type}_{snr_db}db",
                            correct_asset,
                            stage="first",
                            corruption_recipe=recipe,
                            corruption_seed=noise_seed,
                            quality_rank={20: 3, 10: 2, 0: 1}[snr_db],
                            relevance_rank=1 if router_target else None,
                            sync_rank=None,
                            first_stage_training=True,
                        )
                    )
            rows.append(
                condition_row(
                    base,
                    "silence",
                    correct_asset,
                    stage="first",
                    corruption_recipe={"type": "zeros_like_source"},
                    corruption_seed=None,
                    quality_rank=0,
                    relevance_rank=0 if router_target else None,
                    sync_rank=None,
                    first_stage_training=True,
                )
            )
            mismatch_id = mappings[modality][video_id]
            mismatch_asset = asset_by_id[mismatch_id]
            rows.append(
                condition_row(
                    base,
                    "mismatch",
                    mismatch_asset,
                    stage="first",
                    corruption_recipe={
                        "type": "cross_video_audio",
                        "normalization": {
                            "sample_rate_hz": correct_asset["audio_sample_rate_hz"],
                            "channels": correct_asset["audio_channels"],
                            "duration_sec": correct_asset["duration_sec"],
                            "stream_window_count": correct_asset["stream_window_count"],
                            "length_policy": "trim_or_zero_pad_to_target",
                        },
                    },
                    corruption_seed=None,
                    quality_rank=None,
                    relevance_rank=0 if router_target else None,
                    sync_rank=None,
                    first_stage_training=True,
                )
            )
            for shift_sec in SECOND_STAGE_SHIFTS_SEC:
                rows.append(
                    condition_row(
                        base,
                        f"shift_{shift_sec:+.1f}s",
                        correct_asset,
                        stage="second_sync_only",
                        corruption_recipe={
                            "type": "circular_time_shift",
                            "shift_sec": shift_sec,
                        },
                        corruption_seed=None,
                        quality_rank=None,
                        relevance_rank=1 if router_target else None,
                        sync_rank=0,
                        first_stage_training=False,
                    )
                )

    condition_counts = Counter(row["condition"] for row in rows)
    stage_counts = Counter(row["stage"] for row in rows)
    first_stage_rows = [row for row in rows if row["first_stage_training"]]
    second_stage_rows = [row for row in rows if not row["first_stage_training"]]
    report = {
        "question_count": len(pilot) * 3,
        "record_count": len(rows),
        "condition_counts": dict(condition_counts),
        "stage_counts": dict(stage_counts),
        "first_stage_record_count": len(first_stage_rows),
        "second_stage_record_count": len(second_stage_rows),
        "mismatch_self_mapping_count": sum(
            row["physical_video_id"] == row["audio_source_physical_video_id"]
            for row in rows
            if row["condition"] == "mismatch"
        ),
        "mismatch_cross_split_count": sum(
            asset_by_id[row["audio_source_physical_video_id"]]["official_split"]
            != "train"
            for row in rows
            if row["condition"] == "mismatch"
        ),
        "mismatch_missing_target_normalization_count": sum(
            "normalization" not in row["corruption_recipe"]
            for row in rows
            if row["condition"] == "mismatch"
        ),
        "mismatch_category_fallback_counts": mismatch_category_fallbacks,
        "first_stage_shifted_count": sum(
            row["condition"].startswith("shift_") for row in first_stage_rows
        ),
        "second_stage_non_shifted_count": sum(
            not row["condition"].startswith("shift_") for row in second_stage_rows
        ),
        "duplicate_record_id_count": len(rows)
        - len({row["record_id"] for row in rows}),
        "quality_relevance_sync_independently_varied": all(
            (
                any(row["quality_rank"] == 4 and row["relevance_rank"] == 1 for row in rows),
                any(row["quality_rank"] == 0 and row["relevance_rank"] == 0 for row in rows),
                any(row["quality_rank"] is None and row["relevance_rank"] == 0 for row in rows),
                any(row["sync_rank"] == 0 and row["relevance_rank"] == 1 for row in rows),
            )
        ),
    }
    return rows, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-manifest", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--media-audit", type=Path, required=True)
    parser.add_argument("--source-tar-sha256", type=Path, required=True)
    parser.add_argument("--source-license", type=Path, required=True)
    parser.add_argument("--output-assets", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--seed", default="as-m4-cgf-valor32k-p2-v1")
    args = parser.parse_args()

    pilot = load_json(args.pilot_manifest)
    assets, media_report = build_media_assets(pilot, args.media_root, args.media_audit)
    rows, condition_report = build_condition_manifest(pilot, assets, args.seed)
    write_json(args.output_assets, assets)
    write_json(args.output_manifest, rows)

    tar_sha_tokens = args.source_tar_sha256.read_text(encoding="utf-8").split()
    if len(tar_sha_tokens) < 1 or len(tar_sha_tokens[0]) != 64:
        raise ValueError("源 tar SHA-256 文件格式无效")
    report = {
        "artifact_kind": "as_m4_cgf_valor32k_p2_audit",
        "status": "PASS_P2_DATA_CONTRACT"
        if (
            media_report["asset_count"] == len(pilot) == 150
            and media_report["all_have_audio_video"]
            and condition_report["mismatch_self_mapping_count"] == 0
            and condition_report["mismatch_cross_split_count"] == 0
            and condition_report["mismatch_missing_target_normalization_count"] == 0
            and condition_report["first_stage_shifted_count"] == 0
            and condition_report["second_stage_non_shifted_count"] == 0
            and condition_report["duplicate_record_id_count"] == 0
            and condition_report["quality_relevance_sync_independently_varied"]
        )
        else "FAIL_P2_DATA_CONTRACT",
        "dataset_version": DATASET_VERSION,
        "seed": args.seed,
        "source_tar_sha256": tar_sha_tokens[0],
        "inputs": {
            "pilot_manifest_sha256": sha256_file(args.pilot_manifest),
            "media_audit_sha256": sha256_file(args.media_audit),
            "source_license": {
                "name": "CC BY-NC 4.0",
                "sha256": sha256_file(args.source_license),
            },
        },
        "outputs": {
            "media_assets_sha256": sha256_file(args.output_assets),
            "condition_manifest_sha256": sha256_file(args.output_manifest),
        },
        "media": media_report,
        "conditions": condition_report,
        "policy": {
            "official_split": "train",
            "formal_test_accessed": False,
            "waveform_materialized": False,
            "first_stage_sync_gate_enabled": False,
            "second_stage_shifted_assets_frozen_only": True,
            "training_allowed": False,
            "gpu_allowed": False,
        },
    }
    write_json(args.output_report, report)
    print(f"P2 状态：{report['status']}")
    print(f"媒体资产：{media_report['asset_count']}")
    print(f"条件记录：{condition_report['record_count']}")
    print(f"首轮/二阶段：{condition_report['first_stage_record_count']}/{condition_report['second_stage_record_count']}")
    print(f"manifest SHA-256：{report['outputs']['condition_manifest_sha256']}")
    return 0 if report["status"].startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
