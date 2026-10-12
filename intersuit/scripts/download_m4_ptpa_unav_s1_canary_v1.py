#!/usr/bin/env python3
"""按官方流程兼容路径下载并审计 UnAV S1 train/validation canary2。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/m4_ptpa_unav_s1_reconstruction_v1.json"
DEFAULT_PLAN = (
    INTERSUIT_ROOT
    / "datasets/UNAV_S1_V1/qualification_v1/unav_s1_media_canary20_plan_locked.jsonl"
)
DEFAULT_PROBE = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/media_probe_v1/canary2_compat_probe.json"
DEFAULT_OUTPUT = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/media_canary2_v1"
DEFAULT_YTDLP = Path("/home/yjm/miniconda3/envs/llp-download/bin/yt-dlp")
DEFAULT_NODE = Path("/home/yjm/miniconda3/envs/llp-download/bin/node")
DEFAULT_FFMPEG = Path("/home/yjm/miniconda3/envs/llp-download/bin/ffmpeg-official-static")
DEFAULT_FFPROBE = Path("/home/yjm/miniconda3/envs/llp-download/bin/ffprobe")
SOURCE_FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def select_canary2(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for split in ("train", "validation"):
        candidates = [row for row in rows if row["official_split"] == split]
        if not candidates:
            raise ValueError(f"UnAV plan 缺少 {split}")
        output.append(candidates[0])
    return output


def validate_inputs(
    contract: dict[str, Any], plan: list[dict[str, Any]], probe: dict[str, Any]
) -> list[dict[str, Any]]:
    permissions = contract["permissions"]
    if contract.get("status") != "canary2_media_download_authorized_effect_locked":
        raise PermissionError("UnAV canary2 下载状态未授权")
    if permissions.get("canary2_train_validation_media_download_allowed") is not True:
        raise PermissionError("UnAV canary2 scoped 下载权限未授权")
    if permissions.get("train_validation_media_download_allowed") is not False:
        raise PermissionError("UnAV broader 下载权限必须关闭")
    for key in (
        "canary20_train_validation_media_download_allowed", "feature_extraction_allowed",
        "effect_access_allowed", "test_media_download_allowed",
        "test_feature_extraction_allowed", "test_effect_access_allowed", "training_allowed",
    ):
        if permissions.get(key) is not False:
            raise PermissionError(f"UnAV canary2 禁止项漂移：{key}")
    if contract["official_downloader"].get("cookies_allowed") is not False:
        raise PermissionError("UnAV canary2 禁止cookie")
    if probe.get("status") != "PASS" or probe.get("requested") != 2 or probe.get("available") != 2:
        raise PermissionError("UnAV compat canary2 probe 未通过")
    if probe.get("cookies_used") is not False or probe.get("client_or_po_token_args_used") is not False:
        raise PermissionError("UnAV probe 凭据边界漂移")
    selected = select_canary2(plan)
    probed = {row["physical_media_id"] for row in probe["results"] if row["status"] == "available"}
    if {row["physical_media_id"] for row in selected} != probed:
        raise PermissionError("UnAV canary2 plan/probe 物理ID不一致")
    if any(row["official_split"] == "test" for row in selected):
        raise PermissionError("UnAV canary2 禁止test")
    return selected


def ffprobe(path: Path, executable: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            str(executable), "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,codec_name,sample_rate,channels",
            "-of", "json", str(path),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise ValueError(f"ffprobe失败：{path}: {proc.stderr[-1000:]}")
    payload = json.loads(proc.stdout)
    streams = payload.get("streams", [])
    videos = [row for row in streams if row.get("codec_type") == "video"]
    audios = [row for row in streams if row.get("codec_type") == "audio"]
    if not videos or not audios:
        raise ValueError(f"UnAV媒体缺少音频或视频流：{path}")
    return {
        "duration_sec": float(payload["format"]["duration"]),
        "video_codec": videos[0].get("codec_name"),
        "audio_codec": audios[0].get("codec_name"),
        "audio_sample_rate": int(audios[0].get("sample_rate") or 0),
        "audio_channels": int(audios[0].get("channels") or 0),
    }


def run_command(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )


def download_one(
    row: dict[str, Any], output: Path, ytdlp: Path, node: Path,
    ffmpeg: Path, ffprobe_executable: Path, timeout: int,
) -> dict[str, Any]:
    video_id = row["physical_media_id"]
    target = output / f"{video_id}.mp4"
    if target.is_file() and target.stat().st_size > 0:
        media = ffprobe(target, ffprobe_executable)
        return {
            "physical_media_id": video_id,
            "official_split": row["official_split"],
            "status": "reused",
            "media_path": str(target.resolve()),
            "media_bytes": target.stat().st_size,
            "media_sha256": sha256(target),
            **media,
            "cookies_used": False,
            "client_or_po_token_args_used": False,
        }
    full_template = output / f"{video_id}_full_video.%(ext)s"
    command = [
        str(ytdlp), "--ignore-config", row["source_url"],
        "--js-runtimes", f"node:{node}", "-o", str(full_template),
        "-f", SOURCE_FORMAT, "--merge-output-format", "mp4",
    ]
    proc = run_command(command, timeout)
    full = output / f"{video_id}_full_video.mp4"
    if proc.returncode or not full.is_file() or full.stat().st_size == 0:
        return {
            "physical_media_id": video_id,
            "official_split": row["official_split"],
            "status": "download_failed",
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-500:],
            "stderr_tail": proc.stderr[-1500:],
            "media_bytes": 0,
            "cookies_used": False,
            "client_or_po_token_args_used": False,
        }
    partial = output / f"{video_id}.partial.mp4"
    duration = float(row["source_end_sec"]) - float(row["source_start_sec"])
    trim = [
        str(ffmpeg), "-ss", str(row["source_start_sec"]), "-i", str(full),
        "-t", str(duration), "-vcodec", "libx264", "-acodec", "aac",
        "-strict", "-2", str(partial), "-y", "-loglevel", "error",
    ]
    trimmed = run_command(trim, timeout)
    if trimmed.returncode or not partial.is_file() or partial.stat().st_size == 0:
        return {
            "physical_media_id": video_id,
            "official_split": row["official_split"],
            "status": "trim_failed",
            "returncode": trimmed.returncode,
            "stdout_tail": trimmed.stdout[-500:],
            "stderr_tail": trimmed.stderr[-1500:],
            "full_video_bytes": full.stat().st_size,
            "media_bytes": 0,
            "cookies_used": False,
            "client_or_po_token_args_used": False,
        }
    partial.replace(target)
    media = ffprobe(target, ffprobe_executable)
    checks = {
        "duration_within_one_second": abs(media["duration_sec"] - duration) <= 1.0,
        "video_h264": media["video_codec"] == "h264",
        "audio_aac": media["audio_codec"] == "aac",
        "audio_sample_rate_positive": media["audio_sample_rate"] > 0,
        "audio_channels_positive": media["audio_channels"] > 0,
    }
    status = "passed" if all(checks.values()) else "media_qualification_failed"
    result = {
        "physical_media_id": video_id,
        "official_split": row["official_split"],
        "status": status,
        "source_start_sec": row["source_start_sec"],
        "source_end_sec": row["source_end_sec"],
        "expected_duration_sec": duration,
        "full_video_bytes": full.stat().st_size,
        "media_path": str(target.resolve()),
        "media_bytes": target.stat().st_size,
        "media_sha256": sha256(target),
        "media_checks": checks,
        **media,
        "cookies_used": False,
        "client_or_po_token_args_used": False,
    }
    full.unlink(missing_ok=True)
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    plan = read_jsonl(args.plan)
    probe = json.loads(args.probe.read_text(encoding="utf-8"))
    selected = validate_inputs(contract, plan, probe)
    for executable in (args.yt_dlp, args.node, args.ffmpeg, args.ffprobe):
        if not executable.is_file():
            raise FileNotFoundError(f"UnAV canary可执行文件不存在：{executable}")
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for row in selected:
        result = download_one(
            row, args.output, args.yt_dlp, args.node,
            args.ffmpeg, args.ffprobe, args.timeout,
        )
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    payload = {
        "artifact_kind": "m4_ptpa_unav_s1_media_canary2_result",
        "format_version": 1,
        "status": "PASS" if all(row["status"] in {"passed", "reused"} for row in results) else "FAIL",
        "requested": len(results),
        "passed": sum(row["status"] in {"passed", "reused"} for row in results),
        "failed": sum(row["status"] not in {"passed", "reused"} for row in results),
        "split_counts": dict(sorted(__import__("collections").Counter(row["official_split"] for row in results).items())),
        "cookies_used": False,
        "client_or_po_token_args_used": False,
        "feature_extraction_allowed": False,
        "effect_access_allowed": False,
        "test_accessed": False,
        "training_performed": False,
        "results": results,
    }
    result_path = args.output / "canary2_result.json"
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--probe", type=Path, default=DEFAULT_PROBE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--yt-dlp", type=Path, default=DEFAULT_YTDLP)
    parser.add_argument("--node", type=Path, default=DEFAULT_NODE)
    parser.add_argument("--ffmpeg", type=Path, default=DEFAULT_FFMPEG)
    parser.add_argument("--ffprobe", type=Path, default=DEFAULT_FFPROBE)
    parser.add_argument("--timeout", type=int, default=900)
    return parser.parse_args()


if __name__ == "__main__":
    payload = run(parse_args())
    print(json.dumps({key: payload[key] for key in ("status", "requested", "passed", "failed")}, ensure_ascii=False))
