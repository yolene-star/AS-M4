#!/usr/bin/env python3
"""可恢复地选择性解压并资格审计 UnAV S1-R 全量train/validation媒体。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from extract_m4_ptpa_unav_s1_canary20_v1 import (  # noqa: E402
    DEFAULT_ARCHIVE_ROOT,
    DEFAULT_COMPLETION,
    DEFAULT_FFMPEG,
    DEFAULT_FFPROBE,
    DEFAULT_LEDGER,
    DEFAULT_MEMBER_INDEX,
    extract_selected,
    probe_and_decode,
    read_jsonl,
    sha256,
    write_json_atomic,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/m4_ptpa_unav_s1_reconstruction_v1.json"
DEFAULT_OUTPUT_ROOT = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/full_train_validation_media_v1"
PASS_STATUS = "PASS_FULL_TRAIN_VALIDATION_MEDIA_QUALIFIED_EFFECT_LOCKED"


def validate_scope(
    contract: dict[str, Any], ledger_path: Path, member_index_path: Path
) -> dict[str, Any]:
    if contract.get("status") != "full_train_validation_media_qualification_authorized_effect_locked":
        raise PermissionError("UnAV S1-R全量train/validation媒体资格未授权")
    scope = contract["full_train_validation_media_qualification"]
    permissions = contract["permissions"]
    if permissions.get("full_train_validation_media_qualification_allowed") is not True:
        raise PermissionError("UnAV S1-R全量媒体资格执行权限未开启")
    if permissions.get("archive_extraction_allowed") is not False:
        raise PermissionError("UnAV一般归档解压必须保持关闭")
    for key in (
        "selective_train_validation_extraction_allowed", "feature_extraction_allowed",
        "effect_access_allowed", "test_media_download_allowed",
        "test_feature_extraction_allowed", "test_effect_access_allowed",
        "training_allowed", "redistribution_allowed",
    ):
        if permissions.get(key) is not False:
            raise PermissionError(f"UnAV S1-R全量媒体资格禁止项漂移：{key}")
    if sha256(ledger_path) != scope["ledger_sha256"]:
        raise ValueError("UnAV S1-R ledger SHA漂移")
    if sha256(member_index_path) != scope["member_index_sha256"]:
        raise ValueError("UnAV S1-R member index SHA漂移")
    return scope


def build_selection(
    ledger_path: Path,
    member_index_path: Path,
    scope: dict[str, Any],
) -> list[dict[str, Any]]:
    ledger_rows = read_jsonl(ledger_path)
    member_index = {
        row["physical_media_id"]: row for row in read_jsonl(member_index_path)
    }
    eligible = [
        row
        for row in ledger_rows
        if row.get("eligible") is True and row.get("official_split") in {"train", "validation"}
    ]
    eligible.sort(key=lambda row: (row["official_split"], row["physical_media_id"]))
    counts = Counter(row["official_split"] for row in eligible)
    if counts != {"train": int(scope["train"]), "validation": int(scope["validation"])}:
        raise ValueError(f"UnAV S1-R全量split数量漂移：{counts}")
    if len(eligible) != int(scope["requested"]) or any(
        row["official_split"] == "test" for row in eligible
    ):
        raise PermissionError("UnAV S1-R全量allowlist数量或test边界漂移")
    selection = []
    for rank, metadata in enumerate(eligible, start=1):
        video_id = str(metadata["physical_media_id"])
        member = member_index.get(video_id)
        if (
            member is None
            or member.get("metadata_present") is not True
            or member.get("eligible_after_known_history") is not True
            or member.get("official_split") != metadata["official_split"]
        ):
            raise ValueError(f"UnAV S1-R成员映射或资格漂移：{video_id}")
        selection.append(
            {
                "plan": {
                    "physical_media_id": video_id,
                    "official_split": metadata["official_split"],
                    "selection_rank": rank,
                },
                "metadata": metadata,
                "member": member,
            }
        )
    media_bytes = sum(int(item["member"]["media_size_bytes"]) for item in selection)
    duration = sum(float(item["metadata"]["duration_sec"]) for item in selection)
    events = sum(int(item["metadata"]["event_count"]) for item in selection)
    if media_bytes != int(scope["expected_media_bytes"]):
        raise ValueError("UnAV S1-R全量媒体字节漂移")
    if abs(duration - float(scope["expected_duration_sec"])) > 1e-3:
        raise ValueError("UnAV S1-R全量时长和漂移")
    if events != int(scope["expected_event_count"]):
        raise ValueError("UnAV S1-R全量事件数漂移")
    return selection


def cached_passed_result(
    result_path: Path, media_path: Path, expected_sha: str, expected_size: int
) -> dict[str, Any] | None:
    if not result_path.is_file():
        return None
    row = json.loads(result_path.read_text(encoding="utf-8"))
    if row.get("passed") is not True:
        return None
    if (
        row.get("sha256") != expected_sha
        or int(row.get("size", -1)) != expected_size
        or not media_path.is_file()
        or media_path.stat().st_size != expected_size
        or sha256(media_path) != expected_sha
    ):
        raise ValueError(f"UnAV S1-R缓存结果与媒体漂移：{media_path.name}")
    return row


def audit_one(
    item: dict[str, Any],
    extracted_row: dict[str, Any],
    per_file_root: Path,
    ffprobe: Path,
    ffmpeg: Path,
    duration_tolerance: float,
    event_tolerance: float,
) -> dict[str, Any]:
    video_id = item["plan"]["physical_media_id"]
    media_path = Path(extracted_row["path"])
    result_path = per_file_root / f"{video_id}.json"
    cached = cached_passed_result(
        result_path,
        media_path,
        extracted_row["sha256"],
        int(extracted_row["size"]),
    )
    if cached is not None:
        cached["audit_status"] = "reused_passed_result"
        return cached
    try:
        audit = probe_and_decode(
            media_path,
            ffprobe,
            ffmpeg,
            item["metadata"],
            duration_tolerance,
            event_tolerance,
        )
        row = {
            "physical_media_id": video_id,
            "official_split": item["plan"]["official_split"],
            "path": str(media_path),
            "size": int(extracted_row["size"]),
            "sha256": extracted_row["sha256"],
            "event_count": int(item["metadata"]["event_count"]),
            "audit": audit,
            "passed": True,
            "error": None,
            "audit_status": "audited_pass",
        }
    except Exception as error:  # noqa: BLE001 - 需要逐文件保留失败并继续完整资格审计
        row = {
            "physical_media_id": video_id,
            "official_split": item["plan"]["official_split"],
            "path": str(media_path),
            "size": int(extracted_row["size"]),
            "sha256": extracted_row["sha256"],
            "event_count": int(item["metadata"]["event_count"]),
            "audit": None,
            "passed": False,
            "error": f"{type(error).__name__}: {error}",
            "audit_status": "audited_fail",
        }
    write_json_atomic(result_path, row)
    return row


def write_progress(
    path: Path, requested: int, results: list[dict[str, Any]], phase: str
) -> None:
    write_json_atomic(
        path,
        {
            "artifact_kind": "m4_ptpa_unav_s1r_full_media_progress",
            "format_version": 1,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "requested": requested,
            "completed": len(results),
            "passed": sum(row.get("passed") is True for row in results),
            "failed": sum(row.get("passed") is False for row in results),
            "test_media_accessed": False,
            "feature_extraction_performed": False,
            "effect_accessed": False,
            "training_performed": False,
        },
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    scope = validate_scope(contract, args.ledger, args.member_index)
    completion = json.loads(args.completion.read_text(encoding="utf-8"))
    if completion.get("status") != "PASS_ARCHIVE_COMPLETE_VERIFIED_EFFECT_LOCKED":
        raise PermissionError("UnAV归档终态未通过")
    if not args.ffprobe.is_file() or not args.ffmpeg.is_file():
        raise FileNotFoundError("FFprobe/FFmpeg不存在")
    if args.workers < 1:
        raise ValueError("--workers必须大于等于1")
    selection = build_selection(args.ledger, args.member_index, scope)
    requested = len(selection)
    args.output_root.mkdir(parents=True, exist_ok=True)
    progress_path = args.output_root / "progress.json"
    write_progress(progress_path, requested, [], "selective_extraction")
    archive_paths = [args.archive_root / row["name"] for row in completion["results"]]
    extracted = extract_selected(archive_paths, selection, args.output_root / "media")
    media_files = {
        path.name for path in (args.output_root / "media").iterdir() if path.is_file()
    }
    expected_files = {
        f"{item['plan']['physical_media_id']}.mp4" for item in selection
    }
    if media_files != expected_files:
        raise ValueError("UnAV S1-R全量媒体文件集合不精确")
    write_progress(progress_path, requested, [], "parallel_media_audit")
    per_file_root = args.output_root / "per_file"
    per_file_root.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                audit_one,
                item,
                extracted[item["plan"]["physical_media_id"]],
                per_file_root,
                args.ffprobe,
                args.ffmpeg,
                float(scope["duration_max_abs_delta_sec"]),
                float(scope["event_offset_media_tolerance_sec"]),
            ): item["plan"]["physical_media_id"]
            for item in selection
        }
        for future in as_completed(futures):
            results.append(future.result())
            if len(results) % 100 == 0 or len(results) == requested:
                write_progress(progress_path, requested, results, "parallel_media_audit")
                print(
                    json.dumps(
                        {
                            "event": "audit_progress",
                            "completed": len(results),
                            "requested": requested,
                            "passed": sum(row["passed"] for row in results),
                            "failed": sum(not row["passed"] for row in results),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    results.sort(key=lambda row: (row["official_split"], row["physical_media_id"]))
    results_path = args.output_root / "full_media_results.jsonl"
    temporary = args.output_root / ".full_media_results.jsonl.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(results_path)
    passed = sum(row["passed"] for row in results)
    failed = requested - passed
    successful_audits = [row["audit"] for row in results if row["passed"]]
    summary = {
        "artifact_kind": "m4_ptpa_unav_s1r_full_train_validation_media_qualification",
        "format_version": 1,
        "status": PASS_STATUS if failed == 0 else "FAIL_FULL_MEDIA_QUALIFICATION",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested": requested,
        "passed": passed,
        "failed": failed,
        "split_counts": dict(sorted(Counter(row["official_split"] for row in results).items())),
        "total_media_bytes": sum(int(row["size"]) for row in results),
        "duration_max_abs_delta_sec": max(
            (abs(float(row["duration_delta_sec"])) for row in successful_audits),
            default=None,
        ),
        "results_path": str(results_path),
        "results_sha256": sha256(results_path),
        "progress_path": str(progress_path),
        "decode_workers": args.workers,
        "test_media_accessed": False,
        "permissions": {
            "feature_extraction_allowed": False,
            "effect_access_allowed": False,
            "test_media_access_allowed": False,
            "test_effect_access_allowed": False,
            "training_allowed": False,
            "redistribution_allowed": False,
        },
    }
    write_json_atomic(args.output_root / "summary.json", summary)
    write_progress(progress_path, requested, results, "complete")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--completion", type=Path, default=DEFAULT_COMPLETION)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--member-index", type=Path, default=DEFAULT_MEMBER_INDEX)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--ffprobe", type=Path, default=DEFAULT_FFPROBE)
    parser.add_argument("--ffmpeg", type=Path, default=DEFAULT_FFMPEG)
    parser.add_argument("--workers", type=int, default=16)
    return parser.parse_args()


if __name__ == "__main__":
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    result = run(parse_args())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] != PASS_STATUS:
        raise SystemExit(1)
