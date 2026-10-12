#!/usr/bin/env python3
"""冻结 LongVALE V2 Oracle dev/pilot/confirm manifest 与控制映射。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from intersuit.harness.longvale_v2_preflight import (
    CONTROL_SEEDS,
    MIN_SPARSE_EVENTS,
    SPARSE_RATIO_MAX,
    SPLIT_SIZES,
    TOP_K,
    WINDOW_SEC,
    build_manifest_rows,
    build_mismatch_mapping,
    freeze_video_splits,
    parse_training_annotations,
    sha256_file,
    validate_split,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANNOTATION = PROJECT_ROOT / "intersuit/datasets/LongVALE_V2/official_metadata/longvale-annotations-training.json"
DEFAULT_INDEX = PROJECT_ROOT / "intersuit/datasets/LongVALE_V2/archive_index.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "intersuit/harness/artifacts/dynamic_selector_v2_v2/preflight"
EXPECTED_ANNOTATION_SHA256 = "7a14882872bb20f21597b783dcb0fee1f6fc8c3161786662115fc9e05acb29fb"
SPLIT_NAMESPACE = "longvale-v2-oracle-splits-20260803-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotation", type=Path, default=DEFAULT_ANNOTATION)
    parser.add_argument("--archive-index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def main() -> None:
    args = parse_args()
    if sha256_file(args.annotation) != EXPECTED_ANNOTATION_SHA256:
        raise ValueError("LongVALE training annotation SHA 漂移")
    archive_index = json.loads(args.archive_index.read_text(encoding="utf-8"))
    if (
        archive_index.get("artifact_kind") != "longvale_v2_official_training_archive_index"
        or archive_index.get("revision") != "18889b01886e30c36b0d1c650ac4439ad460ee73"
        or archive_index.get("video_count") != 7240
    ):
        raise ValueError("LongVALE archive index 资格不通过")
    media_by_video = {}
    for archive in archive_index["archives"]:
        for video in archive["videos"]:
            video_id = video["video_id"]
            if video_id in media_by_video:
                raise ValueError(f"重复媒体 video_id：{video_id}")
            media_by_video[video_id] = {
                "archive": archive["archive"],
                "archive_size": archive["archive_size"],
                "archive_linked_sha256": archive["linked_sha256"],
                "member": video["name"],
                "compression_method": video["compression_method"],
                "flags": video["flags"],
                "crc32": video["crc32"],
                "compressed_size": video["compressed_size"],
                "uncompressed_size": video["uncompressed_size"],
                "local_header_offset": video["local_header_offset"],
            }

    annotations = parse_training_annotations(args.annotation)
    if set(annotations) != set(media_by_video):
        missing = sorted(set(annotations) - set(media_by_video))[:10]
        extra = sorted(set(media_by_video) - set(annotations))[:10]
        raise ValueError(f"annotation/media ID 不一致：missing={missing}, extra={extra}")
    splits = freeze_video_splits(annotations, namespace=SPLIT_NAMESPACE)
    all_selected = [video_id for values in splits.values() for video_id in values]
    if len(all_selected) != len(set(all_selected)):
        raise AssertionError("oracle_dev/pilot/confirm 物理视频不互斥")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries = {}
    manifest_hashes = {}
    mismatch_hashes = {}
    for split_name, video_ids in splits.items():
        rows = build_manifest_rows(
            annotations, video_ids, media_by_video, split_name=split_name
        )
        summaries[split_name] = validate_split(rows, split_name=split_name)
        manifest_path = args.output_dir / f"{split_name}.jsonl"
        write_jsonl(manifest_path, rows)
        manifest_hashes[split_name] = sha256_file(manifest_path)
        mappings = {
            str(seed): build_mismatch_mapping(video_ids, seed=seed)
            for seed in CONTROL_SEEDS
        }
        mapping_path = args.output_dir / f"{split_name}_mismatch_mappings.json"
        write_json(mapping_path, mappings)
        mismatch_hashes[split_name] = sha256_file(mapping_path)

    lock = {
        "artifact_kind": "dynamic_selector_v2_longvale_oracle_preflight_lock",
        "format_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "source": {
            "repository": "ttgeng233/LongVALE",
            "revision": archive_index["revision"],
            "annotation_sha256": EXPECTED_ANNOTATION_SHA256,
            "archive_index_sha256": sha256_file(args.archive_index),
        },
        "selection": {
            "namespace": SPLIT_NAMESPACE,
            "physical_video_isolation": True,
            "split_sizes": SPLIT_SIZES,
            "minimum_sparse_events": MIN_SPARSE_EVENTS,
            "summaries": summaries,
        },
        "runtime_contract": {
            "window_sec": WINDOW_SEC,
            "hop_sec": WINDOW_SEC,
            "pad_short": True,
            "top_k": TOP_K,
            "sparse_event_ratio_max": SPARSE_RATIO_MAX,
            "random_seeds": list(CONTROL_SEEDS),
            "mismatch_seeds": list(CONTROL_SEEDS),
            "bootstrap_repeats": 10000,
            "bootstrap_seed": 20260724,
            "prompt_template": "Describe the audio-visual event between <start> and <end> seconds.",
        },
        "conditions": [
            "text_only", "all", "uniform_topk", "energy_topk", "onset_topk",
            "cached_eventness_topk", "random_topk_5seeds", "oracle_topk",
            "oracle_mismatch_5mappings",
        ],
        "temporal_scorer": {"status": "not_qualified", "included": False},
        "freeze_contract": {
            "optimizer_created": False,
            "modules_eval_and_requires_grad_false": [
                "M4/Qwen2", "lm_head", "vision_tower", "BEATs", "projector/resampler",
                "fusion/gate", "event_aligner", "offset_head", "learned_gate",
            ],
        },
        "permissions": {
            "oracle_pilot": True,
            "oracle_confirm": False,
            "learned_selector_training": False,
        },
        "manifest_sha256": manifest_hashes,
        "mismatch_mapping_sha256": mismatch_hashes,
    }
    write_json(args.output_dir / "preflight_lock.json", lock)
    print(json.dumps(lock, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
