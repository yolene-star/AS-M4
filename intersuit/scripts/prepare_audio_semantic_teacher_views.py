#!/usr/bin/env python
"""为 T0v2 完整 train 与 teacher_confirm 生成可追溯的 48 kHz 教师音频视图。"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import wave
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEVELOPMENT_MANIFEST = (
    INTERSUIT_ROOT
    / "datasets/AVE_HF_EXPANDED/teacher_development_full_train_manifest.jsonl"
)
CONFIRM_MANIFEST = (
    INTERSUIT_ROOT
    / "datasets/AVE_HF_EXPANDED/teacher_confirm_manifest.jsonl"
)
OUTPUT_ROOT = (
    INTERSUIT_ROOT / "datasets/AUDIO_SEMANTIC_TEACHER_T0_V2"
)
WAV_ROOT = OUTPUT_ROOT / "audio_48k_mono"
OUTPUT_MANIFEST = OUTPUT_ROOT / "teacher_view_full_train_confirm_manifest.jsonl"
OUTPUT_LOCK = OUTPUT_ROOT / "teacher_view_full_train_confirm_lock.json"
SAMPLE_RATE = 48_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"manifest 为空：{path}")
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            )


def wav_info(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frame_count = handle.getnframes()
        compression = handle.getcomptype()
    return {
        "channels": channels,
        "sample_width_bytes": sample_width,
        "sample_rate": sample_rate,
        "frame_count": frame_count,
        "duration_seconds": frame_count / sample_rate,
        "compression": compression,
    }


def ffmpeg_version(ffmpeg: str) -> str:
    result = subprocess.run(
        [ffmpeg, "-version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return result.stdout.splitlines()[0]


def decode(ffmpeg: str, source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp.wav")
    command = [
        ffmpeg,
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(temporary),
    ]
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 解码失败：{source}\n{result.stderr.strip()}"
        )
    info = wav_info(temporary)
    if (
        info["sample_rate"] != SAMPLE_RATE
        or info["channels"] != 1
        or info["sample_width_bytes"] != 2
        or info["compression"] != "NONE"
        or info["frame_count"] <= 0
    ):
        raise ValueError(f"教师 WAV contract 失败：{temporary}: {info}")
    temporary.replace(target)


def role(row: dict[str, Any], source: str) -> str:
    if source == "confirm":
        return "teacher_confirm"
    if (
        row.get("diagnostic_split") != "train"
        or row.get("teacher_development_allowed") is not True
        or row.get("teacher_development_subsampled") is not False
    ):
        raise ValueError("teacher-development 角色或完整性标记异常")
    return "teacher_development_full_train"


def build_rows(
    ffmpeg: str,
    development_rows: list[dict[str, Any]],
    confirm_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    combined = [("development", row) for row in development_rows] + [
        ("confirm", row) for row in confirm_rows
    ]
    physical_ids = [row["physical_media_id"] for _, row in combined]
    if len(physical_ids) != len(set(physical_ids)):
        raise ValueError("teacher-development 与 teacher_confirm 存在 physical overlap")
    outputs = []
    for index, (source_role, row) in enumerate(
        sorted(
            combined,
            key=lambda item: (
                role(item[1], item[0]),
                item[1]["canonical_label"],
                item[1]["physical_media_id"],
            ),
        ),
        start=1,
    ):
        video = Path(row["video_path"]).resolve()
        student_audio = Path(row["audio_path"]).resolve()
        if not video.is_file() or not student_audio.is_file():
            raise FileNotFoundError(
                f"缺少源媒体：{video} / {student_audio}"
            )
        collection = row["collection"].lower()
        teacher_audio = (
            WAV_ROOT / f"{collection}--{row['youtube_id']}.wav"
        ).resolve()
        if not teacher_audio.is_file():
            decode(ffmpeg, video, teacher_audio)
        teacher_info = wav_info(teacher_audio)
        student_info = wav_info(student_audio)
        if (
            teacher_info["sample_rate"] != SAMPLE_RATE
            or teacher_info["channels"] != 1
            or teacher_info["sample_width_bytes"] != 2
        ):
            raise ValueError(f"已有教师 WAV contract 异常：{teacher_audio}")
        if student_info["sample_rate"] != 16_000:
            raise ValueError(f"学生视图不是 16 kHz：{student_audio}")
        outputs.append(
            {
                "physical_media_id": row["physical_media_id"],
                "youtube_id": row["youtube_id"],
                "canonical_label": row["canonical_label"],
                "teacher_view_role": role(row, source_role),
                "source_video_path": str(video),
                "source_video_sha256": sha256(video),
                "student_audio_path": str(student_audio),
                "student_audio_sha256": sha256(student_audio),
                "student_audio_sample_rate": student_info["sample_rate"],
                "student_audio_frame_count": student_info["frame_count"],
                "teacher_audio_path": str(teacher_audio),
                "teacher_audio_sha256": sha256(teacher_audio),
                "teacher_audio_sample_rate": teacher_info["sample_rate"],
                "teacher_audio_frame_count": teacher_info["frame_count"],
                "teacher_audio_duration_seconds": teacher_info[
                    "duration_seconds"
                ],
                "teacher_audio_channels": teacher_info["channels"],
                "teacher_audio_sample_width_bytes": teacher_info[
                    "sample_width_bytes"
                ],
                "timeline_start_seconds": 0.0,
                "validation_accessed": False,
                "test_locked_accessed": False,
            }
        )
        if index % 50 == 0 or index == len(combined):
            print(f"已处理教师视图 {index}/{len(combined)}", flush=True)
    return outputs


def run() -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FileNotFoundError("未找到 ffmpeg")
    development_rows = read_jsonl(DEVELOPMENT_MANIFEST)
    confirm_rows = read_jsonl(CONFIRM_MANIFEST)
    if len(development_rows) != 524 or len(confirm_rows) != 84:
        raise ValueError(
            f"输入数量异常：development={len(development_rows)}, "
            f"confirm={len(confirm_rows)}"
        )
    development_counts = Counter(
        row["canonical_label"] for row in development_rows
    )
    if len(development_counts) != 28 or min(development_counts.values()) <= 0:
        raise ValueError(
            f"完整 train 必须覆盖 28 类：{dict(development_counts)}"
        )
    rows = build_rows(ffmpeg, development_rows, confirm_rows)
    write_jsonl(OUTPUT_MANIFEST, rows)
    counts = Counter(row["teacher_view_role"] for row in rows)
    durations = [row["teacher_audio_duration_seconds"] for row in rows]
    lock = {
        "schema_version": 1,
        "status": "PASS",
        "inputs": {
            str(DEVELOPMENT_MANIFEST): sha256(DEVELOPMENT_MANIFEST),
            str(CONFIRM_MANIFEST): sha256(CONFIRM_MANIFEST),
        },
        "decode_contract": {
            "command": (
                "ffmpeg -nostdin -v error -y -i <source> -map 0:a:0 "
                "-vn -ac 1 -ar 48000 -c:a pcm_s16le <target.tmp.wav>"
            ),
            "ffmpeg_path": ffmpeg,
            "ffmpeg_version": ffmpeg_version(ffmpeg),
            "sample_rate": SAMPLE_RATE,
            "channels": 1,
            "sample_width_bytes": 2,
            "tail_policy": "preserve_full_decoded_clip_no_truncation",
        },
        "outputs": {
            "manifest_path": str(OUTPUT_MANIFEST),
            "manifest_sha256": sha256(OUTPUT_MANIFEST),
            "row_count": len(rows),
            "role_counts": dict(sorted(counts.items())),
            "minimum_duration_seconds": min(durations),
            "maximum_duration_seconds": max(durations),
        },
        "implementation": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256(Path(__file__).resolve()),
            "python": sys.version,
        },
        "physical_overlap_count": 0,
        "teacher_development_subsampled": False,
        "validation_accessed": False,
        "test_locked_accessed": False,
    }
    write_json(OUTPUT_LOCK, lock)
    return {
        "status": "PASS",
        "row_count": len(rows),
        "role_counts": dict(sorted(counts.items())),
        "manifest": str(OUTPUT_MANIFEST),
        "manifest_sha256": sha256(OUTPUT_MANIFEST),
        "lock_sha256": sha256(OUTPUT_LOCK),
    }


def main() -> int:
    try:
        result = run()
    except Exception as exc:
        print(f"教师双视图构建失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
