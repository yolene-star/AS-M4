#!/usr/bin/env python3
"""按 ZIP member 的 HTTP Range 下载并审计 LongVALE oracle_pilot 媒体。"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from intersuit.harness.longvale_v2_preflight import ZipMember, extract_zip_member, sha256_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "intersuit/harness/artifacts/dynamic_selector_v2_v2/preflight/oracle_pilot.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "intersuit/datasets/LongVALE_V2/oracle_pilot_media"
DEFAULT_AUDIT = PROJECT_ROOT / "intersuit/harness/artifacts/dynamic_selector_v2_v2/preflight/oracle_pilot_media_audit.json"
REVISION = "18889b01886e30c36b0d1c650ac4439ad460ee73"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit-output", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--chunk-bytes", type=int, default=32 * 1024 * 1024)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def _url(archive: str) -> str:
    return f"https://huggingface.co/datasets/ttgeng233/LongVALE/resolve/{REVISION}/{archive}"


def _curl_range(url: str, start: int, end: int, output: Path, expected_archive_sha256: str) -> None:
    if not 0 <= start <= end:
        raise ValueError(f"非法 range：{start}-{end}")
    expected_size = end - start + 1
    header = output.with_suffix(output.suffix + ".headers")
    command = [
        "curl", "-sS", "-L", "--fail", "--retry", "3", "--retry-delay", "2",
        "--range", f"{start}-{end}", "--max-filesize", str(expected_size + 1),
        "--dump-header", str(header), "--output", str(output), url,
    ]
    subprocess.run(command, check=True)
    headers = header.read_text(encoding="latin-1")
    content_ranges = re.findall(r"(?im)^content-range:\s*bytes\s+(\d+)-(\d+)/(\d+)\s*$", headers)
    linked_hashes = re.findall(r"(?im)^x-linked-etag:\s*\"?([0-9a-f]{64})\"?\s*$", headers)
    header.unlink(missing_ok=True)
    if not content_ranges or tuple(map(int, content_ranges[-1][:2])) != (start, end):
        output.unlink(missing_ok=True)
        raise ValueError("官方媒体请求未返回精确 Content-Range")
    if expected_archive_sha256 and (not linked_hashes or linked_hashes[-1] != expected_archive_sha256):
        output.unlink(missing_ok=True)
        raise ValueError("官方 ZIP linked SHA-256 漂移")
    actual_size = output.stat().st_size
    if actual_size != expected_size:
        output.unlink(missing_ok=True)
        raise ValueError(f"range 下载大小不一致：{actual_size}/{expected_size}")


def _member_from_media(media: dict) -> ZipMember:
    return ZipMember(
        name=media["member"],
        compression_method=int(media["compression_method"]),
        flags=int(media["flags"]),
        crc32=int(media["crc32"]),
        compressed_size=int(media["compressed_size"]),
        uncompressed_size=int(media["uncompressed_size"]),
        local_header_offset=int(media["local_header_offset"]),
    )


def _data_offset(url: str, media: dict, temporary_dir: Path) -> int:
    header_path = temporary_dir / "local-header.bin"
    start = int(media["local_header_offset"])
    end = min(start + 65535, int(media["archive_size"]) - 1)
    _curl_range(url, start, end, header_path, media["archive_linked_sha256"])
    blob = header_path.read_bytes()
    header_path.unlink()
    if blob[:4] != b"PK\x03\x04":
        raise ValueError(f"local header 签名错误：{media['member']}")
    flags, method = struct.unpack_from("<2H", blob, 6)
    name_length, extra_length = struct.unpack_from("<2H", blob, 26)
    name = blob[30 : 30 + name_length].decode("utf-8" if flags & 0x800 else "cp437")
    if name != media["member"] or flags != media["flags"] or method != media["compression_method"]:
        raise ValueError(f"local header 与 central directory 不一致：{media['member']}")
    return start + 30 + name_length + extra_length


def _verify_existing(path: Path, member: ZipMember) -> bool:
    if not path.is_file() or path.stat().st_size != member.uncompressed_size:
        return False
    import zlib

    crc32 = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            crc32 = zlib.crc32(chunk, crc32)
    return crc32 & 0xFFFFFFFF == member.crc32


def download_video(video_id: str, media: dict, output_dir: Path, chunk_bytes: int) -> dict:
    member = _member_from_media(media)
    output = output_dir / f"{video_id}.mp4"
    if _verify_existing(output, member):
        return {"video_id": video_id, "path": str(output), "status": "reused", "sha256": sha256_file(output)}
    output.unlink(missing_ok=True)
    partial_root = output_dir / ".partial"
    partial_root.mkdir(parents=True, exist_ok=True)
    compressed = partial_root / f"{video_id}.deflate.part"
    if compressed.exists() and compressed.stat().st_size > member.compressed_size:
        compressed.unlink()
    url = _url(media["archive"])
    with tempfile.TemporaryDirectory(prefix=f"longvale-{video_id}-", dir="/tmp") as temporary:
        temporary_dir = Path(temporary)
        data_offset = _data_offset(url, media, temporary_dir)
        while (compressed.stat().st_size if compressed.exists() else 0) < member.compressed_size:
            completed = compressed.stat().st_size if compressed.exists() else 0
            start = data_offset + completed
            end = min(data_offset + member.compressed_size, start + chunk_bytes) - 1
            chunk = temporary_dir / "chunk.bin"
            _curl_range(url, start, end, chunk, media["archive_linked_sha256"])
            with compressed.open("ab") as destination, chunk.open("rb") as source:
                for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
                    destination.write(block)
            chunk.unlink()
        extract_zip_member(compressed, output, member)
    compressed.unlink()
    return {"video_id": video_id, "path": str(output), "status": "downloaded", "sha256": sha256_file(output)}


def _ffprobe(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def main() -> None:
    args = parse_args()
    if not 1 <= args.workers <= 4:
        raise ValueError("workers 必须在 1..4")
    if args.chunk_bytes < 1024 * 1024:
        raise ValueError("chunk-bytes 不得小于 1 MiB")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    lock_handle = (args.output_dir / ".download.lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError(f"已有下载进程持有独占锁：{args.output_dir}") from exc
    rows = [json.loads(line) for line in args.manifest.read_text(encoding="utf-8").splitlines() if line]
    media_by_video = {}
    durations = {}
    for row in rows:
        media_by_video[row["video_id"]] = row["media"]
        durations[row["video_id"]] = float(row["duration_sec"])
    selected_ids = sorted(media_by_video)
    if args.limit is not None:
        selected_ids = selected_ids[: args.limit]
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(download_video, video_id, media_by_video[video_id], args.output_dir, args.chunk_bytes): video_id
            for video_id in selected_ids
        }
        for future in as_completed(futures):
            item = future.result()
            path = Path(item["path"])
            media_duration = _ffprobe(path)
            item["bytes"] = path.stat().st_size
            item["ffprobe_duration_sec"] = media_duration
            item["annotation_duration_sec"] = durations[item["video_id"]]
            item["duration_delta_sec"] = media_duration - durations[item["video_id"]]
            results.append(item)
            print(f"{item['status']} {item['video_id']} ({item['bytes']} bytes)")
    results.sort(key=lambda item: item["video_id"])
    complete = len(results) == len(media_by_video)
    audit = {
        "artifact_kind": "dynamic_selector_v2_longvale_oracle_pilot_media_audit",
        "format_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if complete else "partial_validation",
        "revision": REVISION,
        "manifest_sha256": sha256_file(args.manifest),
        "expected_video_count": len(media_by_video),
        "audited_video_count": len(results),
        "all_crc32_pass": True,
        "all_ffprobe_pass": True,
        "total_bytes": sum(item["bytes"] for item in results),
        "videos": results,
    }
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: audit[key] for key in ("status", "audited_video_count", "total_bytes")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
