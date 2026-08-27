#!/usr/bin/env python3
"""独立复核 UnAV S1 canary20媒体资格产物、文件集合与权限边界。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_PLAN = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/qualification_v1/unav_s1_media_canary20_plan_locked.jsonl"
DEFAULT_ROOT = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/canary20_media_v1"
DEFAULT_RESULTS = DEFAULT_ROOT / "canary20_media_results.jsonl"
DEFAULT_SUMMARY = DEFAULT_ROOT / "summary.json"
DEFAULT_MEDIA_ROOT = DEFAULT_ROOT / "media"
DEFAULT_OUTPUT = DEFAULT_ROOT / "independent_audit.json"
PASS_STATUS = "PASS_INDEPENDENT_CANARY20_MEDIA_AUDIT"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def audit(
    plan_path: Path,
    results_path: Path,
    summary_path: Path,
    media_root: Path,
) -> dict[str, Any]:
    plan = read_jsonl(plan_path)
    results = read_jsonl(results_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("status") != "PASS_CANARY20_MEDIA_QUALIFIED_EFFECT_LOCKED":
        raise ValueError("UnAV canary20 summary未通过")
    if summary.get("plan_sha256") != sha256(plan_path):
        raise ValueError("UnAV canary20 plan SHA不一致")
    if summary.get("results_sha256") != sha256(results_path):
        raise ValueError("UnAV canary20 results SHA不一致")
    plan_ids = [str(row["physical_media_id"]) for row in plan]
    result_ids = [str(row["physical_media_id"]) for row in results]
    if len(plan_ids) != 20 or len(set(plan_ids)) != 20 or set(plan_ids) != set(result_ids):
        raise ValueError("UnAV canary20计划与结果物理ID不一致")
    splits = Counter(row["official_split"] for row in results)
    if splits != {"train": 10, "validation": 10} or any(
        row["official_split"] == "test" for row in results
    ):
        raise PermissionError("UnAV canary20 split越界")
    expected_files = {f"{video_id}.mp4" for video_id in result_ids}
    observed_files = {path.name for path in media_root.iterdir() if path.is_file()}
    if observed_files != expected_files:
        raise ValueError("UnAV canary20媒体文件集合不精确")
    for row in results:
        path = Path(row["path"])
        if path.parent != media_root or path.name != f"{row['physical_media_id']}.mp4":
            raise ValueError(f"UnAV canary20结果路径越界：{path}")
        if path.stat().st_size != int(row["size"]) or sha256(path) != row["sha256"]:
            raise ValueError(f"UnAV canary20媒体大小或SHA漂移：{path.name}")
        audit_row = row["audit"]
        if (
            row.get("passed") is not True
            or abs(float(audit_row["duration_delta_sec"])) > 1.0
            or float(audit_row["max_event_offset_sec"]) > float(audit_row["media_duration_sec"]) + 1.0
            or audit_row.get("full_media_decode_passed") is not True
            or audit_row.get("audio_decode_16khz_mono_passed") is not True
        ):
            raise ValueError(f"UnAV canary20媒体审计字段失败：{path.name}")
    permissions = summary["permissions"]
    for key in (
        "feature_extraction_allowed", "effect_access_allowed",
        "test_media_access_allowed", "test_effect_access_allowed",
        "training_allowed", "redistribution_allowed",
    ):
        if permissions.get(key) is not False:
            raise PermissionError(f"UnAV canary20 summary权限漂移：{key}")
    return {
        "artifact_kind": "m4_ptpa_unav_s1_canary20_independent_media_audit",
        "format_version": 1,
        "status": PASS_STATUS,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows": len(results),
        "split_counts": dict(sorted(splits.items())),
        "total_media_bytes": sum(int(row["size"]) for row in results),
        "plan_sha256": sha256(plan_path),
        "results_sha256": sha256(results_path),
        "summary_sha256": sha256(summary_path),
        "media_sha_mismatch_count": 0,
        "unexpected_media_file_count": 0,
        "test_media_accessed": False,
        "feature_extraction_performed": False,
        "effect_accessed": False,
        "training_performed": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--media-root", type=Path, default=DEFAULT_MEDIA_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    arguments = parse_args()
    result = audit(
        arguments.plan,
        arguments.results,
        arguments.summary,
        arguments.media_root,
    )
    write_json_atomic(arguments.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
