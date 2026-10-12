#!/usr/bin/env python3
"""审计 OmniMMI 显式音频线索候选的本地媒体与音轨，不读取模型预测。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEW = ROOT / "intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/review_manifest.jsonl"
DEFAULT_VIDEO_ROOT = ROOT / "third_party/OmniMMI/omnimmi/videos"
DEFAULT_OUTPUT = ROOT / "intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/media_audit.json"
SELECTED_CANDIDATES = {"audio_required_candidate", "audio_visual_joint_candidate"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def selected_review_rows(review_path: Path) -> list[dict]:
    rows = [json.loads(line) for line in review_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row for row in rows if row.get("auto_candidate") in SELECTED_CANDIDATES]


def probe_media(path: Path) -> dict:
    if not path.is_file():
        return {"exists": False, "has_video": False, "has_audio": False, "duration_sec": None, "sha256": None}
    command = [
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
        "-show_entries", "format=duration", "-of", "json", str(path),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    payload = json.loads(completed.stdout)
    stream_types = {stream.get("codec_type") for stream in payload.get("streams", [])}
    duration = payload.get("format", {}).get("duration")
    return {
        "exists": True,
        "has_video": "video" in stream_types,
        "has_audio": "audio" in stream_types,
        "duration_sec": float(duration) if duration is not None else None,
        "sha256": sha256(path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-manifest", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--video-root", type=Path, default=DEFAULT_VIDEO_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    review_path = args.review_manifest.resolve()
    video_root = args.video_root.resolve()
    selected = selected_review_rows(review_path)
    unique_videos = sorted({row["video"] for row in selected})
    media = {video: probe_media(video_root / video) for video in unique_videos}
    missing = [video for video, item in media.items() if not item["exists"]]
    no_audio = [video for video, item in media.items() if item["exists"] and not item["has_audio"]]
    bad_video = [video for video, item in media.items() if item["exists"] and not item["has_video"]]
    ineligible_ids = sorted(row["review_id"] for row in selected if row["video"] in set(missing + no_audio + bad_video))
    if missing or bad_video:
        status = "BLOCKED_CANDIDATE_MEDIA_PREFLIGHT"
    elif no_audio:
        status = "PASS_CANDIDATE_MEDIA_PREFLIGHT_WITH_EXCLUSIONS"
    else:
        status = "PASS_CANDIDATE_MEDIA_PREFLIGHT"
    result = {
        "status": status,
        "prediction_files_accessed": False,
        "gpu_used": False,
        "review_manifest_sha256": sha256(review_path),
        "selected_review_rows": len(selected),
        "eligible_selected_rows": len(selected) - len(ineligible_ids),
        "unique_videos": len(unique_videos),
        "missing_videos": missing,
        "videos_without_audio": no_audio,
        "files_without_video_stream": bad_video,
        "ineligible_review_ids": ineligible_ids,
        "media": media,
        "next_gate": "human_audio_visual_review",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "media"}, ensure_ascii=False, indent=2, sort_keys=True))
    if status.startswith("BLOCKED"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
