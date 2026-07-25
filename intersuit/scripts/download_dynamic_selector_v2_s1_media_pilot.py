#!/usr/bin/env python3
"""按冻结清单探测或下载 UnAV-100 S1 validation 媒体剪辑。"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


ARTIFACT_KIND = "dynamic_selector_v2_s1_validation_media_pilot_allowlist"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--status-output", type=Path, required=True)
    parser.add_argument("--yt-dlp", type=Path, default=Path("yt-dlp"))
    parser.add_argument("--probe-status", type=Path)
    parser.add_argument("--mode", choices=("probe", "download"), default="probe")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    payload = json.loads(args.allowlist.read_text(encoding="utf-8"))
    if payload.get("artifact_kind") != ARTIFACT_KIND:
        raise ValueError("refusing input that is not the frozen S1 media-pilot allowlist")
    if (payload.get("config") or {}).get("split") != "validation":
        raise ValueError("media pilot must remain validation-only")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("allowlist entries must be a non-empty list")
    if args.probe_status is not None:
        probe = json.loads(args.probe_status.read_text(encoding="utf-8"))
        if probe.get("artifact_kind") != "dynamic_selector_v2_s1_media_probe_or_download_status":
            raise ValueError("probe status has the wrong artifact kind")
        if probe.get("mode") != "probe" or Path(probe.get("allowlist", "")) != args.allowlist.resolve():
            raise ValueError("probe status does not belong to this allowlist")
        available = {str(result["video_id"]) for result in probe.get("results", []) if result.get("status") == "available"}
        entries = [entry for entry in entries if str(entry["video_id"]) in available]
    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("limit must be positive")
        entries = entries[: args.limit]

    args.output_root.mkdir(parents=True, exist_ok=True)
    results = []
    for entry in entries:
        video_id = str(entry["video_id"])
        start = float(entry["source_start_sec"])
        end = float(entry["source_end_sec"])
        url = f"https://www.youtube.com/watch?v={video_id}"
        target = args.output_root / f"{video_id}.mp4"
        if args.mode == "download" and target.is_file() and target.stat().st_size > 0:
            results.append(
                {
                    "selection_rank": int(entry["selection_rank"]),
                    "video_id": video_id,
                    "mode": args.mode,
                    "status": "downloaded",
                    "returncode": 0,
                    "target": str(target.resolve()),
                    "bytes": target.stat().st_size,
                    "stdout_tail": "existing non-empty file reused",
                    "stderr_tail": "",
                }
            )
            print(json.dumps(results[-1], ensure_ascii=False))
            continue
        base = [
            str(args.yt_dlp),
            "--ignore-config",
            "--no-playlist",
            "--retries",
            str(args.retries),
            "--socket-timeout",
            str(args.timeout),
            "--extractor-args",
            "youtube:player_client=android",
        ]
        if args.mode == "probe":
            command = [*base, "--simulate", "--quiet", "--print", "id", url]
        else:
            command = [
                *base,
                "--continue",
                "--download-sections",
                f"*{start}-{end}",
                "--force-keyframes-at-cuts",
                "--merge-output-format",
                "mp4",
                "-f",
                "18/b",
                "-o",
                str(target),
                url,
            ]
        proc = run(command)
        ok = proc.returncode == 0 and (args.mode == "probe" or (target.is_file() and target.stat().st_size > 0))
        results.append(
            {
                "selection_rank": int(entry["selection_rank"]),
                "video_id": video_id,
                "mode": args.mode,
                "status": "available" if ok and args.mode == "probe" else "downloaded" if ok else "failed",
                "returncode": proc.returncode,
                "target": str(target.resolve()) if args.mode == "download" else None,
                "bytes": target.stat().st_size if target.is_file() else 0,
                "stdout_tail": proc.stdout.strip()[-300:],
                "stderr_tail": proc.stderr.strip()[-1000:],
            }
        )
        print(json.dumps(results[-1], ensure_ascii=False))

    output = {
        "format_version": 1,
        "artifact_kind": "dynamic_selector_v2_s1_media_probe_or_download_status",
        "mode": args.mode,
        "allowlist": str(args.allowlist.resolve()),
        "probe_status": str(args.probe_status.resolve()) if args.probe_status else None,
        "requested_count": len(entries),
        "success_count": sum(result["status"] in {"available", "downloaded"} for result in results),
        "failure_count": sum(result["status"] == "failed" for result in results),
        "results": results,
    }
    args.status_output.parent.mkdir(parents=True, exist_ok=True)
    args.status_output.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if output["failure_count"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
