#!/usr/bin/env python3
"""生成 LLP pilot150 最终效果锁定媒体 manifest 与资格 Review。"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
SCRIPT_ROOT = INTERSUIT_ROOT / "scripts"
ARTIFACT_ROOT = INTERSUIT_ROOT / "harness/artifacts/m4_ptpa_audio_semantic_fusion_v1"
ORIGINAL_PLAN = ARTIFACT_ROOT / "llp_metadata_qualification_v2/llp_pilot_plan_locked_not_allowed.jsonl"
MEDIA_ROOT = INTERSUIT_ROOT / "datasets/LLP_PILOT_MEDIA_V2"
COVERAGE_PLAN = MEDIA_ROOT / "backfill_v2/llp_pilot_coverage_repair_round2_plan_locked.jsonl"
DEFAULT_OUTPUT = MEDIA_ROOT / "final_media_v2"
EXPECTED_ORIGINAL_SHA = "4df243662d9b98a049b80fd881052781ea4bbfa1f30ae1e420aafab84e5f3ff9"
EXPECTED_COVERAGE_SHA = "d0c0981cadc999d6ffdee65b07ecd934809de01ced3951b4a070b8491f83b7fb"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def load_metadata_module():
    path = SCRIPT_ROOT / "qualify_m4_ptpa_llp_metadata_v2.py"
    spec = importlib.util.spec_from_file_location("qualify_m4_ptpa_llp_metadata_v2_for_final", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def probe(path: Path, ffprobe: str) -> dict[str, Any]:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip()[-4000:] or f"ffprobe exit {result.returncode}")
    return json.loads(result.stdout)


def audit_row(row: dict[str, Any], role: str, ffprobe: str) -> dict[str, Any]:
    media = MEDIA_ROOT / "video" / f"{row['physical_media_id']}.mp4"
    if not media.is_file():
        raise ValueError(f"LLP final media 缺失: {media}")
    payload = probe(media, ffprobe)
    streams = payload.get("streams", [])
    video = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    duration = float(payload.get("format", {}).get("duration"))
    checks = {
        "duration_finite_positive": math.isfinite(duration) and duration > 0,
        "duration_within_one_second": abs(duration - 10.0) <= 1.0,
        "video_h264": bool(video) and video[0].get("codec_name") == "h264",
        "audio_aac": bool(audio) and audio[0].get("codec_name") == "aac",
        "sample_rate_positive": bool(audio) and int(audio[0].get("sample_rate") or 0) > 0,
        "channels_positive": bool(audio) and int(audio[0].get("channels") or 0) > 0,
    }
    if not all(checks.values()):
        raise ValueError(f"LLP final media 验收失败: {row['sample_id']}; {checks}")
    return {
        **row,
        "final_manifest_role": role,
        "media_path": str(media.relative_to(PROJECT_ROOT)),
        "media_bytes": media.stat().st_size,
        "media_sha256": sha256(media),
        "duration_sec": duration,
        "video_codec": video[0].get("codec_name"),
        "audio_codec": audio[0].get("codec_name"),
        "audio_sample_rate": int(audio[0].get("sample_rate") or 0),
        "audio_channels": int(audio[0].get("channels") or 0),
        "media_checks": checks,
        "media_qualified": True,
        "feature_extraction_allowed": False,
        "effect_access_allowed": False,
    }


def build_outputs(ffprobe: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if sha256(ORIGINAL_PLAN) != EXPECTED_ORIGINAL_SHA:
        raise ValueError("LLP original plan SHA 漂移")
    if sha256(COVERAGE_PLAN) != EXPECTED_COVERAGE_SHA:
        raise ValueError("LLP coverage plan SHA 漂移")
    original = read_jsonl(ORIGINAL_PLAN)
    coverage = read_jsonl(COVERAGE_PLAN)
    media_ids = {path.stem for path in (MEDIA_ROOT / "video").glob("*.mp4")}
    fixed = [row for row in original if row["physical_media_id"] in media_ids]
    if Counter(row["official_split"] for row in fixed) != Counter({"train": 93, "validation": 46}):
        raise ValueError("LLP fixed original split 数量漂移")
    selected = [audit_row(row, "original_pilot", ffprobe) for row in fixed]
    selected.extend(audit_row(row, "coverage_repair", ffprobe) for row in coverage)
    if len(selected) != 150 or len({row["physical_media_id"] for row in selected}) != 150:
        raise ValueError("LLP final manifest 数量或物理 ID 唯一性失败")
    split_counts = Counter(row["official_split"] for row in selected)
    if split_counts != Counter({"train": 100, "validation": 50}):
        raise ValueError(f"LLP final split 数量失败: {split_counts}")

    label_counts = {
        split: Counter(
            label
            for row in selected
            if row["official_split"] == split
            for label in row["event_labels"]
        )
        for split in ("train", "validation")
    }
    required = {"train": 4, "validation": 2}
    below = {
        split: {label: count for label, count in counts.items() if count < required[split]}
        for split, counts in label_counts.items()
    }
    if any(below.values()) or any(len(counts) != 25 for counts in label_counts.values()):
        raise ValueError(f"LLP final label coverage 失败: {below}")

    metadata = load_metadata_module()
    history_ids, _ = metadata.full_historical_youtube_ids()
    historical_overlap = sum(
        row["physical_media_id"].casefold() in history_ids for row in selected
    )
    if historical_overlap:
        raise ValueError("LLP final manifest 历史物理重叠非零")
    selected_ids = {row["physical_media_id"] for row in selected}
    unused = sorted(media_ids - selected_ids)
    durations = [row["duration_sec"] for row in selected]
    review = {
        "artifact_kind": "m4_ptpa_llp_pilot150_final_media_review_v2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "media_qualified_effect_locked",
        "input_hashes": {
            "original_plan": EXPECTED_ORIGINAL_SHA,
            "coverage_plan": EXPECTED_COVERAGE_SHA,
        },
        "rows": len(selected),
        "unique_physical_media": len(selected_ids),
        "split_counts": dict(split_counts),
        "role_counts": dict(Counter(row["final_manifest_role"] for row in selected)),
        "label_count": {split: len(counts) for split, counts in label_counts.items()},
        "minimum_per_label": {split: min(counts.values()) for split, counts in label_counts.items()},
        "below_required": below,
        "historical_overlap": historical_overlap,
        "duration_min_sec": min(durations),
        "duration_max_sec": max(durations),
        "duration_max_abs_delta_sec": max(abs(value - 10.0) for value in durations),
        "video_codec_counts": dict(Counter(row["video_codec"] for row in selected)),
        "audio_codec_counts": dict(Counter(row["audio_codec"] for row in selected)),
        "sample_rate_counts": dict(Counter(row["audio_sample_rate"] for row in selected)),
        "channel_counts": dict(Counter(row["audio_channels"] for row in selected)),
        "total_media_bytes": sum(row["media_bytes"] for row in selected),
        "unused_qualified_media_count": len(unused),
        "unused_qualified_media_ids": unused,
        "permissions": {
            "media_qualified": True,
            "feature_extraction_allowed": False,
            "gpu_execution_allowed": False,
            "training_allowed": False,
            "effect_access_allowed": False,
        },
        "next_stage_allowed": False,
        "next_stage": "independent_review_before_s1_feature_extraction",
    }
    return selected, review


def render_review(review: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# LLP pilot150 最终媒体资格 Review v2",
            "",
            f"结论：`{review['status'].upper()}`。媒体合格，特征/效果/训练仍锁定。",
            "",
            f"- 最终行数/物理媒体：`{review['rows']}/{review['unique_physical_media']}`。",
            f"- split：`{review['split_counts']}`；来源：`{review['role_counts']}`。",
            f"- 25类最低频次：`{review['minimum_per_label']}`；低于门槛：`{review['below_required']}`。",
            f"- 时长：`{review['duration_min_sec']}`–`{review['duration_max_sec']}` 秒。",
            f"- 视频/音频编码：`{review['video_codec_counts']}` / `{review['audio_codec_counts']}`。",
            f"- 历史重叠：`{review['historical_overlap']}`；未入最终 manifest 的额外合格媒体：`{review['unused_qualified_media_count']}`。",
            "",
            "## 人话结论",
            "",
            "最终150份合法试卷已经凑齐并逐个验收，但还没开始提特征或看实验效果。下一步先做独立Review，再决定是否进入S1。",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--ffprobe", default="/home/yjm/miniconda3/envs/llp-download/bin/ffprobe"
    )
    args = parser.parse_args()
    rows, review = build_outputs(args.ffprobe)
    manifest = args.output_root / "llp_pilot150_media_qualified_effect_locked.jsonl"
    review_path = args.output_root / "llp_pilot150_final_media_review.json"
    write_jsonl(manifest, rows)
    review["manifest_sha256"] = sha256(manifest)
    write_json(review_path, review)
    (args.output_root / "LLP_PILOT150_FINAL_MEDIA_REVIEW_ZH.md").write_text(
        render_review(review), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": review["status"],
                "rows": len(rows),
                "manifest_sha256": review["manifest_sha256"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
