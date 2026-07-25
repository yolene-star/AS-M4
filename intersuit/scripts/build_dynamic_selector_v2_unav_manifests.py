#!/usr/bin/env python3
"""Build frozen UnAV V2 manifests grouped by physical video and event label."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from intersuit.harness.dynamic_selector_v2_data import (
    build_unav_video_label_samples,
    frozen_label_mapping,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--test", type=Path, required=True)
    parser.add_argument("--exclude-video-ids", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def read_rows(path: Path) -> list[dict]:
    frame = pd.read_parquet(path)
    required = {"video_id", "duration", "timestamp", "event_label"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path} 缺少字段：{sorted(missing)}")
    return frame[list(sorted(required))].to_dict("records")


def read_exclusions(path: Path | None) -> set[str]:
    if path is None:
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("video_ids") or payload.get("excluded_video_ids")
    if not isinstance(payload, list):
        raise ValueError("排除清单必须是数组或包含 video_ids 数组的对象")
    return {str(value) for value in payload}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    rows = {
        "train": read_rows(args.train),
        "validation": read_rows(args.validation),
        "test": read_rows(args.test),
    }
    mapping = frozen_label_mapping(row["event_label"] for row in rows["train"])
    exclusions = read_exclusions(args.exclude_video_ids)
    manifests = {
        split: build_unav_video_label_samples(
            values,
            split=split,
            label_mapping=mapping,
            excluded_video_ids=exclusions,
        )
        for split, values in rows.items()
    }
    output_dir = args.output_dir
    for split, samples in manifests.items():
        write_json(output_dir / f"unav_{split}_video_label.json", [sample.to_dict() for sample in samples])
    write_json(output_dir / "unav_label_mapping.json", mapping)
    manifest_sha256 = {
        split: sha256(output_dir / f"unav_{split}_video_label.json") for split in manifests
    }
    summary = {
        "format_version": 1,
        "sample_unit": "video_id_event_label",
        "positive_definition": "overlap_with_any_same_label_occurrence",
        "negative_definition": "no_overlap_with_any_same_label_occurrence",
        "excluded_video_count": len(exclusions),
        "excluded_video_ids_sha256": hashlib.sha256(
            ("\n".join(sorted(exclusions)) + "\n").encode("utf-8")
        ).hexdigest(),
        "exclusion_file_sha256": sha256(args.exclude_video_ids) if args.exclude_video_ids else None,
        "label_count": len(mapping),
        "label_mapping_sha256": sha256(output_dir / "unav_label_mapping.json"),
        "manifest_sha256": manifest_sha256,
        "source_sha256": {
            "train": sha256(args.train),
            "validation": sha256(args.validation),
            "test": sha256(args.test),
        },
        "splits": {
            split: {
                "sample_count": len(samples),
                "video_count": len({sample.video_id for sample in samples}),
                "annotation_count": sum(sample.annotation_count for sample in samples),
                "multi_occurrence_sample_count": sum(sample.annotation_count > 1 for sample in samples),
                "sparse_union_ratio_le_10pct": sum(sample.union_duration_ratio <= 0.10 for sample in samples),
            }
            for split, samples in manifests.items()
        },
    }
    write_json(output_dir / "unav_v2_manifest_summary.json", summary)


if __name__ == "__main__":
    main()
