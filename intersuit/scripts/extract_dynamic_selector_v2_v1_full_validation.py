#!/usr/bin/env python3
"""从 UnAV-100 官方分片归档中只提取 V1 完整稀疏 validation 媒体。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


ARCHIVE_PREFIX = "ssd3/gengtiantian/audio_visual_data/unav100/raw_videos"
EXPECTED_PART_SIZE = 5_368_709_120
EXPECTED_TAIL_SIZE = 1_351_094_873
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("拒绝运行：必须通过 conda run -n M4 启动")
    allowlist = json.loads(args.allowlist.read_text(encoding="utf-8"))
    if allowlist.get("artifact_kind") != "dynamic_selector_v2_v1_full_validation_media_allowlist":
        raise ValueError("invalid full-validation allowlist")
    if allowlist.get("split") != "validation":
        raise ValueError("full V1 extraction refuses non-validation media")
    entries = allowlist.get("entries")
    if not isinstance(entries, list) or len(entries) != int(allowlist.get("video_count", -1)):
        raise ValueError("allowlist entries/video_count mismatch")
    video_ids = [str(entry["video_id"]) for entry in entries]
    if len(video_ids) != len(set(video_ids)):
        raise ValueError("allowlist contains duplicate video ids")
    invalid_ids = [video_id for video_id in video_ids if not VIDEO_ID_RE.fullmatch(video_id)]
    if invalid_ids:
        raise ValueError(f"unsafe video ids in allowlist: {invalid_ids[:3]}")

    parts = [
        args.archive_dir / f"unav_raw_videos.tar.gz.part_{index:02d}"
        for index in range(13)
    ]
    for index, part in enumerate(parts):
        expected_size = EXPECTED_TAIL_SIZE if index == 12 else EXPECTED_PART_SIZE
        if not part.is_file() or part.stat().st_size != expected_size:
            raise ValueError(f"archive part missing or size mismatch: {part}")

    args.output_root.mkdir(parents=True, exist_ok=True)
    existing = sorted(args.output_root.iterdir())
    if existing:
        raise ValueError(f"output root must be empty before extraction: {args.output_root}")

    with tempfile.TemporaryDirectory(prefix="m4-unav-v1-members-", dir="/tmp") as temp_dir:
        member_file = Path(temp_dir) / "members.txt"
        member_file.write_text(
            "".join(f"{ARCHIVE_PREFIX}/{video_id}.mp4\n" for video_id in video_ids),
            encoding="utf-8",
        )
        cat_proc = subprocess.Popen(
            ["/bin/cat", *map(str, parts)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert cat_proc.stdout is not None
        tar_proc = subprocess.run(
            [
                "/bin/tar",
                "--extract",
                "--gzip",
                "--file",
                "-",
                "--directory",
                str(args.output_root),
                "--strip-components",
                "5",
                "--no-recursion",
                "--files-from",
                str(member_file),
            ],
            stdin=cat_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        cat_proc.stdout.close()
        cat_stderr = (cat_proc.stderr.read() if cat_proc.stderr is not None else b"").decode(
            errors="replace"
        )
        cat_returncode = cat_proc.wait()
        if cat_returncode or tar_proc.returncode:
            raise RuntimeError(
                "归档选择性提取失败："
                f"cat={cat_returncode}, tar={tar_proc.returncode}, "
                f"cat_stderr={cat_stderr[-500:]!r}, tar_stderr={tar_proc.stderr[-1000:]!r}"
            )

    extracted = sorted(args.output_root.glob("*.mp4"))
    extracted_ids = {path.stem for path in extracted}
    if extracted_ids != set(video_ids):
        missing = sorted(set(video_ids) - extracted_ids)
        extra = sorted(extracted_ids - set(video_ids))
        raise ValueError(f"extracted media mismatch: missing={missing[:5]}, extra={extra[:5]}")
    other_files = sorted(path for path in args.output_root.iterdir() if path.suffix != ".mp4")
    if other_files:
        raise ValueError(f"unexpected extracted files: {other_files[:5]}")

    payload = {
        "artifact_kind": "dynamic_selector_v2_v1_full_validation_extraction",
        "status": "PASS",
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV"),
        "allowlist": str(args.allowlist.resolve()),
        "allowlist_sha256": sha256(args.allowlist),
        "archive_dir": str(args.archive_dir.resolve()),
        "archive_total_bytes": sum(part.stat().st_size for part in parts),
        "output_root": str(args.output_root.resolve()),
        "video_count": len(extracted),
        "total_media_bytes": sum(path.stat().st_size for path in extracted),
    }
    write_json(args.summary, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
