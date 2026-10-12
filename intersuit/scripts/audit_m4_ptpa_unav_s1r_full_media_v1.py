#!/usr/bin/env python3
"""独立复核UnAV S1-R全量媒体资格并生成合格/拒绝manifest。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_LEDGER = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/qualification_v1/unav_s1_physical_ledger.jsonl"
DEFAULT_ROOT = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/full_train_validation_media_v1"
DEFAULT_RESULTS = DEFAULT_ROOT / "full_media_results.jsonl"
DEFAULT_SUMMARY = DEFAULT_ROOT / "summary.json"
DEFAULT_MEDIA_ROOT = DEFAULT_ROOT / "media"
DEFAULT_QUALIFIED = DEFAULT_ROOT / "qualified_media_manifest.jsonl"
DEFAULT_REJECTED = DEFAULT_ROOT / "rejected_media_manifest.jsonl"
DEFAULT_OUTPUT = DEFAULT_ROOT / "independent_audit.json"
PASS_STATUS = "PASS_INDEPENDENT_AUDIT_WITH_2_MEDIA_REJECTED_EFFECT_LOCKED"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def audit(
    ledger_path: Path,
    results_path: Path,
    summary_path: Path,
    media_root: Path,
    qualified_path: Path,
    rejected_path: Path,
) -> dict[str, Any]:
    ledger = {row["physical_media_id"]: row for row in read_jsonl(ledger_path)}
    results = read_jsonl(results_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("status") != "FAIL_FULL_MEDIA_QUALIFICATION":
        raise ValueError("UnAV S1-R原始严格全量状态必须保留FAIL")
    if summary.get("results_sha256") != sha256(results_path):
        raise ValueError("UnAV S1-R results SHA漂移")
    if len(results) != 7943 or len({row["physical_media_id"] for row in results}) != 7943:
        raise ValueError("UnAV S1-R结果数量或物理ID唯一性漂移")
    split_counts = Counter(row["official_split"] for row in results)
    if split_counts != {"train": 6226, "validation": 1717}:
        raise PermissionError(f"UnAV S1-R结果split越界：{split_counts}")
    observed_files = {path.name for path in media_root.iterdir() if path.is_file()}
    expected_files = {f"{row['physical_media_id']}.mp4" for row in results}
    if observed_files != expected_files:
        raise ValueError("UnAV S1-R媒体文件集合不精确")

    qualified = []
    rejected = []
    label_videos: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for index, row in enumerate(results, start=1):
        video_id = str(row["physical_media_id"])
        metadata = ledger.get(video_id)
        path = media_root / f"{video_id}.mp4"
        if (
            metadata is None
            or metadata.get("eligible") is not True
            or metadata.get("official_split") != row["official_split"]
        ):
            raise ValueError(f"UnAV S1-R结果ledger资格漂移：{video_id}")
        if path.stat().st_size != int(row["size"]) or sha256(path) != row["sha256"]:
            raise ValueError(f"UnAV S1-R媒体大小或SHA漂移：{video_id}")
        if row["passed"]:
            audit_row = row.get("audit")
            if (
                audit_row is None
                or abs(float(audit_row["duration_delta_sec"])) > 1.0
                or float(audit_row["max_event_offset_sec"]) > float(audit_row["media_duration_sec"]) + 1.0
                or audit_row.get("full_media_decode_passed") is not True
                or audit_row.get("audio_decode_16khz_mono_passed") is not True
            ):
                raise ValueError(f"UnAV S1-R合格审计字段漂移：{video_id}")
            qualified_row = {
                "physical_media_id": video_id,
                "official_split": row["official_split"],
                "path": str(path),
                "size": int(row["size"]),
                "sha256": row["sha256"],
                "metadata_duration_sec": float(metadata["duration_sec"]),
                "media_duration_sec": float(audit_row["media_duration_sec"]),
                "duration_delta_sec": float(audit_row["duration_delta_sec"]),
                "event_count": int(metadata["event_count"]),
                "event_labels": metadata["event_labels"],
                "events": metadata["events"],
                "full_media_decode_passed": True,
                "audio_decode_16khz_mono_passed": True,
                "effect_access_allowed": False,
                "test_effect_access_allowed": False,
                "training_allowed": False,
            }
            qualified.append(qualified_row)
            for label in metadata["event_labels"]:
                label_videos[row["official_split"]][label].add(video_id)
        else:
            if row.get("audit") is not None or "时长偏差超限" not in str(row.get("error")):
                raise ValueError(f"UnAV S1-R拒绝原因不符合冻结媒体门禁：{video_id}")
            rejected.append(
                {
                    "physical_media_id": video_id,
                    "official_split": row["official_split"],
                    "path": str(path),
                    "size": int(row["size"]),
                    "sha256": row["sha256"],
                    "metadata_duration_sec": float(metadata["duration_sec"]),
                    "event_labels": metadata["event_labels"],
                    "events": metadata["events"],
                    "rejection_reason": row["error"],
                    "rejection_is_effect_based": False,
                }
            )
        if index % 500 == 0:
            print(json.dumps({"event": "independent_sha_progress", "completed": index, "requested": 7943}), flush=True)

    qualified.sort(key=lambda row: (row["official_split"], row["physical_media_id"]))
    rejected.sort(key=lambda row: (row["official_split"], row["physical_media_id"]))
    if Counter(row["official_split"] for row in qualified) != {"train": 6224, "validation": 1717}:
        raise ValueError("UnAV S1-R合格子集split数量漂移")
    if Counter(row["official_split"] for row in rejected) != {"train": 2}:
        raise ValueError("UnAV S1-R拒绝子集split数量漂移")
    label_counts = {split: len(labels) for split, labels in label_videos.items()}
    min_unique = {
        split: min(len(video_ids) for video_ids in labels.values())
        for split, labels in label_videos.items()
    }
    if label_counts != {"train": 100, "validation": 100}:
        raise ValueError("UnAV S1-R拒绝后类别覆盖不完整")
    write_jsonl_atomic(qualified_path, qualified)
    write_jsonl_atomic(rejected_path, rejected)
    return {
        "artifact_kind": "m4_ptpa_unav_s1r_full_media_independent_audit",
        "format_version": 1,
        "status": PASS_STATUS,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested": len(results),
        "qualified": len(qualified),
        "rejected": len(rejected),
        "qualified_split_counts": dict(sorted(Counter(row["official_split"] for row in qualified).items())),
        "rejected_split_counts": dict(sorted(Counter(row["official_split"] for row in rejected).items())),
        "qualified_total_media_bytes": sum(int(row["size"]) for row in qualified),
        "rejected_total_media_bytes": sum(int(row["size"]) for row in rejected),
        "label_counts": dict(sorted(label_counts.items())),
        "min_unique_videos_per_label": dict(sorted(min_unique.items())),
        "qualified_manifest_path": str(qualified_path),
        "qualified_manifest_sha256": sha256(qualified_path),
        "rejected_manifest_path": str(rejected_path),
        "rejected_manifest_sha256": sha256(rejected_path),
        "results_sha256": sha256(results_path),
        "summary_sha256": sha256(summary_path),
        "media_sha_mismatch_count": 0,
        "unexpected_media_file_count": 0,
        "test_media_accessed": False,
        "feature_extraction_performed": False,
        "effect_accessed": False,
        "training_performed": False,
        "redistribution_performed": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--media-root", type=Path, default=DEFAULT_MEDIA_ROOT)
    parser.add_argument("--qualified", type=Path, default=DEFAULT_QUALIFIED)
    parser.add_argument("--rejected", type=Path, default=DEFAULT_REJECTED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    arguments = parse_args()
    result = audit(
        arguments.ledger,
        arguments.results,
        arguments.summary,
        arguments.media_root,
        arguments.qualified,
        arguments.rejected,
    )
    write_json_atomic(arguments.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
