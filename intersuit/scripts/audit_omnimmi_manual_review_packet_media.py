#!/usr/bin/env python3
"""审计人工复核工作包中 Visual-only 控制的本地媒体。"""

from __future__ import annotations

import argparse
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKET = ROOT / "intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/manual_review_packet.jsonl"
DEFAULT_VIDEO_ROOT = ROOT / "third_party/OmniMMI/omnimmi/videos"
DEFAULT_OUTPUT = ROOT / "intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/control_media_audit.json"


def _load_probe():
    source = ROOT / "intersuit/scripts/audit_omnimmi_audio_candidate_media.py"
    spec = spec_from_file_location("audio_media_audit", source)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.probe_media, module.sha256


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--video-root", type=Path, default=DEFAULT_VIDEO_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    packet_path = args.packet.resolve()
    rows = [json.loads(line) for line in packet_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    controls = [row for row in rows if row.get("review_role") == "visual_control_candidate"]
    probe_media, sha256 = _load_probe()
    videos = sorted({row["video"] for row in controls})
    media = {video: probe_media(args.video_root.resolve() / video) for video in videos}
    missing = [video for video, item in media.items() if not item["exists"]]
    no_audio = [video for video, item in media.items() if item["exists"] and not item["has_audio"]]
    bad_video = [video for video, item in media.items() if item["exists"] and not item["has_video"]]
    status = "PASS_CONTROL_MEDIA_PREFLIGHT" if not missing and not no_audio and not bad_video else "BLOCKED_CONTROL_MEDIA_PREFLIGHT"
    result = {
        "status": status,
        "prediction_files_accessed": False,
        "gpu_used": False,
        "packet_sha256": sha256(packet_path),
        "control_rows": len(controls),
        "unique_videos": len(videos),
        "missing_videos": missing,
        "videos_without_audio": no_audio,
        "files_without_video_stream": bad_video,
        "media": media,
        "next_gate": "human_modality_review",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "media"}, ensure_ascii=False, indent=2, sort_keys=True))
    if status.startswith("BLOCKED"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
