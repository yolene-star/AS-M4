#!/usr/bin/env python3
"""审计 Dynamic Selector V2 V1 完整稀疏 validation 的官方媒体资格。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any

from intersuit.harness.dynamic_selector_v2_localization import validate_s1_manifest_records


SAMPLE_RATE = 16_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_json(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise ValueError(proc.stderr.strip()[-1000:])
    return json.loads(proc.stdout)


def audit_one(path: Path, expected_duration: float, tolerance: float) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path.resolve()),
        "expected_duration": expected_duration,
        "status": "rejected",
        "reasons": [],
    }
    if not path.is_file() or path.stat().st_size == 0:
        result["reasons"].append("missing_or_empty_media")
        return result
    result["bytes"] = path.stat().st_size
    result["media_sha256"] = sha256(path)
    try:
        payload = run_json(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-show_entries",
                "stream=codec_type,codec_name,sample_rate,channels",
                "-of",
                "json",
                str(path),
            ]
        )
    except (ValueError, json.JSONDecodeError) as error:
        result["reasons"].append("ffprobe_failed")
        result["probe_error"] = str(error)
        return result
    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration = float((payload.get("format") or {}).get("duration") or 0.0)
    result.update(
        {
            "actual_duration": duration,
            "duration_error": duration - expected_duration,
            "video_codec": video.get("codec_name") if video else None,
            "audio_codec": audio.get("codec_name") if audio else None,
            "audio_sample_rate": int(audio.get("sample_rate") or 0) if audio else None,
            "audio_channels": int(audio.get("channels") or 0) if audio else None,
        }
    )
    if video is None:
        result["reasons"].append("missing_video_stream")
    if audio is None:
        result["reasons"].append("missing_audio_stream")
    if not math.isfinite(duration) or abs(duration - expected_duration) > tolerance:
        result["reasons"].append("duration_mismatch")
    if audio is not None:
        decode = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-f",
                "f32le",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        result["audio_decode_returncode"] = decode.returncode
        if decode.returncode:
            result["reasons"].append("full_audio_decode_failed")
            result["audio_decode_error"] = decode.stderr.decode(errors="replace")[-1000:]
    result["status"] = "accepted" if not result["reasons"] else "rejected"
    return result


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-tolerance", type=float, default=0.25)
    parser.add_argument("--sparse-ratio", type=float, default=0.10)
    args = parser.parse_args()

    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("拒绝运行：必须通过 conda run -n M4 启动")
    records = json.loads(args.manifest.read_text(encoding="utf-8"))
    if validate_s1_manifest_records(records) != "validation":
        raise ValueError("full V1 media audit must use validation")
    allowlist = json.loads(args.allowlist.read_text(encoding="utf-8"))
    if allowlist.get("artifact_kind") != "dynamic_selector_v2_v1_full_validation_media_allowlist":
        raise ValueError("invalid full-validation allowlist")
    entries = allowlist.get("entries", [])
    allowed_ids = {str(entry["video_id"]) for entry in entries}
    sparse_records = [
        record
        for record in records
        if float(record["union_duration_ratio"]) <= args.sparse_ratio
    ]
    sparse_ids = {str(record["video_id"]) for record in sparse_records}
    if sparse_ids != allowed_ids:
        raise ValueError("allowlist does not exactly match sparse validation physical videos")
    invalid_timestamp_samples = []
    for record in sparse_records:
        duration = float(record["duration"])
        if any(
            not (0 <= float(start) < float(end) <= duration + 1e-9)
            for start, end in record["target_intervals"]
        ):
            invalid_timestamp_samples.append(str(record["sample_id"]))

    audit_entries = []
    for index, entry in enumerate(entries, start=1):
        video_id = str(entry["video_id"])
        row = audit_one(
            args.media_root / f"{video_id}.mp4",
            float(entry["duration"]),
            args.duration_tolerance,
        )
        row.update({"video_id": video_id, "selection_rank": int(entry["selection_rank"])})
        audit_entries.append(row)
        if index % 10 == 0 or index == len(entries):
            print(
                json.dumps(
                    {
                        "progress": f"{index}/{len(entries)}",
                        "accepted": sum(item["status"] == "accepted" for item in audit_entries),
                        "rejected": sum(item["status"] != "accepted" for item in audit_entries),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )

    rejected = [entry for entry in audit_entries if entry["status"] != "accepted"]
    status = "PASS" if not rejected and not invalid_timestamp_samples else "FAIL"
    payload = {
        "artifact_kind": "dynamic_selector_v2_v1_full_validation_media_audit",
        "status": status,
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV"),
        "manifest_sha256": sha256(args.manifest),
        "allowlist_sha256": sha256(args.allowlist),
        "media_root": str(args.media_root.resolve()),
        "config": {
            "split": "validation",
            "sparse_ratio": args.sparse_ratio,
            "duration_tolerance": args.duration_tolerance,
            "decode_sample_rate": SAMPLE_RATE,
            "decode_channels": 1,
            "full_audio_decode": True,
        },
        "sample_count": len(sparse_records),
        "video_count": len(entries),
        "accepted_count": len(audit_entries) - len(rejected),
        "rejected_count": len(rejected),
        "invalid_timestamp_sample_count": len(invalid_timestamp_samples),
        "invalid_timestamp_samples": invalid_timestamp_samples,
        "entries": audit_entries,
    }
    write_json(args.output, payload)
    print(
        json.dumps(
            {
                "status": status,
                "accepted_count": payload["accepted_count"],
                "rejected_count": payload["rejected_count"],
                "invalid_timestamp_sample_count": len(invalid_timestamp_samples),
                "output": str(args.output),
                "output_sha256": sha256(args.output),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    if status != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
