#!/usr/bin/env python
"""对 AVE D1 validation 媒体执行只读、全流解码预检并冻结结果。"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable

import av


EXPECTED_VALIDATION_COUNT = 391


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-manifest",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/dynamic_selector_d1_ave_official_media/"
            "validation_media_inventory.jsonl"
        ),
    )
    parser.add_argument(
        "--media-lock",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/dynamic_selector_d1_ave_official_media/"
            "media_lock.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/"
            "dynamic_selector_d1_ave_validation_media_preflight"
        ),
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--expected-count", type=int, default=EXPECTED_VALIDATION_COUNT)
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_validation_rows(path: Path, expected_count: int) -> list[dict]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != expected_count:
        raise ValueError(
            f"validation 行数不符：expected={expected_count}, actual={len(rows)}"
        )
    ids = [row.get("youtube_id") for row in rows]
    if any(not isinstance(value, str) or not value for value in ids):
        raise ValueError("validation manifest 含非法 youtube_id")
    if len(ids) != len(set(ids)):
        raise ValueError("validation manifest 含重复物理视频")
    for row in rows:
        if row.get("official_split") != "validation":
            raise ValueError("输入含非 validation 行，拒绝运行")
        if row.get("test_sealed") is not False:
            raise ValueError("输入含 test/sealed 行，拒绝运行")
        if row.get("status") != "present":
            raise ValueError("validation manifest 含未冻结为 present 的媒体")
    return rows


def probe_media(media_path: Path) -> dict:
    with av.open(str(media_path), mode="r") as container:
        duration_sec = (
            float(container.duration / av.time_base)
            if container.duration is not None
            else None
        )
        return {
            "duration_sec": duration_sec,
            "video_stream_count": sum(stream.type == "video" for stream in container.streams),
            "audio_stream_count": sum(stream.type == "audio" for stream in container.streams),
        }


def decode_stream(media_path: Path, stream_kind: str) -> int:
    with av.open(str(media_path), mode="r") as container:
        if stream_kind == "video":
            return sum(1 for _ in container.decode(video=0))
        if stream_kind == "audio":
            return sum(1 for _ in container.decode(audio=0))
    raise ValueError(f"未知 stream_kind：{stream_kind}")


def audit_row(
    row: dict,
) -> dict:
    media_path = Path(row["media_path"])
    reasons = []
    actual_size = None
    actual_sha256 = None
    duration_sec = None
    video_stream_count = 0
    audio_stream_count = 0
    decoded_video_frame_count = 0
    decoded_audio_frame_count = 0
    full_video_decode_ok = False
    full_audio_decode_ok = False
    try:
        if not media_path.is_file():
            reasons.append("missing_media")
        else:
            actual_size = media_path.stat().st_size
            if actual_size != row.get("media_size_bytes"):
                reasons.append("media_size_changed")
            actual_sha256 = file_sha256(media_path)
            if actual_sha256 != row.get("media_sha256"):
                reasons.append("media_sha256_changed")
        if not reasons:
            probe = probe_media(media_path)
            duration_sec = probe["duration_sec"]
            video_stream_count = probe["video_stream_count"]
            audio_stream_count = probe["audio_stream_count"]
            if duration_sec is None or not math.isfinite(duration_sec) or duration_sec <= 0:
                reasons.append("invalid_duration")
            if video_stream_count < 1:
                reasons.append("missing_video_stream")
            if audio_stream_count < 1:
                reasons.append("missing_audio_stream")
        if not reasons:
            decoded_video_frame_count = decode_stream(media_path, "video")
            if decoded_video_frame_count > 0:
                full_video_decode_ok = True
            else:
                reasons.append("video_decode_empty")
            decoded_audio_frame_count = decode_stream(media_path, "audio")
            if decoded_audio_frame_count > 0:
                full_audio_decode_ok = True
            else:
                reasons.append("audio_decode_empty")
    except av.FFmpegError as exc:
        reasons.append(f"pyav_decode_failed:{str(exc)[:500]}")
    except OSError as exc:
        reasons.append(f"io_error:{exc}")
    return {
        "youtube_id": row["youtube_id"],
        "official_split": "validation",
        "media_path": str(media_path.resolve()),
        "expected_size_bytes": row.get("media_size_bytes"),
        "actual_size_bytes": actual_size,
        "expected_sha256": row.get("media_sha256"),
        "actual_sha256": actual_sha256,
        "duration_sec": duration_sec,
        "video_stream_count": video_stream_count,
        "audio_stream_count": audio_stream_count,
        "decoded_video_frame_count": decoded_video_frame_count,
        "decoded_audio_frame_count": decoded_audio_frame_count,
        "full_video_decode_ok": full_video_decode_ok,
        "full_audio_decode_ok": full_audio_decode_ok,
        "status": "valid" if not reasons else "invalid",
        "failure_reasons": reasons,
        "test_accessed": False,
    }


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_report(summary: dict) -> str:
    return f"""# Dynamic Window Selector D1 AVE validation 媒体解码预检报告

