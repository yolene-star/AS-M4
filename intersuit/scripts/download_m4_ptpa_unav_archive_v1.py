#!/usr/bin/env python3
"""断点续传下载并逐卷校验 UnAV-100 官方65.78GB媒体归档。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/m4_ptpa_unav_s1_reconstruction_v1.json"
DEFAULT_OUTPUT = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/official_archive"
DEFAULT_STATUS = DEFAULT_OUTPUT / "archive_download_status.json"
DEFAULT_CURL = Path("/usr/bin/curl")
HF_REPO = "https://huggingface.co/datasets/ttgeng233/UnAV-100/resolve"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def validate_contract(contract: dict[str, Any]) -> list[dict[str, Any]]:
    if contract.get("status") != "full_archive_download_authorized_effect_locked":
        raise PermissionError("UnAV完整归档下载状态未授权")
    permissions = contract["permissions"]
    if permissions.get("full_archive_download_allowed_by_explicit_user_authorization") is not True:
        raise PermissionError("UnAV完整归档scoped权限未授权")
    for key in (
        "archive_extraction_allowed", "train_validation_media_download_allowed",
        "canary2_train_validation_media_download_allowed",
        "canary20_train_validation_media_download_allowed", "feature_extraction_allowed",
        "effect_access_allowed", "test_media_download_allowed",
        "test_feature_extraction_allowed", "test_effect_access_allowed",
        "training_allowed", "redistribution_allowed",
    ):
        if permissions.get(key) is not False:
            raise PermissionError(f"UnAV归档阶段禁止项漂移：{key}")
    archive = contract["media_distribution"]
    files = list(archive["archive_files"])
    if len(files) != archive["archive_parts"]:
        raise ValueError("UnAV归档卷数合同漂移")
    if sum(row["size"] for row in files) != archive["archive_total_bytes"]:
        raise ValueError("UnAV归档总大小合同漂移")
    if len({row["name"] for row in files}) != len(files):
        raise ValueError("UnAV归档文件名重复")
    return files


def archive_url(revision: str, name: str) -> str:
    return f"{HF_REPO}/{revision}/{name}"


def download_part(
    item: dict[str, Any], revision: str, output: Path, curl: Path,
    retries: int = 12,
) -> dict[str, Any]:
    name = item["name"]
    expected_size = int(item["size"])
    expected_sha = str(item["sha256"])
    target = output / name
    partial = output / f"{name}.partial"
    if target.is_file():
        observed_size = target.stat().st_size
        observed_sha = sha256(target) if observed_size == expected_size else None
        if observed_size == expected_size and observed_sha == expected_sha:
            return {
                "name": name,
                "status": "reused_verified",
                "size": observed_size,
                "sha256": observed_sha,
                "download_seconds": 0.0,
            }
        raise ValueError(f"已存在UnAV归档卷不匹配，拒绝覆盖：{target}")
    if partial.is_file() and partial.stat().st_size > expected_size:
        raise ValueError(f"UnAV partial超过预期大小，拒绝继续：{partial}")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    print(
        json.dumps(
            {
                "event": "download_start",
                "name": name,
                "expected_size": expected_size,
                "resume_bytes": partial.stat().st_size if partial.is_file() else 0,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    command = [
        str(curl), "--fail", "--location", "--retry", str(retries),
        "--retry-delay", "5", "--retry-connrefused", "--continue-at", "-",
        "--progress-bar", "--output", str(partial), archive_url(revision, name),
    ]
    proc = subprocess.run(command, check=False)
    elapsed = time.monotonic() - started
    if proc.returncode:
        return {
            "name": name,
            "status": "download_failed",
            "returncode": proc.returncode,
            "partial_bytes": partial.stat().st_size if partial.is_file() else 0,
            "download_seconds": elapsed,
        }
    observed_size = partial.stat().st_size if partial.is_file() else 0
    if observed_size != expected_size:
        return {
            "name": name,
            "status": "size_mismatch",
            "expected_size": expected_size,
            "observed_size": observed_size,
            "download_seconds": elapsed,
        }
    print(json.dumps({"event": "sha256_start", "name": name}, ensure_ascii=False), flush=True)
    observed_sha = sha256(partial)
    if observed_sha != expected_sha:
        return {
            "name": name,
            "status": "sha256_mismatch",
            "expected_sha256": expected_sha,
            "observed_sha256": observed_sha,
            "size": observed_size,
            "download_seconds": elapsed,
        }
    partial.replace(target)
    return {
        "name": name,
        "status": "downloaded_verified",
        "size": observed_size,
        "sha256": observed_sha,
        "download_seconds": elapsed,
        "mean_mib_per_sec": (observed_size / (1024 * 1024)) / elapsed if elapsed > 0 else None,
    }


def download_part_with_retries(
    item: dict[str, Any], revision: str, output: Path, curl: Path,
    retries: int = 12, part_attempts: int = 24, retry_delay: int = 30,
) -> dict[str, Any]:
    if part_attempts < 1:
        raise ValueError("--part-attempts必须大于等于1")
    for attempt in range(1, part_attempts + 1):
        result = download_part(item, revision, output, curl, retries)
        result["part_attempt"] = attempt
        if result["status"] in {"downloaded_verified", "reused_verified"}:
            return result
        retryable = result["status"] == "download_failed" or (
            result["status"] == "size_mismatch"
            and result.get("observed_size", 0) < result.get("expected_size", 0)
        )
        if not retryable or attempt == part_attempts:
            return result
        print(
            json.dumps(
                {
                    "event": "part_retry",
                    "name": item["name"],
                    "previous_status": result["status"],
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "partial_bytes": result.get("partial_bytes", 0),
                    "retry_delay_seconds": retry_delay,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        time.sleep(retry_delay)
    raise AssertionError("不可达的分卷重试状态")


def download_parts(
    files: list[dict[str, Any]], revision: str, output: Path, curl: Path,
    retries: int, part_attempts: int, retry_delay: int, workers: int,
    progress_callback: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    if workers < 1:
        raise ValueError("--workers必须大于等于1")

    def invoke(item: dict[str, Any]) -> dict[str, Any]:
        return download_part_with_retries(
            item, revision, output, curl, retries, part_attempts, retry_delay,
        )

    completed: dict[str, dict[str, Any]] = {}

    def record(result: dict[str, Any]) -> None:
        completed[result["name"]] = result
        ordered = [completed[item["name"]] for item in files if item["name"] in completed]
        if progress_callback is not None:
            progress_callback(ordered)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    if workers == 1:
        for item in files:
            result = invoke(item)
            record(result)
            if result["status"] not in {"downloaded_verified", "reused_verified"}:
                break
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(files))) as executor:
            futures = {executor.submit(invoke, item): item["name"] for item in files}
            for future in as_completed(futures):
                record(future.result())
    return [completed[item["name"]] for item in files if item["name"] in completed]


def status_payload(
    revision: str, requested_parts: int, results: list[dict[str, Any]], status: str,
) -> dict[str, Any]:
    return {
        "artifact_kind": "m4_ptpa_unav_official_archive_download_status",
        "format_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "revision": revision,
        "requested_parts": requested_parts,
        "completed_verified": sum(
            row["status"] in {"downloaded_verified", "reused_verified"}
            for row in results
        ),
        "failed": sum(
            row["status"] not in {"downloaded_verified", "reused_verified"}
            for row in results
        ),
        "in_progress": requested_parts - len(results),
        "archive_extraction_allowed": False,
        "feature_extraction_allowed": False,
        "effect_access_allowed": False,
        "test_effect_access_allowed": False,
        "training_allowed": False,
        "status": status,
        "results": results,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    if not args.curl.is_file():
        raise FileNotFoundError(f"curl不存在：{args.curl}")
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    files = validate_contract(contract)
    revision = contract["media_distribution"]["archive_revision"]
    if args.only_index is not None:
        if not 0 <= args.only_index < len(files):
            raise ValueError("--only-index越界")
        files = [files[args.only_index]]
    requested_parts = len(files)

    def update_status(results: list[dict[str, Any]]) -> None:
        write_json(args.status, status_payload(revision, requested_parts, results, "RUNNING"))

    results = download_parts(
        files, revision, args.output, args.curl, args.retries,
        args.part_attempts, args.part_retry_delay, args.workers, update_status,
    )
    final_status = "PASS" if (
        len(results) == requested_parts and all(
            row["status"] in {"downloaded_verified", "reused_verified"}
            for row in results
        )
    ) else "FAIL"
    final = status_payload(revision, requested_parts, results, final_status)
    write_json(args.status, final)
    return final


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--curl", type=Path, default=DEFAULT_CURL)
    parser.add_argument("--retries", type=int, default=12)
    parser.add_argument("--part-attempts", type=int, default=24)
    parser.add_argument("--part-retry-delay", type=int, default=30)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--only-index", type=int)
    return parser.parse_args()


if __name__ == "__main__":
    result = run(parse_args())
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("status", "requested_parts", "completed_verified", "failed")
            },
            ensure_ascii=False,
        )
    )
