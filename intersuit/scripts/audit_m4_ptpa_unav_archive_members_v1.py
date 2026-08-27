#!/usr/bin/env python3
"""不解压媒体，流式审计 UnAV-100 分卷tar成员及metadata映射。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_ARCHIVE_ROOT = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/official_archive"
DEFAULT_COMPLETION = DEFAULT_ARCHIVE_ROOT / "archive_completion_manifest_v1.json"
DEFAULT_LEDGER = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/qualification_v1/unav_s1_physical_ledger.jsonl"
DEFAULT_OUTPUT_ROOT = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/archive_member_audit_v1"
EXPECTED_PREFIX = PurePosixPath("ssd3/gengtiantian/audio_visual_data/unav100/raw_videos")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class MultipartReader:
    def __init__(self, paths: list[Path]):
        self._paths: Iterator[Path] = iter(paths)
        self._handle: Any = None

    def read(self, size: int = -1) -> bytes:
        chunks = []
        remaining = size
        while size < 0 or remaining > 0:
            if self._handle is None:
                try:
                    self._handle = next(self._paths).open("rb")
                except StopIteration:
                    break
            chunk = self._handle.read(-1 if size < 0 else remaining)
            if chunk:
                chunks.append(chunk)
                if size >= 0:
                    remaining -= len(chunk)
            else:
                self._handle.close()
                self._handle = None
        return b"".join(chunks)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def read_ledger(path: Path) -> dict[str, dict[str, Any]]:
    rows = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            video_id = str(row["physical_media_id"])
            if video_id in rows:
                raise ValueError(f"UnAV ledger物理ID重复：{video_id}")
            rows[video_id] = row
    return rows


def validate_member_path(name: str) -> tuple[bool, str | None]:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return False, "unsafe_path"
    if path == EXPECTED_PREFIX:
        return True, None
    if path.parent != EXPECTED_PREFIX:
        return False, "unexpected_parent"
    if path.suffix.lower() != ".mp4":
        return False, "unexpected_suffix"
    return True, None


def scan_archive(
    archive_paths: list[Path], ledger: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reader = MultipartReader(archive_paths)
    rows = []
    seen_ids: set[str] = set()
    unsafe = []
    non_regular = []
    directories = 0
    total_media_bytes = 0
    try:
        with tarfile.open(fileobj=reader, mode="r|gz") as archive:
            for member_index, member in enumerate(archive):
                path_ok, path_error = validate_member_path(member.name)
                if not path_ok:
                    unsafe.append({"name": member.name, "reason": path_error})
                    continue
                if member.isdir():
                    directories += 1
                    continue
                if not member.isreg():
                    non_regular.append({"name": member.name, "type": repr(member.type)})
                    continue
                video_id = PurePosixPath(member.name).stem
                if video_id in seen_ids:
                    raise ValueError(f"UnAV归档物理ID重复：{video_id}")
                seen_ids.add(video_id)
                metadata = ledger.get(video_id)
                rows.append(
                    {
                        "archive_member_index": member_index,
                        "member_name": member.name,
                        "media_size_bytes": int(member.size),
                        "physical_media_id": video_id,
                        "metadata_present": metadata is not None,
                        "official_split": metadata.get("official_split") if metadata else None,
                        "eligible_after_known_history": metadata.get("eligible") if metadata else None,
                        "exclusion_reasons": metadata.get("exclusion_reasons", []) if metadata else [],
                        "test_effect_locked": metadata.get("test_effect_locked") if metadata else None,
                    }
                )
                total_media_bytes += int(member.size)
    finally:
        reader.close()

    ledger_ids = set(ledger)
    archive_ids = {row["physical_media_id"] for row in rows}
    split_counts = Counter(row["official_split"] for row in rows if row["official_split"])
    eligible_counts = Counter(
        row["official_split"]
        for row in rows
        if row["official_split"] and row["eligible_after_known_history"]
    )
    summary = {
        "status": "PASS" if not (
            unsafe or non_regular or archive_ids - ledger_ids or ledger_ids - archive_ids
        ) else "FAIL",
        "archive_member_count": len(rows) + directories + len(unsafe) + len(non_regular),
        "directory_count": directories,
        "regular_mp4_count": len(rows),
        "total_media_bytes": total_media_bytes,
        "metadata_physical_id_count": len(ledger_ids),
        "archive_physical_id_count": len(archive_ids),
        "archive_split_counts": dict(sorted(split_counts.items())),
        "eligible_after_known_history_counts": dict(sorted(eligible_counts.items())),
        "metadata_missing_from_archive_count": len(ledger_ids - archive_ids),
        "metadata_missing_from_archive_sample": sorted(ledger_ids - archive_ids)[:20],
        "archive_missing_from_metadata_count": len(archive_ids - ledger_ids),
        "archive_missing_from_metadata_sample": sorted(archive_ids - ledger_ids)[:20],
        "unsafe_member_count": len(unsafe),
        "unsafe_member_sample": unsafe[:20],
        "non_regular_member_count": len(non_regular),
        "non_regular_member_sample": non_regular[:20],
    }
    return rows, summary


def run(
    completion_path: Path,
    archive_root: Path,
    ledger_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    if completion.get("status") != "PASS_ARCHIVE_COMPLETE_VERIFIED_EFFECT_LOCKED":
        raise PermissionError("UnAV归档组合终态未通过")
    permissions = completion["permissions"]
    if permissions.get("archive_member_inventory_allowed") is not True:
        raise PermissionError("UnAV归档成员清点未授权")
    for key in (
        "archive_extraction_allowed", "feature_extraction_allowed",
        "effect_access_allowed", "test_media_access_allowed",
        "test_effect_access_allowed", "training_allowed", "redistribution_allowed",
    ):
        if permissions.get(key) is not False:
            raise PermissionError(f"UnAV成员审计禁止项漂移：{key}")

    archive_paths = [archive_root / row["name"] for row in completion["results"]]
    if any(not path.is_file() for path in archive_paths):
        raise FileNotFoundError("UnAV归档卷缺失")
    ledger = read_ledger(ledger_path)
    rows, summary = scan_archive(archive_paths, ledger)
    output_root.mkdir(parents=True, exist_ok=True)
    index_path = output_root / "archive_member_index.jsonl"
    temporary = output_root / ".archive_member_index.jsonl.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(index_path)
    summary.update(
        {
            "artifact_kind": "m4_ptpa_unav_archive_member_audit",
            "format_version": 1,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "completion_manifest_path": str(completion_path),
            "completion_manifest_sha256": sha256(completion_path),
            "ledger_path": str(ledger_path),
            "ledger_sha256": sha256(ledger_path),
            "member_index_path": str(index_path),
            "member_index_sha256": sha256(index_path),
            "permissions": {
                "archive_extraction_allowed": False,
                "feature_extraction_allowed": False,
                "effect_access_allowed": False,
                "test_media_access_allowed": False,
                "test_effect_access_allowed": False,
                "training_allowed": False,
            },
        }
    )
    summary_path = output_root / "summary.json"
    write_json_atomic(summary_path, summary)
    if summary["status"] != "PASS":
        raise ValueError(f"UnAV归档成员审计失败：{summary_path}")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--completion", type=Path, default=DEFAULT_COMPLETION)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser.parse_args()


if __name__ == "__main__":
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    arguments = parse_args()
    result = run(
        arguments.completion,
        arguments.archive_root,
        arguments.ledger,
        arguments.output_root,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