## 结论

状态：**{summary["status"]}**。本步骤只读取 validation 媒体并执行全视频流、全音频流解码，没有访问 test，也没有运行窗口策略或效果评测。

- validation 总数：{summary["sample_count"]}
- 解码有效：{summary["valid_count"]}
- 解码无效：{summary["invalid_count"]}
- failure 分布：`{summary["failure_counts"]}`
- duration 范围：`{summary["duration_range_sec"]}`
- Python 环境：`M4`
- test accessed：`false`

## 下一阶段边界

只有本预检全部通过后，下一步才可在 validation 上实现并运行 D1 策略矩阵。test 继续封存。
"""


def run(args: argparse.Namespace) -> dict:
    if args.workers <= 0:
        raise ValueError("--workers 必须为正数")
    started_at = datetime.now().astimezone()
    input_manifest = args.input_manifest.resolve()
    media_lock_path = args.media_lock.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_validation_rows(input_manifest, args.expected_count)
    media_lock = json.loads(media_lock_path.read_text(encoding="utf-8"))
    if media_lock.get("test_seal", {}).get("sealed") is not True:
        raise ValueError("上游 media lock 未保持 test sealed")
    if media_lock.get("counts", {}).get("validation", {}).get("missing_count") != 0:
        raise ValueError("上游 validation media lock 仍有缺失")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        records = list(executor.map(audit_row, rows))
    invalid = [record for record in records if record["status"] != "valid"]
    valid_durations = [
        record["duration_sec"]
        for record in records
        if record["status"] == "valid" and record["duration_sec"] is not None
    ]
    records_path = output_dir / "validation_decode_inventory.jsonl"
    invalid_path = output_dir / "validation_decode_invalid.jsonl"
    write_jsonl(records_path, records)
    write_jsonl(invalid_path, invalid)
    summary = {
        "stage": "D1-AVE-validation-media-decode-preflight",
        "status": "pass" if not invalid else "fail",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(),
        "python_environment": "M4",
        "sample_count": len(records),
        "valid_count": len(records) - len(invalid),
        "invalid_count": len(invalid),
        "failure_counts": dict(
            sorted(
                Counter(
                    reason.split(":", 1)[0]
                    for record in invalid
                    for reason in record["failure_reasons"]
                ).items()
            )
        ),
        "duration_range_sec": [
            min(valid_durations) if valid_durations else None,
            max(valid_durations) if valid_durations else None,
        ],
        "workers": args.workers,
        "decoder": {
            "backend": "PyAV",
            "pyav_version": av.__version__,
            "ffmpeg_library_versions": {
                name: list(version)
                for name, version in sorted(av.library_versions.items())
            },
        },
        "input_manifest": {
            "path": str(input_manifest),
            "sha256": file_sha256(input_manifest),
        },
        "media_lock": {
            "path": str(media_lock_path),
            "sha256": file_sha256(media_lock_path),
        },
        "outputs": {
            "inventory": {
                "path": str(records_path),
                "sha256": file_sha256(records_path),
            },
            "invalid": {
                "path": str(invalid_path),
                "sha256": file_sha256(invalid_path),
            },
        },
        "test_accessed": False,
        "prediction_run": False,
        "effect_evaluation_performed": False,
    }
    summary_path = output_dir / "summary.json"
    write_json(summary_path, summary)
    (output_dir / "report.md").write_text(build_report(summary), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": summary["status"],
                "valid_count": summary["valid_count"],
                "invalid_count": summary["invalid_count"],
            },
            ensure_ascii=False,
        )
    )
    return summary


def main() -> int:
    summary = run(parse_args())
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
