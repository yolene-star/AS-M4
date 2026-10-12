"""LongVALE V2 Oracle 诊断的数据冻结与远程 ZIP 索引辅助函数。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import struct
from typing import Iterable
import zlib


SPARSE_RATIO_MAX = 0.10
WINDOW_SEC = 1.0
TOP_K = 8
CONTROL_SEEDS = (20260720, 20260721, 20260722, 20260723, 20260724)
SPLIT_SIZES = {"oracle_dev": 50, "oracle_pilot": 100, "oracle_confirm": 200}
MIN_SPARSE_EVENTS = {"oracle_dev": 100, "oracle_pilot": 500, "oracle_confirm": 1000}
BOUNDARY_TOLERANCE_SEC = 0.025


@dataclass(frozen=True)
class ZipMember:
    name: str
    compression_method: int
    flags: int
    crc32: int
    compressed_size: int
    uncompressed_size: int
    local_header_offset: int


def extract_zip_member(compressed_path: Path, output_path: Path, member: ZipMember) -> None:
    """解压已按 range 获取的单个 ZIP member，并验证 size/CRC。"""

    if member.flags & 0x1:
        raise ValueError(f"不支持加密 ZIP member：{member.name}")
    if member.compression_method not in {0, 8}:
        raise ValueError(f"不支持 ZIP compression method={member.compression_method}")
    if compressed_path.stat().st_size != member.compressed_size:
        raise ValueError(f"compressed member 大小不一致：{member.name}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    crc32 = 0
    written = 0
    decompressor = zlib.decompressobj(-15) if member.compression_method == 8 else None
    with compressed_path.open("rb") as source, temporary.open("wb") as destination:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            decoded = decompressor.decompress(chunk) if decompressor is not None else chunk
            destination.write(decoded)
            written += len(decoded)
            crc32 = zlib.crc32(decoded, crc32)
        if decompressor is not None:
            decoded = decompressor.flush()
            destination.write(decoded)
            written += len(decoded)
            crc32 = zlib.crc32(decoded, crc32)
            if not decompressor.eof:
                raise ValueError(f"DEFLATE stream 未完整结束：{member.name}")
    if written != member.uncompressed_size or crc32 & 0xFFFFFFFF != member.crc32:
        temporary.unlink(missing_ok=True)
        raise ValueError(
            f"解压校验失败：{member.name}, size={written}/{member.uncompressed_size}, "
            f"crc32={crc32 & 0xFFFFFFFF:08x}/{member.crc32:08x}"
        )
    temporary.replace(output_path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip64_directory(blob: bytes) -> tuple[int, int, int]:
    position = blob.rfind(b"PK\x06\x06")
    if position < 0 or position + 56 > len(blob):
        raise ValueError("ZIP64 central directory record 不在已下载尾部范围内")
    values = struct.unpack_from("<4sQ2H2I4Q", blob, position)
    return int(values[7]), int(values[8]), int(values[9])


def _central_directory_location(blob: bytes) -> tuple[int, int, int]:
    position = blob.rfind(b"PK\x05\x06")
    if position < 0 or position + 22 > len(blob):
        raise ValueError("缺少 ZIP end-of-central-directory record")
    values = struct.unpack_from("<4s4H2IH", blob, position)
    entry_count, directory_size, directory_offset = int(values[4]), int(values[5]), int(values[6])
    if entry_count == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        return _zip64_directory(blob)
    return entry_count, directory_size, directory_offset


def _zip64_extra(extra: bytes, *, compressed: int, uncompressed: int, offset: int) -> tuple[int, int, int]:
    cursor = 0
    while cursor + 4 <= len(extra):
        field_id, field_size = struct.unpack_from("<HH", extra, cursor)
        value = extra[cursor + 4 : cursor + 4 + field_size]
        cursor += 4 + field_size
        if field_id != 0x0001:
            continue
        value_cursor = 0

        def read_u64(current: int, required: bool) -> tuple[int, int]:
            if not required:
                return current, value_cursor
            if value_cursor + 8 > len(value):
                raise ValueError("ZIP64 extra field 截断")
            return int(struct.unpack_from("<Q", value, value_cursor)[0]), value_cursor + 8

        uncompressed, value_cursor = read_u64(uncompressed, uncompressed == 0xFFFFFFFF)
        compressed, value_cursor = read_u64(compressed, compressed == 0xFFFFFFFF)
        offset, value_cursor = read_u64(offset, offset == 0xFFFFFFFF)
        return compressed, uncompressed, offset
    if 0xFFFFFFFF in (compressed, uncompressed, offset):
        raise ValueError("ZIP64 entry 缺少必要 extra field")
    return compressed, uncompressed, offset


def parse_zip_central_directory(blob: bytes, *, total_size: int) -> list[ZipMember]:
    """从远程 ZIP 的尾部 range 解析成员，无需下载整个归档。"""

    if total_size < len(blob):
        raise ValueError("ZIP total_size 小于已下载尾部")
    entry_count, directory_size, directory_offset = _central_directory_location(blob)
    tail_offset = total_size - len(blob)
    start = directory_offset - tail_offset
    end = start + directory_size
    if start < 0 or end > len(blob):
        raise ValueError(f"central directory 需要更大的尾部 range：size={directory_size}")

    members: list[ZipMember] = []
    cursor = start
    for _ in range(entry_count):
        if blob[cursor : cursor + 4] != b"PK\x01\x02" or cursor + 46 > end:
            raise ValueError("central directory entry 截断或签名错误")
        flags = struct.unpack_from("<H", blob, cursor + 8)[0]
        method = struct.unpack_from("<H", blob, cursor + 10)[0]
        crc32 = struct.unpack_from("<I", blob, cursor + 16)[0]
        compressed = struct.unpack_from("<I", blob, cursor + 20)[0]
        uncompressed = struct.unpack_from("<I", blob, cursor + 24)[0]
        name_length, extra_length, comment_length = struct.unpack_from("<3H", blob, cursor + 28)
        offset = struct.unpack_from("<I", blob, cursor + 42)[0]
        variable_end = cursor + 46 + name_length + extra_length + comment_length
        if variable_end > end:
            raise ValueError("central directory variable fields 截断")
        name_bytes = blob[cursor + 46 : cursor + 46 + name_length]
        encoding = "utf-8" if flags & 0x800 else "cp437"
        name = name_bytes.decode(encoding)
        extra_start = cursor + 46 + name_length
        extra = blob[extra_start : extra_start + extra_length]
        compressed, uncompressed, offset = _zip64_extra(
            extra, compressed=compressed, uncompressed=uncompressed, offset=offset
        )
        if not name.endswith("/"):
            members.append(
                ZipMember(
                    name=name,
                    compression_method=method,
                    flags=flags,
                    crc32=crc32,
                    compressed_size=compressed,
                    uncompressed_size=uncompressed,
                    local_header_offset=offset,
                )
            )
        cursor = variable_end
    if cursor != end:
        raise ValueError(f"central directory 长度不一致：parsed={cursor - start}, expected={directory_size}")
    return members


def parse_training_annotations(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ValueError("LongVALE training annotation 顶层必须是非空对象")
    parsed: dict[str, dict] = {}
    for video_id, item in payload.items():
        duration = float(item["duration"])
        segments = item.get("segments")
        if not isinstance(segments, dict) or not segments:
            raise ValueError(f"{video_id} 缺少 segments")
        events = []
        for event_index, (timestamp, caption) in enumerate(segments.items()):
            fields = timestamp.split("_")
            if len(fields) != 2:
                raise ValueError(f"{video_id} timestamp 非法：{timestamp}")
            start, end = map(float, fields)
            if not (math.isfinite(duration) and math.isfinite(start) and math.isfinite(end)):
                raise ValueError(f"{video_id} 存在非有限边界")
            if duration <= 0 or start < 0 or end <= start or end > duration + BOUNDARY_TOLERANCE_SEC:
                raise ValueError(f"{video_id} 边界非法：duration={duration}, timestamp={timestamp}")
            end = min(end, duration)
            text = str(caption).strip()
            if not text:
                raise ValueError(f"{video_id}/{event_index} caption 为空")
            events.append(
                {
                    "event_index": event_index,
                    "start_sec": start,
                    "end_sec": end,
                    "caption": text,
                    "is_sparse": (end - start) / duration <= SPARSE_RATIO_MAX,
                }
            )
        parsed[str(video_id)] = {"duration_sec": duration, "events": events}
    return parsed


def stable_video_order(video_ids: Iterable[str], *, namespace: str) -> list[str]:
    return sorted(
        (str(video_id) for video_id in video_ids),
        key=lambda video_id: (hashlib.sha256(f"{namespace}:{video_id}".encode()).digest(), video_id),
    )


def freeze_video_splits(annotations: dict[str, dict], *, namespace: str) -> dict[str, list[str]]:
    eligible = [
        video_id
        for video_id, item in annotations.items()
        if any(event["is_sparse"] for event in item["events"])
    ]
    ordered = stable_video_order(eligible, namespace=namespace)
    required = sum(SPLIT_SIZES.values())
    if len(ordered) < required:
        raise ValueError(f"稀疏域物理视频不足：{len(ordered)} < {required}")
    result: dict[str, list[str]] = {}
    cursor = 0
    for split_name, count in SPLIT_SIZES.items():
        result[split_name] = ordered[cursor : cursor + count]
        cursor += count
    return result


def build_mismatch_mapping(video_ids: Iterable[str], *, seed: int) -> dict[str, str]:
    ids = sorted(str(value) for value in video_ids)
    if len(ids) < 2 or len(ids) != len(set(ids)):
        raise ValueError("mismatch mapping 需要至少两个唯一 video_id")
    ranked = stable_video_order(ids, namespace=f"longvale-v2-mismatch-{seed}")
    offset = 1 + seed % (len(ranked) - 1)
    raw = {source: ranked[(index + offset) % len(ranked)] for index, source in enumerate(ranked)}
    mapping = {video_id: raw[video_id] for video_id in ids}
    if set(mapping) != set(mapping.values()) or any(source == target for source, target in mapping.items()):
        raise AssertionError("mismatch mapping 不是无固定点的一一映射")
    return mapping


def build_manifest_rows(
    annotations: dict[str, dict],
    video_ids: Iterable[str],
    media_by_video: dict[str, dict],
    *,
    split_name: str,
) -> list[dict]:
    rows = []
    for video_id in sorted(video_ids):
        if video_id not in media_by_video:
            raise ValueError(f"ZIP 索引缺少媒体：{video_id}.mp4")
        item = annotations[video_id]
        for event in item["events"]:
            rows.append(
                {
                    "sample_id": f"longvale:training:{video_id}:{event['event_index']}",
                    "split": split_name,
                    "video_id": video_id,
                    "duration_sec": item["duration_sec"],
                    "event_index": event["event_index"],
                    "event_start_sec": event["start_sec"],
                    "event_end_sec": event["end_sec"],
                    "event_ratio": (event["end_sec"] - event["start_sec"]) / item["duration_sec"],
                    "is_sparse": event["is_sparse"],
                    "caption": event["caption"],
                    "prompt": (
                        f"Describe the audio-visual event between {event['start_sec']:.3f} "
                        f"and {event['end_sec']:.3f} seconds."
                    ),
                    "media": media_by_video[video_id],
                }
            )
    return rows


def validate_split(rows: list[dict], *, split_name: str) -> dict:
    videos = {row["video_id"] for row in rows}
    sparse_events = sum(bool(row["is_sparse"]) for row in rows)
    if len(videos) < SPLIT_SIZES[split_name]:
        raise ValueError(f"{split_name} 物理视频不足")
    if sparse_events < MIN_SPARSE_EVENTS[split_name]:
        raise ValueError(f"{split_name} 稀疏事件不足：{sparse_events} < {MIN_SPARSE_EVENTS[split_name]}")
    return {
        "video_count": len(videos),
        "all_event_count": len(rows),
        "sparse_event_count": sparse_events,
    }
