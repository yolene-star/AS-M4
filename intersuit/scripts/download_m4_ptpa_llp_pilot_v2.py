#!/usr/bin/env python3
"""按 LLP 官方协议下载冻结 pilot，并增加可审计的 canary/验收门禁。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
PILOT = (
    INTERSUIT_ROOT
    / "harness/artifacts/m4_ptpa_audio_semantic_fusion_v1/llp_metadata_qualification_v2/llp_pilot_plan_locked_not_allowed.jsonl"
)
RESPONSIBILITY_LOCK = INTERSUIT_ROOT / "configs/m4_ptpa_audio_dataset_responsibility_v2.json"
DEFAULT_OUTPUT = INTERSUIT_ROOT / "datasets/LLP_PILOT_MEDIA_V2"
EXPECTED_PILOT_SHA256 = "4df243662d9b98a049b80fd881052781ea4bbfa1f30ae1e420aafab84e5f3ff9"
OFFICIAL_REPOSITORY_COMMIT = "fde56118f2c2e7c19188dfefa2569a37b70054fe"
OFFICIAL_PROTOCOL = {
    "source": "https://github.com/YapengTian/AVVP-ECCV20",
    "repository_commit": OFFICIAL_REPOSITORY_COMMIT,
    "download_format": "best",
    "clip_duration_sec": 10.0,
    "video_codec": "libx264",
    "audio_codec": "aac",
    "output_name": "<11-char-youtube-id>.mp4",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def verify_plan_inputs() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if sha256(PILOT) != EXPECTED_PILOT_SHA256:
        raise ValueError("LLP 冻结 pilot SHA 漂移")
    lock = read_json(RESPONSIBILITY_LOCK)
    qualification = lock.get("datasets", {}).get("llp", {}).get("pre_download_qualification", {})
    if (
        lock.get("permissions", {}).get("llp_pilot_download_allowed") is not False
        or qualification.get("pilot_manifest_sha256") != EXPECTED_PILOT_SHA256
        or qualification.get("pilot_media_download_allowed") is not False
        or lock.get("permissions", {}).get("feature_extraction_allowed") is not False
        or lock.get("permissions", {}).get("training_allowed") is not False
    ):
        raise ValueError("LLP 下载前责任锁合同漂移")
    rows = read_jsonl(PILOT)
    ids = [str(row.get("physical_media_id", "")) for row in rows]
    if (
        len(rows) != 150
        or len(set(ids)) != 150
        or any(len(value) != 11 for value in ids)
        or any(row.get("official_split") not in {"train", "validation"} for row in rows)
        or any(row.get("download_allowed") is not False for row in rows)
        or any(float(row["end_sec"]) - float(row["start_sec"]) != 10.0 for row in rows)
    ):
        raise ValueError("LLP pilot 行合同漂移")
    return lock, rows


def build_jobs(rows: list[dict[str, Any]], output_root: Path, scope: str) -> list[dict[str, Any]]:
    selected = rows[:2] if scope == "canary2" else rows
    return [
        {
            "job_index": index,
            "sample_id": row["sample_id"],
            "clip_id": row["clip_id"],
            "physical_media_id": row["physical_media_id"],
            "official_split": row["official_split"],
            "source_url": row["source_url"],
            "start_sec": float(row["start_sec"]),
            "end_sec": float(row["end_sec"]),
            "expected_duration_sec": 10.0,
            "output_path": str(output_root / "video" / f"{row['physical_media_id']}.mp4"),
            "pilot_sha256": EXPECTED_PILOT_SHA256,
            "official_protocol": OFFICIAL_PROTOCOL,
            "media_download_allowed": False,
            "feature_extraction_allowed": False,
            "effect_access_allowed": False,
        }
        for index, row in enumerate(selected)
    ]


def verify_authorization(path: Path, scope: str) -> dict[str, Any]:
    value = read_json(path)
    expected_count = 2 if scope == "canary2" else 150
    if (
        value.get("status") != "llp_pilot_media_download_authorized"
        or value.get("scope") != scope
        or value.get("row_count") != expected_count
        or value.get("pilot_sha256") != EXPECTED_PILOT_SHA256
        or value.get("internal_noncommercial_research_only") is not True
        or value.get("redistribution_allowed") is not False
        or value.get("feature_extraction_allowed") is not False
        or value.get("effect_access_allowed") is not False
    ):
        raise ValueError("LLP 媒体下载 authorization lock 不合格")
    credential_mode = value.get("credential_mode")
    if credential_mode == "rotated_cookie":
        if value.get("credentials_rotated_after_chat_exposure") is not True:
            raise ValueError("LLP rotated-cookie authorization 未证明凭据轮换")
    elif credential_mode == "anonymous":
        if value.get("compromised_credentials_used") is not False:
            raise ValueError("LLP anonymous authorization 禁止使用泄露凭据")
    else:
        raise ValueError("LLP authorization credential_mode 非法")
    return value


def probe_media(path: Path, ffprobe_executable: str = "ffprobe") -> dict[str, Any]:
    result = subprocess.run(
        [
            ffprobe_executable,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip()[-4000:] or f"ffprobe exit {result.returncode}")
    return json.loads(result.stdout)


def validate_media(path: Path, payload: dict[str, Any], expected_duration: float = 10.0) -> dict[str, Any]:
    streams = payload.get("streams", [])
    try:
        duration = float(payload.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = -1.0
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    checks = {
        "file_exists": path.is_file(),
        "nonempty": path.is_file() and path.stat().st_size > 0,
        "has_video_stream": bool(video_streams),
        "has_audio_stream": bool(audio_streams),
        "video_codec_h264": bool(video_streams) and video_streams[0].get("codec_name") == "h264",
        "audio_codec_aac": bool(audio_streams) and audio_streams[0].get("codec_name") == "aac",
        "audio_sample_rate_positive": bool(audio_streams)
        and int(audio_streams[0].get("sample_rate") or 0) > 0,
        "audio_channels_positive": bool(audio_streams)
        and int(audio_streams[0].get("channels") or 0) > 0,
        "duration_finite": math.isfinite(duration) and duration > 0,
        "duration_within_one_second": abs(duration - expected_duration) <= 1.0,
    }
    return {
        "status": "pass" if all(checks.values()) else "fail",
        "bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": sha256(path) if path.is_file() else None,
        "duration_sec": duration,
        "video_codec": video_streams[0].get("codec_name") if video_streams else None,
        "audio_codec": audio_streams[0].get("codec_name") if audio_streams else None,
        "audio_sample_rate": int(audio_streams[0].get("sample_rate") or 0) if audio_streams else 0,
        "audio_channels": int(audio_streams[0].get("channels") or 0) if audio_streams else 0,
        "checks": checks,
    }


def run_checked(command: list[str]) -> None:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        details = "\n".join(value for value in (result.stdout.strip(), result.stderr.strip()) if value)
        raise RuntimeError(details[-4000:] if details else f"command exit {result.returncode}")


def download_job(
    job: dict[str, Any],
    *,
    output_root: Path,
    ytdlp: str,
    ffmpeg: str,
    ffprobe_executable: str,
    cookies_file: Path | None,
    cookies_from_browser: str | None,
) -> dict[str, Any]:
    final_path = Path(job["output_path"])
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if final_path.is_file():
        audit = validate_media(final_path, probe_media(final_path, ffprobe_executable))
        if audit["status"] == "pass":
            return {**job, **audit, "download_status": "reused", "credential_mode": "redacted"}
        raise ValueError(f"已存在媒体未通过验收，拒绝覆盖: {final_path}")

    temp_root = output_root / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{job['physical_media_id']}-", dir=temp_root) as temp:
        temp_dir = Path(temp)
        source_template = temp_dir / "source.%(ext)s"
        command = [
            ytdlp,
            "--ignore-config",
            "--no-playlist",
            "-f",
            "best",
            "-o",
            str(source_template),
            job["source_url"],
        ]
        if cookies_file is not None:
            command[1:1] = ["--cookies", str(cookies_file)]
        elif cookies_from_browser:
            command[1:1] = ["--cookies-from-browser", cookies_from_browser]
        run_checked(command)
        sources = [path for path in temp_dir.glob("source.*") if path.is_file()]
        if len(sources) != 1:
            raise ValueError(f"yt-dlp 源媒体数量异常: {len(sources)}")
        encoded = temp_dir / "clip.mp4"
        run_checked(
            [
                ffmpeg,
                "-ss",
                str(job["start_sec"]),
                "-i",
                str(sources[0]),
                "-t",
                str(job["expected_duration_sec"]),
                "-vcodec",
                "libx264",
                "-acodec",
                "aac",
                "-strict",
                "-2",
                "-movflags",
                "+faststart",
                "-y",
                "-loglevel",
                "error",
                str(encoded),
            ]
        )
        audit = validate_media(encoded, probe_media(encoded, ffprobe_executable))
        if audit["status"] != "pass":
            raise ValueError(f"下载媒体后验收失败: {job['sample_id']}; {audit['checks']}")
        os.replace(encoded, final_path)
    return {**job, **audit, "download_status": "downloaded", "credential_mode": "redacted"}


def build_review(jobs: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    return {
        "artifact_kind": "m4_ptpa_llp_official_protocol_download_plan_v2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "plan_ready_execution_locked",
        "scope": scope,
        "row_count": len(jobs),
        "pilot_sha256": EXPECTED_PILOT_SHA256,
        "official_protocol": OFFICIAL_PROTOCOL,
        "permissions": {
            "plan_generation_allowed": True,
            "media_download_allowed": False,
            "feature_extraction_allowed": False,
            "gpu_execution_allowed": False,
            "training_allowed": False,
            "effect_access_allowed": False,
        },
        "execution_requirements": [
            "fresh authorization lock bound to pilot SHA and exact scope",
            "anonymous mode with no credentials, or rotated-cookie mode after credential rotation",
            "internal noncommercial research only and no redistribution",
            "anonymous mode supplies no credential; rotated-cookie mode supplies exactly one source",
            "canary2 must pass before pilot150 authorization",
        ],
        "next_stage_allowed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--scope", choices=("canary2", "pilot150"), default="canary2")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorization-lock", type=Path)
    parser.add_argument("--cookies-file", type=Path)
    parser.add_argument("--cookies-from-browser")
    parser.add_argument("--yt-dlp", default="yt-dlp")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()

    _, rows = verify_plan_inputs()
    jobs = build_jobs(rows, args.output_root, args.scope)
    review = build_review(jobs, args.scope)
    write_jsonl(args.output_root / f"{args.scope}_download_plan_locked.jsonl", jobs)
    write_json(args.output_root / f"{args.scope}_download_review.json", review)

    if not args.execute:
        print(json.dumps({"status": review["status"], "scope": args.scope, "rows": len(jobs)}, ensure_ascii=False))
        return
    if args.authorization_lock is None:
        raise ValueError("--execute 必须提供 --authorization-lock")
    authorization = verify_authorization(args.authorization_lock, args.scope)
    credential_count = int(args.cookies_file is not None) + int(args.cookies_from_browser is not None)
    if authorization["credential_mode"] == "anonymous" and credential_count != 0:
        raise ValueError("anonymous 模式禁止提供 credential source")
    if authorization["credential_mode"] == "rotated_cookie" and credential_count != 1:
        raise ValueError("rotated-cookie 模式必须且只能提供一个 credential source")

    results: list[dict[str, Any]] = []
    results_path = args.output_root / f"{args.scope}_download_results.jsonl"
    for job in jobs:
        try:
            result = download_job(
                job,
                output_root=args.output_root,
                ytdlp=args.yt_dlp,
                ffmpeg=args.ffmpeg,
                ffprobe_executable=args.ffprobe,
                cookies_file=args.cookies_file,
                cookies_from_browser=args.cookies_from_browser,
            )
        except Exception as exc:
            result = {
                **job,
                "status": "fail",
                "download_status": "failed",
                "error": str(exc)[-4000:],
                "credential_mode": "redacted",
            }
        results.append(result)
        write_jsonl(results_path, results)
    summary = {
        "artifact_kind": "m4_ptpa_llp_official_protocol_download_results_v2",
        "scope": args.scope,
        "expected": len(jobs),
        "passed": sum(row.get("status") == "pass" for row in results),
        "failed": sum(row.get("status") != "pass" for row in results),
        "canary_execution_authorized": args.scope == "canary2",
        "media_qualification_passed": all(row.get("status") == "pass" for row in results),
        "next_stage_allowed": False,
        "media_download_allowed": False,
        "feature_extraction_allowed": False,
        "effect_access_allowed": False,
    }
    write_json(args.output_root / f"{args.scope}_download_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
