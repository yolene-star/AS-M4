#!/usr/bin/env python3
"""仅选择性解压并审计冻结的 UnAV S1 train/validation canary20。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/m4_ptpa_unav_s1_reconstruction_v1.json"
DEFAULT_ARCHIVE_ROOT = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/official_archive"
DEFAULT_COMPLETION = DEFAULT_ARCHIVE_ROOT / "archive_completion_manifest_v1.json"
DEFAULT_LEDGER = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/qualification_v1/unav_s1_physical_ledger.jsonl"
DEFAULT_PLAN = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/qualification_v1/unav_s1_media_canary20_plan_locked.jsonl"
DEFAULT_MEMBER_INDEX = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/archive_member_audit_v1/archive_member_index.jsonl"
DEFAULT_OUTPUT_ROOT = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/canary20_media_v1"
DEFAULT_FFPROBE = Path("/home/yjm/miniconda3/envs/package/bin/ffprobe")
DEFAULT_FFMPEG = Path("/home/yjm/miniconda3/envs/package/bin/ffmpeg")
PASS_STATUS = "PASS_CANARY20_MEDIA_QUALIFIED_EFFECT_LOCKED"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class MultipartReader:
    def __init__(self, paths: list[Path]):
        self._paths: Iterator[Path] = iter(paths)
        self._handle: Any = None

    def read(self, size: int = -1) -> bytes:
        chunks = []
        remaining = size
        while size < 0 or remaining > 0:
            if self._handle is None:
                try:
                    self._handle = next(self._paths).open("rb")
                except StopIteration:
                    break
            chunk = self._handle.read(-1 if size < 0 else remaining)
            if chunk:
                chunks.append(chunk)
                if size >= 0:
                    remaining -= len(chunk)
            else:
                self._handle.close()
                self._handle = None
        return b"".join(chunks)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def validate_scope(
    contract: dict[str, Any],
    plan_path: Path,
    member_index_path: Path,
) -> dict[str, Any]:
    if contract.get("status") != "canary20_selective_extraction_authorized_effect_locked":
        raise PermissionError("UnAV canary20选择性解压未授权")
    scope = contract["canary20_selective_extraction"]
    permissions = contract["permissions"]
    if permissions.get("selective_train_validation_extraction_allowed") is not True:
        raise PermissionError("UnAV train/validation选择性解压权限未开启")
    if permissions.get("archive_extraction_allowed") is not False:
        raise PermissionError("UnAV一般归档解压权限必须保持关闭")
    for key in (
        "feature_extraction_allowed", "effect_access_allowed",
        "test_media_download_allowed", "test_feature_extraction_allowed",
        "test_effect_access_allowed", "training_allowed", "redistribution_allowed",
    ):
        if permissions.get(key) is not False:
            raise PermissionError(f"UnAV canary20禁止项漂移：{key}")
    if sha256(plan_path) != scope["plan_sha256"]:
        raise ValueError("UnAV canary20计划SHA漂移")
    if sha256(member_index_path) != scope["member_index_sha256"]:
        raise ValueError("UnAV成员索引SHA漂移")
    return scope


def build_selection(
    plan_path: Path,
    ledger_path: Path,
    member_index_path: Path,
) -> list[dict[str, Any]]:
    plan = read_jsonl(plan_path)
    ledger = {row["physical_media_id"]: row for row in read_jsonl(ledger_path)}
    member_index = {
        row["physical_media_id"]: row for row in read_jsonl(member_index_path)
    }
    counts = Counter(row["official_split"] for row in plan)
    ids = [str(row["physical_media_id"]) for row in plan]
    if len(plan) != 20 or len(set(ids)) != 20 or counts != {"train": 10, "validation": 10}:
        raise ValueError("UnAV canary20必须为train/validation各10且物理ID唯一")
    output = []
    for row in plan:
        video_id = str(row["physical_media_id"])
        if row["official_split"] == "test":
            raise PermissionError("UnAV canary20禁止test")
        if row.get("historical_overlap") is not False:
            raise ValueError(f"UnAV canary20历史重叠：{video_id}")
        if row.get("feature_extraction_allowed") is not False or row.get("effect_access_allowed") is not False:
            raise PermissionError(f"UnAV canary20特征/效果权限漂移：{video_id}")
        metadata = ledger.get(video_id)
        member = member_index.get(video_id)
        if metadata is None or member is None:
            raise ValueError(f"UnAV canary20缺失ledger/member：{video_id}")
        if metadata.get("eligible") is not True or metadata["official_split"] != row["official_split"]:
            raise ValueError(f"UnAV canary20资格或split漂移：{video_id}")
        if member.get("metadata_present") is not True or member["official_split"] != row["official_split"]:
            raise ValueError(f"UnAV canary20成员映射漂移：{video_id}")
        output.append(
            {
                "plan": row,
                "metadata": metadata,
                "member": member,
            }
        )
    return sorted(output, key=lambda item: int(item["plan"]["selection_rank"]))


def extract_selected(
    archive_paths: list[Path],
    selection: list[dict[str, Any]],
    media_root: Path,
) -> dict[str, dict[str, Any]]:
    expected = {item["member"]["member_name"]: item for item in selection}
    media_root.mkdir(parents=True, exist_ok=True)
    completed = {}
    for item in selection:
        video_id = item["plan"]["physical_media_id"]
        target = media_root / f"{video_id}.mp4"
        expected_size = int(item["member"]["media_size_bytes"])
        if target.is_file():
            if target.stat().st_size != expected_size:
                raise ValueError(f"UnAV已有canary媒体大小漂移：{target}")
            completed[video_id] = {
                "path": target,
                "size": expected_size,
                "sha256": sha256(target),
                "extraction_status": "reused_size_verified",
            }

    pending_names = {
        item["member"]["member_name"]
        for item in selection
        if item["plan"]["physical_media_id"] not in completed
    }
    if pending_names:
        reader = MultipartReader(archive_paths)
        try:
            with tarfile.open(fileobj=reader, mode="r|gz") as archive:
                for member in archive:
                    if member.name not in pending_names:
                        continue
                    item = expected[member.name]
                    video_id = item["plan"]["physical_media_id"]
                    if not member.isreg() or PurePosixPath(member.name).name != f"{video_id}.mp4":
                        raise ValueError(f"UnAV canary成员类型或名称异常：{member.name}")
                    if member.size != int(item["member"]["media_size_bytes"]):
                        raise ValueError(f"UnAV canary成员大小漂移：{member.name}")
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError(f"UnAV canary成员无法读取：{member.name}")
                    target = media_root / f"{video_id}.mp4"
                    temporary = media_root / f".{video_id}.mp4.partial"
                    with temporary.open("wb") as handle:
                        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                            handle.write(chunk)
                    if temporary.stat().st_size != member.size:
                        raise ValueError(f"UnAV canary写出大小漂移：{video_id}")
                    temporary.replace(target)
                    completed[video_id] = {
                        "path": target,
                        "size": target.stat().st_size,
                        "sha256": sha256(target),
                        "extraction_status": "extracted_verified",
                    }
                    pending_names.remove(member.name)
                    if not pending_names:
                        break
        finally:
            reader.close()
    if pending_names:
        raise ValueError(f"UnAV canary成员未找到：{sorted(pending_names)}")
    return completed


def probe_and_decode(
    path: Path,
    ffprobe: Path,
    ffmpeg: Path,
    metadata: dict[str, Any],
    duration_tolerance: float,
    event_tolerance: float,
) -> dict[str, Any]:
    probe = subprocess.run(
        [
            str(ffprobe), "-v", "error", "-show_entries",
            "format=duration,format_name:stream=index,codec_type,codec_name,sample_rate,channels",
            "-of", "json", str(path),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    if probe.returncode:
        raise ValueError(f"ffprobe失败：{path.name}：{probe.stderr[-500:]}")
    payload = json.loads(probe.stdout)
    duration = float(payload["format"]["duration"])
    streams = payload.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not video_streams or not audio_streams:
        raise ValueError(f"UnAV canary缺少视频或音频轨：{path.name}")
    expected_duration = float(metadata["duration_sec"])
    duration_delta = duration - expected_duration
    max_event_offset = max(float(event["offset"]) for event in metadata["events"])
    if abs(duration_delta) > duration_tolerance:
        raise ValueError(f"UnAV canary时长偏差超限：{path.name}/{duration_delta}")
    if max_event_offset > duration + event_tolerance:
        raise ValueError(f"UnAV canary事件超出媒体：{path.name}/{max_event_offset}/{duration}")

    decode = subprocess.run(
        [
            str(ffmpeg), "-nostdin", "-v", "error", "-xerror", "-i", str(path),
            "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    if decode.returncode:
        raise ValueError(f"UnAV canary全媒体解码失败：{path.name}：{decode.stderr[-500:]}")
    audio_decode = subprocess.run(
        [
            str(ffmpeg), "-nostdin", "-v", "error", "-xerror", "-i", str(path),
            "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "null", "-",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    if audio_decode.returncode:
        raise ValueError(f"UnAV canary 16k单声道解码失败：{path.name}：{audio_decode.stderr[-500:]}")
    return {
        "container_format": payload["format"].get("format_name"),
        "media_duration_sec": duration,
        "metadata_duration_sec": expected_duration,
        "duration_delta_sec": duration_delta,
        "max_event_offset_sec": max_event_offset,
        "video_codecs": sorted({stream.get("codec_name") for stream in video_streams}),
        "audio_codecs": sorted({stream.get("codec_name") for stream in audio_streams}),
        "audio_sample_rates": sorted({int(stream["sample_rate"]) for stream in audio_streams if stream.get("sample_rate")}),
        "audio_channels": sorted({int(stream["channels"]) for stream in audio_streams if stream.get("channels")}),
        "full_media_decode_passed": True,
        "audio_decode_16khz_mono_passed": True,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    scope = validate_scope(contract, args.plan, args.member_index)
    completion = json.loads(args.completion.read_text(encoding="utf-8"))
    if completion.get("status") != "PASS_ARCHIVE_COMPLETE_VERIFIED_EFFECT_LOCKED":
        raise PermissionError("UnAV归档终态未通过")
    if not args.ffprobe.is_file() or not args.ffmpeg.is_file():
        raise FileNotFoundError("FFprobe/FFmpeg不存在")
    selection = build_selection(args.plan, args.ledger, args.member_index)
    if sum(int(item["member"]["media_size_bytes"]) for item in selection) != scope["expected_media_bytes"]:
        raise ValueError("UnAV canary20预期媒体总字节漂移")
    archive_paths = [args.archive_root / row["name"] for row in completion["results"]]
    extracted = extract_selected(archive_paths, selection, args.output_root / "media")
    results = []
    for item in selection:
        video_id = item["plan"]["physical_media_id"]
        extracted_row = extracted[video_id]
        audit = probe_and_decode(
            extracted_row["path"], args.ffprobe, args.ffmpeg, item["metadata"],
            float(scope["duration_max_abs_delta_sec"]),
            float(scope["event_offset_media_tolerance_sec"]),
        )
        results.append(
            {
                "selection_rank": item["plan"]["selection_rank"],
                "physical_media_id": video_id,
                "official_split": item["plan"]["official_split"],
                "event_count": item["metadata"]["event_count"],
                "event_labels": item["metadata"]["event_labels"],
                "path": str(extracted_row["path"]),
                "size": extracted_row["size"],
                "sha256": extracted_row["sha256"],
                "extraction_status": extracted_row["extraction_status"],
                "audit": audit,
                "passed": True,
            }
        )
    results_path = args.output_root / "canary20_media_results.jsonl"
    temporary = args.output_root / ".canary20_media_results.jsonl.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(results_path)
    summary = {
        "artifact_kind": "m4_ptpa_unav_s1_canary20_media_qualification",
        "format_version": 1,
        "status": PASS_STATUS,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested": 20,
        "passed": len(results),
        "failed": 0,
        "split_counts": dict(sorted(Counter(row["official_split"] for row in results).items())),
        "total_media_bytes": sum(row["size"] for row in results),
        "duration_max_abs_delta_sec": max(abs(row["audit"]["duration_delta_sec"]) for row in results),
        "results_path": str(results_path),
        "results_sha256": sha256(results_path),
        "plan_path": str(args.plan),
        "plan_sha256": sha256(args.plan),
        "test_media_accessed": False,
        "permissions": {
            "feature_extraction_allowed": False,
            "effect_access_allowed": False,
            "test_media_access_allowed": False,
            "test_effect_access_allowed": False,
            "training_allowed": False,
            "redistribution_allowed": False,
        },
    }
    write_json_atomic(args.output_root / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--completion", type=Path, default=DEFAULT_COMPLETION)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--member-index", type=Path, default=DEFAULT_MEMBER_INDEX)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--ffprobe", type=Path, default=DEFAULT_FFPROBE)
    parser.add_argument("--ffmpeg", type=Path, default=DEFAULT_FFMPEG)
    return parser.parse_args()


if __name__ == "__main__":
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    result = run(parse_args())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
