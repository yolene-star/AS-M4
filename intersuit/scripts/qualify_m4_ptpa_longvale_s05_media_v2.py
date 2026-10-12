#!/usr/bin/env python3
"""冻结并验收 LongVALE part19 的全部未暴露 S0.5 候选媒体。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
from typing import Any
import zipfile
import zlib

from intersuit.harness.longvale_v2_preflight import (
    CONTROL_SEEDS,
    build_mismatch_mapping,
    parse_training_annotations,
    sha256_file,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
RESPONSIBILITY_LOCK = INTERSUIT_ROOT / "configs/m4_ptpa_audio_dataset_responsibility_v2.json"
ANNOTATIONS = INTERSUIT_ROOT / "datasets/LongVALE_V2/official_metadata/longvale-annotations-training.json"
ARCHIVE_INDEX = INTERSUIT_ROOT / "datasets/LongVALE_V2/archive_index.json"
ARCHIVE = (
    INTERSUIT_ROOT
    / "datasets/LongVALE_V2/official_archives/raw_videos_train/LongVALE_train_7240_part_19.zip"
)
EXPOSED_MANIFESTS = (
    INTERSUIT_ROOT
    / "harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/p3_longvale_frozen100/pilot_manifest.jsonl",
    INTERSUIT_ROOT / "harness/artifacts/dynamic_selector_v2_v2/preflight/oracle_dev.jsonl",
)
RESERVED_MANIFEST = (
    INTERSUIT_ROOT
    / "harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/p1_offline/confirm_one_sparse_event_locked_not_allowed.jsonl"
)
DEFAULT_OUTPUT = (
    INTERSUIT_ROOT
    / "harness/artifacts/m4_ptpa_audio_semantic_fusion_v1/longvale_s05_media_qualification_v2"
)
DEFAULT_MEDIA_ROOT = INTERSUIT_ROOT / "datasets/LongVALE_V2/semantic_fusion_confirm_media_v2"
ARCHIVE_NAME = "raw_videos_train/LongVALE_train_7240_part_19.zip"
EVENT_NAMESPACE = "m4-ptpa-audio-semantic-fusion-longvale-s05-event-v2-20260821"
EXPECTED_ELIGIBLE_VIDEOS = 186
MAX_DURATION_DELTA_SEC = 1.0
EVENT_BOUNDARY_TOLERANCE_SEC = 0.5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--media-root", type=Path, default=DEFAULT_MEDIA_ROOT)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


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


def row_ids(paths: tuple[Path, ...]) -> set[str]:
    return {str(row["video_id"]) for path in paths for row in read_jsonl(path)}


def verify_inputs() -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    lock = read_json(RESPONSIBILITY_LOCK)
    longvale = lock.get("datasets", {}).get("longvale", {})
    authorized = longvale.get("authorized_archive", {})
    permissions = lock.get("permissions", {})
    if (
        lock.get("status") != "plan_locked_dataset_qualification_required"
        or longvale.get("primary_stages") != ["s3b", "s6"]
        or authorized.get("path") != ARCHIVE_NAME
        or permissions.get("longvale_part19_download_allowed_by_explicit_user_authorization") is not True
        or permissions.get("confirm_effect_access_allowed") is not False
        or permissions.get("training_allowed") is not False
    ):
        raise ValueError("LongVALE v2 数据责任/权限合同漂移")
    if not ARCHIVE.is_file():
        raise FileNotFoundError(ARCHIVE)
    if ARCHIVE.stat().st_size != int(authorized["size_bytes"]):
        raise ValueError("LongVALE part19 文件大小漂移")
    archive_sha = sha256_file(ARCHIVE)
    if archive_sha != authorized["sha256"]:
        raise ValueError("LongVALE part19 SHA256 漂移")
    index = read_json(ARCHIVE_INDEX)
    archive_item = next(
        (item for item in index.get("archives", []) if item.get("archive") == ARCHIVE_NAME),
        None,
    )
    if archive_item is None:
        raise ValueError("LongVALE part19 不在 archive index")
    if (
        int(archive_item["archive_size"]) != ARCHIVE.stat().st_size
        or archive_item["linked_sha256"] != archive_sha
    ):
        raise ValueError("LongVALE part19 与 archive index 不一致")
    input_hashes = {
        str(path.relative_to(PROJECT_ROOT)): sha256_file(path)
        for path in (
            RESPONSIBILITY_LOCK,
            ANNOTATIONS,
            ARCHIVE_INDEX,
            *EXPOSED_MANIFESTS,
            RESERVED_MANIFEST,
            ARCHIVE,
        )
    }
    return lock, input_hashes, archive_item


def select_event(video_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        raise ValueError(f"LongVALE {video_id} 没有合法事件")
    return min(
        events,
        key=lambda event: (
            hashlib.sha256(
                (
                    f"{EVENT_NAMESPACE}:{video_id}:{event['event_index']}:"
                    f"{event['start_sec']}:{event['end_sec']}:{event['caption']}"
                ).encode()
            ).digest(),
            int(event["event_index"]),
        ),
    )


def build_sealed_plan(
    archive_item: dict[str, Any], annotations: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], dict[str, Any]]:
    exposed = row_ids(EXPOSED_MANIFESTS)
    reserved = row_ids((RESERVED_MANIFEST,))
    indexed = {str(row["video_id"]): row for row in archive_item["videos"]}
    if len(indexed) != int(archive_item["video_count"]):
        raise ValueError("LongVALE part19 index video_id 不唯一")
    if exposed & reserved:
        raise ValueError("LongVALE 已暴露与预留集合重叠")
    eligible = sorted(set(indexed) - exposed - reserved)
    if len(eligible) != EXPECTED_ELIGIBLE_VIDEOS:
        raise ValueError(
            f"LongVALE part19 合格候选数量漂移: {len(eligible)}/{EXPECTED_ELIGIBLE_VIDEOS}"
        )
    rows = []
    for video_id in eligible:
        if video_id not in annotations:
            raise ValueError(f"LongVALE annotation 缺少 part19 video_id: {video_id}")
        event = select_event(video_id, annotations[video_id]["events"])
        media = indexed[video_id]
        rows.append(
            {
                "sample_id": f"longvale:semantic_fusion_confirm_v2:{video_id}:{event['event_index']}",
                "dataset": "LongVALE",
                "official_source_split": "training",
                "project_split": "internal_confirm_sealed_effect_locked",
                "video_id": video_id,
                "duration_sec": float(annotations[video_id]["duration_sec"]),
                "event_index": int(event["event_index"]),
                "event_start_sec": float(event["start_sec"]),
                "event_end_sec": float(event["end_sec"]),
                "event_caption": str(event["caption"]),
                "event_selection_namespace": EVENT_NAMESPACE,
                "prompt": (
                    "Describe the audio-visual event between "
                    f"{float(event['start_sec']):.3f} and {float(event['end_sec']):.3f} seconds."
                ),
                "media": {
                    "archive": ARCHIVE_NAME,
                    "archive_sha256": archive_item["linked_sha256"],
                    "member": media["name"],
                    "compression_method": int(media["compression_method"]),
                    "flags": int(media["flags"]),
                    "crc32": int(media["crc32"]),
                    "compressed_size": int(media["compressed_size"]),
                    "uncompressed_size": int(media["uncompressed_size"]),
                    "local_header_offset": int(media["local_header_offset"]),
                },
                "feature_extraction_allowed": False,
                "model_inference_allowed": False,
                "effect_access_allowed": False,
            }
        )
    mismatch = {
        str(seed): build_mismatch_mapping(eligible, seed=seed) for seed in CONTROL_SEEDS
    }
    summary = {
        "artifact_kind": "m4_ptpa_longvale_s05_sealed_plan_v2",
        "archive_video_count": len(indexed),
        "excluded_exposed_count": len(set(indexed) & exposed),
        "excluded_reserved_count": len(set(indexed) & reserved),
        "selected_physical_video_count": len(rows),
        "events_per_video": 1,
        "event_selection_namespace": EVENT_NAMESPACE,
        "content_or_model_based_selection": False,
        "mismatch_seeds": list(CONTROL_SEEDS),
        "feature_extraction_allowed": False,
        "model_inference_allowed": False,
        "effect_access_allowed": False,
    }
    return rows, mismatch, summary


def crc32_file(path: Path) -> int:
    value = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            value = zlib.crc32(chunk, value)
    return value & 0xFFFFFFFF


def extract_member(zf: zipfile.ZipFile, row: dict[str, Any], media_root: Path) -> tuple[Path, str]:
    media = row["media"]
    output = media_root / f"{row['video_id']}.mp4"
    expected_size = int(media["uncompressed_size"])
    expected_crc = int(media["crc32"])
    if output.is_file():
        if output.stat().st_size != expected_size or crc32_file(output) != expected_crc:
            raise ValueError(f"已有 LongVALE 媒体 size/CRC 漂移，拒绝覆盖: {output}")
        return output, "reused"
    if output.exists():
        raise ValueError(f"LongVALE 媒体目标不是普通文件: {output}")
    info = zf.getinfo(media["member"])
    if (
        info.file_size != expected_size
        or info.compress_size != int(media["compressed_size"])
        or info.CRC != expected_crc
        or info.compress_type != int(media["compression_method"])
    ):
        raise ValueError(f"ZIP member 与 sealed manifest 不一致: {media['member']}")
    media_root.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".mp4.part")
    if temporary.exists():
        raise ValueError(f"存在未审计的旧 part 文件，拒绝覆盖: {temporary}")
    digest = hashlib.sha256()
    crc = 0
    written = 0
    try:
        with zf.open(info, "r") as source, temporary.open("xb") as destination:
            for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                destination.write(chunk)
                digest.update(chunk)
                crc = zlib.crc32(chunk, crc)
                written += len(chunk)
        if written != expected_size or crc & 0xFFFFFFFF != expected_crc:
            raise ValueError(f"LongVALE member 解压后 size/CRC 失败: {media['member']}")
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output, "extracted"


def ffprobe_media(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    duration = float(payload["format"]["duration"])
    streams = payload.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not math.isfinite(duration) or duration <= 0 or not video_streams or not audio_streams:
        raise ValueError(f"LongVALE ffprobe 缺少有限时长/视频流/音轨: {path}")
    first_audio = audio_streams[0]
    if int(first_audio.get("channels") or 0) <= 0 or int(first_audio.get("sample_rate") or 0) <= 0:
        raise ValueError(f"LongVALE 音轨 channels/sample_rate 非法: {path}")
    return {
        "format_duration_sec": duration,
        "video_stream_count": len(video_streams),
        "audio_stream_count": len(audio_streams),
        "audio_channels": int(first_audio["channels"]),
        "audio_sample_rate": int(first_audio["sample_rate"]),
    }


def decode_event(path: Path, start_sec: float, end_sec: float) -> None:
    duration = min(1.0, end_sec - start_sec)
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-ss",
            f"{start_sec:.6f}",
            "-i",
            str(path),
            "-t",
            f"{duration:.6f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stderr.strip():
        raise ValueError(f"LongVALE event decode 产生错误输出: {path}: {result.stderr[:300]}")


def audit_one(zf: zipfile.ZipFile, row: dict[str, Any], media_root: Path) -> dict[str, Any]:
    path, extraction_status = extract_member(zf, row, media_root)
    probe = ffprobe_media(path)
    media_duration = float(probe["format_duration_sec"])
    annotation_duration = float(row["duration_sec"])
    duration_delta = media_duration - annotation_duration
    if abs(duration_delta) > MAX_DURATION_DELTA_SEC:
        raise ValueError(
            f"LongVALE 媒体/标注时长差超过 {MAX_DURATION_DELTA_SEC}s: "
            f"{row['video_id']} delta={duration_delta}"
        )
    if float(row["event_end_sec"]) > media_duration + EVENT_BOUNDARY_TOLERANCE_SEC:
        raise ValueError(f"LongVALE event 超出媒体时长: {row['sample_id']}")
    decode_event(path, float(row["event_start_sec"]), float(row["event_end_sec"]))
    return {
        "video_id": row["video_id"],
        "sample_id": row["sample_id"],
        "path": str(path),
        "status": "pass",
        "extraction_status": extraction_status,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "crc32": crc32_file(path),
        "annotation_duration_sec": annotation_duration,
        "duration_delta_sec": duration_delta,
        "event_start_sec": float(row["event_start_sec"]),
        "event_end_sec": float(row["event_end_sec"]),
        "ffprobe": probe,
        "ffmpeg_event_decode_pass": True,
    }


def build_review(
    plan_summary: dict[str, Any],
    input_hashes: dict[str, str],
    plan_sha256: str,
    mismatch_sha256: str,
    audit_rows: list[dict[str, Any]],
    *,
    plan_only: bool,
) -> dict[str, Any]:
    passed = [row for row in audit_rows if row.get("status") == "pass"]
    failed = [row for row in audit_rows if row.get("status") != "pass"]
    media_qualified = not plan_only and len(passed) == EXPECTED_ELIGIBLE_VIDEOS and not failed
    status = (
        "sealed_plan_ready_media_not_audited"
        if plan_only
        else "longvale_media_qualified_effect_locked"
        if media_qualified
        else "blocked_longvale_media_qualification"
    )
    return {
        "artifact_kind": "m4_ptpa_longvale_s05_media_qualification_review_v2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input_hashes": input_hashes,
        "sealed_plan_sha256": plan_sha256,
        "mismatch_mappings_sha256": mismatch_sha256,
        "plan_summary": plan_summary,
        "media_audit": {
            "expected_video_count": EXPECTED_ELIGIBLE_VIDEOS,
            "audited_video_count": len(audit_rows),
            "passed_video_count": len(passed),
            "failed_video_count": len(failed),
            "total_media_bytes": sum(int(row.get("bytes", 0)) for row in passed),
        },
        "longvale_media_blocker_closed": media_qualified,
        "permissions": {
            "sealed_manifest_qualified": media_qualified,
            "feature_extraction_allowed": False,
            "model_inference_allowed": False,
            "confirm_effect_access_allowed": False,
            "gpu_execution_allowed": False,
            "training_allowed": False,
            "formal_test_effect_access_allowed": False,
        },
        "next_stage_allowed": False,
        "next_stage": (
            "complete_s1_s2_s3a_before_longvale_effect_access"
            if media_qualified
            else "fix_or_replace_failed_media_without_reusing_exposed_or_reserved_videos"
        ),
    }


def render_review(review: dict[str, Any]) -> str:
    media = review["media_audit"]
    return "\n".join(
        [
            "# LongVALE S0.5 媒体资格终态 Review v2",
            "",
            f"结论：`{review['status'].upper()}`。",
            "",
            "## 冻结范围",
            "",
            f"- part19 物理视频：`{review['plan_summary']['archive_video_count']}`。",
            f"- 排除历史/预留：`{review['plan_summary']['excluded_exposed_count']}` / `{review['plan_summary']['excluded_reserved_count']}`。",
            f"- sealed 物理视频：`{review['plan_summary']['selected_physical_video_count']}`，每视频事件数 `1`。",
            "- 不按内容、标签难度或模型结果挑选；5 套 mismatch 映射已预先冻结。",
            "",
            "## 媒体验收",
            "",
            f"- expected/audited/pass/fail：`{media['expected_video_count']}/{media['audited_video_count']}/{media['passed_video_count']}/{media['failed_video_count']}`。",
            f"- 总媒体字节：`{media['total_media_bytes']}`。",
            f"- LongVALE 媒体 blocker closed：`{review['longvale_media_blocker_closed']}`。",
            "",
            "## 权限",
            "",
            "sealed manifest 即使通过，也不解锁特征、推理、confirm效果、GPU、训练或formal test。",
            "LongVALE效果必须等待S1 LLP、S2 Clotho-AQA和S3a AVCaps的前置门禁。",
            "",
            "## 人话结论",
            "",
            "这一步只证明新长视频试卷文件完整、声音可读且没有用过；不代表模型已经获准答题。",
            "",
        ]
    )


def main() -> None:
    args = parse_args()
    if shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None:
        raise RuntimeError("缺少 ffprobe/ffmpeg，禁止 LongVALE 媒体验收")
    lock, input_hashes, archive_item = verify_inputs()
    del lock
    annotations = parse_training_annotations(ANNOTATIONS)
    plan_rows, mismatch, plan_summary = build_sealed_plan(archive_item, annotations)
    if args.limit is not None:
        if not 1 <= args.limit <= len(plan_rows):
            raise ValueError("--limit 必须在 1..186")
        plan_rows = plan_rows[: args.limit]
    args.output_root.mkdir(parents=True, exist_ok=True)
    plan_path = args.output_root / "longvale_confirm_sealed_effect_locked.jsonl"
    mismatch_path = args.output_root / "longvale_mismatch_mappings.json"
    write_jsonl(plan_path, plan_rows)
    write_json(mismatch_path, mismatch)
    plan_sha = sha256_file(plan_path)
    mismatch_sha = sha256_file(mismatch_path)

    audit_rows: list[dict[str, Any]] = []
    if not args.plan_only:
        args.media_root.mkdir(parents=True, exist_ok=True)
        lock_handle = (args.media_root / ".qualification.lock").open("w", encoding="utf-8")
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"已有 LongVALE qualification 持有锁: {args.media_root}") from exc
        with zipfile.ZipFile(ARCHIVE, "r") as zf:
            for index, row in enumerate(plan_rows, start=1):
                try:
                    item = audit_one(zf, row, args.media_root)
                except Exception as exc:  # 需要把失败写入终态 Review
                    item = {
                        "video_id": row["video_id"],
                        "sample_id": row["sample_id"],
                        "status": "fail",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                audit_rows.append(item)
                print(
                    f"[{index}/{len(plan_rows)}] {item['status']} {row['video_id']}",
                    flush=True,
                )
    write_jsonl(args.output_root / "longvale_media_audit.jsonl", audit_rows)
    review = build_review(
        plan_summary,
        input_hashes,
        plan_sha,
        mismatch_sha,
        audit_rows,
        plan_only=args.plan_only,
    )
    write_json(args.output_root / "longvale_s05_media_review.json", review)
    (args.output_root / "LONGVALE_S05_MEDIA_REVIEW_ZH.md").write_text(
        render_review(review), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": review["status"],
                "selected": len(plan_rows),
                "passed": review["media_audit"]["passed_video_count"],
                "failed": review["media_audit"]["failed_video_count"],
            },
            ensure_ascii=False,
        )
    )
    if not args.plan_only and review["status"] != "longvale_media_qualified_effect_locked":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
