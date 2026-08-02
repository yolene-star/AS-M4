#!/usr/bin/env python3
"""用 HTTP Range 索引 LongVALE 官方 training ZIP，不下载完整归档。"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import json
from pathlib import Path
import re
import subprocess
import tempfile

from intersuit.harness.longvale_v2_preflight import parse_zip_central_directory


DEFAULT_REVISION = "18889b01886e30c36b0d1c650ac4439ad460ee73"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "intersuit/datasets/LongVALE_V2/archive_index.json"
ARCHIVE_COUNT = 39


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--initial-tail-bytes", type=int, default=262_144)
    parser.add_argument("--maximum-tail-bytes", type=int, default=8 * 1024 * 1024)
    return parser.parse_args()


def _archive_path(part: int) -> str:
    return f"raw_videos_train/LongVALE_train_7240_part_{part}.zip"


def _fetch_tail(revision: str, archive: str, tail_bytes: int, directory: Path) -> tuple[bytes, int, str]:
    url = f"https://huggingface.co/datasets/ttgeng233/LongVALE/resolve/{revision}/{archive}"
    stem = archive.rsplit("/", 1)[-1]
    body_path = directory / f"{stem}.{tail_bytes}.tail"
    header_path = directory / f"{stem}.{tail_bytes}.headers"
    command = [
        "curl", "-L", "--fail", "--retry", "3", "--retry-delay", "2",
        "--range", f"-{tail_bytes}", "--max-filesize", str(tail_bytes + 1),
        "--dump-header", str(header_path), "--output", str(body_path), url,
    ]
    subprocess.run(command, check=True)
    headers = header_path.read_text(encoding="latin-1")
    matches = re.findall(r"(?im)^content-range:\s*bytes\s+\d+-\d+/(\d+)\s*$", headers)
    etags = re.findall(r"(?im)^x-linked-etag:\s*\"?([0-9a-f]{64})\"?\s*$", headers)
    if not matches:
        raise ValueError(f"{archive} 未返回 Content-Range，拒绝继续")
    return body_path.read_bytes(), int(matches[-1]), etags[-1] if etags else ""


def index_archive(revision: str, part: int, initial: int, maximum: int, directory: Path) -> dict:
    archive = _archive_path(part)
    tail_bytes = initial
    while tail_bytes <= maximum:
        blob, total_size, linked_sha256 = _fetch_tail(revision, archive, tail_bytes, directory)
        try:
            members = parse_zip_central_directory(blob, total_size=total_size)
            break
        except ValueError as exc:
            if "需要更大的尾部 range" not in str(exc) or tail_bytes == maximum:
                raise
            tail_bytes = min(tail_bytes * 2, maximum)
    else:
        raise ValueError(f"{archive} 超过最大 central-directory range")
    videos = []
    for member in members:
        path = Path(member.name)
        if path.suffix.lower() != ".mp4":
            raise ValueError(f"{archive} 包含非 MP4 文件：{member.name}")
        videos.append({"video_id": path.stem, **asdict(member)})
    return {
        "archive": archive,
        "archive_size": total_size,
        "linked_sha256": linked_sha256,
        "tail_bytes_used": len(blob),
        "video_count": len(videos),
        "videos": videos,
    }


def main() -> None:
    args = parse_args()
    if not 1 <= args.workers <= 8:
        raise ValueError("workers 必须在 1..8")
    with tempfile.TemporaryDirectory(prefix="longvale-v2-index-", dir="/tmp") as temporary:
        directory = Path(temporary)
        archives = []
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    index_archive,
                    args.revision,
                    part,
                    args.initial_tail_bytes,
                    args.maximum_tail_bytes,
                    directory,
                ): part
                for part in range(1, ARCHIVE_COUNT + 1)
            }
            for future in as_completed(futures):
                item = future.result()
                archives.append(item)
                print(f"indexed {item['archive']}: {item['video_count']} videos")
    archives.sort(key=lambda item: int(re.search(r"part_(\d+)\.zip$", item["archive"]).group(1)))
    video_ids = [video["video_id"] for archive in archives for video in archive["videos"]]
    if len(video_ids) != 7240 or len(video_ids) != len(set(video_ids)):
        raise ValueError(f"官方 training ZIP 应包含 7240 个唯一视频，实际为 {len(video_ids)}/{len(set(video_ids))}")
    payload = {
        "artifact_kind": "longvale_v2_official_training_archive_index",
        "format_version": 1,
        "repository": "ttgeng233/LongVALE",
        "revision": args.revision,
        "archive_count": len(archives),
        "video_count": len(video_ids),
        "archives": archives,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_output.replace(args.output)
    print(json.dumps({"status": "pass", "output": str(args.output), "video_count": len(video_ids)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
