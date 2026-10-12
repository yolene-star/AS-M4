#!/usr/bin/env python3
"""独立审计 UnAV S1-R 冻结 BEATs 全量特征，不计算效果指标。"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import torch


EXPECTED_CHECKPOINT = "d43cbfad4d7b56381c061d7a24774f908d4d94c72961f6eb1d9090ff18cd8d34"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified-manifest", type=Path, required=True)
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    qualified = read_jsonl(args.qualified_manifest)
    features = read_jsonl(args.feature_manifest)
    qualified_by_id = {row["physical_media_id"]: row for row in qualified}
    errors = []
    split_counts = Counter()
    total_windows = 0
    seen = set()
    for index, row in enumerate(features, start=1):
        video_id = row["video_id"]
        source = qualified_by_id.get(video_id)
        if source is None or video_id in seen:
            errors.append(f"身份缺失或重复:{video_id}")
            continue
        seen.add(video_id)
        path = Path(row["feature_path"])
        if not path.is_file() or sha256(path) != row["feature_sha256"]:
            errors.append(f"文件或SHA异常:{video_id}")
            continue
        payload = torch.load(path, map_location="cpu", weights_only=False)
        embedding = payload.get("audio_embedding")
        valid = payload.get("valid_sample_count")
        timestamps = payload.get("timestamps")
        expected_windows = int(row["window_count"])
        if payload.get("video_id") != video_id or payload.get("media_sha256") != source["sha256"]:
            errors.append(f"媒体身份异常:{video_id}")
        if payload.get("checkpoint_sha256") != EXPECTED_CHECKPOINT or payload.get("frozen_beats") is not True:
            errors.append(f"编码器合同异常:{video_id}")
        if payload.get("training_performed") is not False or payload.get("effect_accessed") is not False or payload.get("test_accessed") is not False:
            errors.append(f"权限字段异常:{video_id}")
        if not isinstance(embedding, torch.Tensor) or embedding.shape != (expected_windows, 768) or not torch.isfinite(embedding).all():
            errors.append(f"embedding异常:{video_id}")
        if not isinstance(valid, torch.Tensor) or valid.shape != (expected_windows,) or (valid <= 0).any() or (valid > 16000).any():
            errors.append(f"valid_sample_count异常:{video_id}")
        if not isinstance(timestamps, torch.Tensor) or timestamps.shape != (expected_windows, 2) or not torch.isfinite(timestamps).all():
            errors.append(f"timestamps异常:{video_id}")
        split_counts[source["official_split"]] += 1
        total_windows += expected_windows
        if index % 500 == 0:
            print(json.dumps({"audited": index, "total": len(features), "errors": len(errors)}, ensure_ascii=False), flush=True)

    missing = set(qualified_by_id) - seen
    extra_files = {path.stem for path in args.feature_manifest.parent.joinpath("features").glob("*.pt")} - seen
    result = {
        "status": "PASS_INDEPENDENT_FROZEN_BEATS_FEATURE_AUDIT" if not errors and not missing and not extra_files else "FAIL_INDEPENDENT_FROZEN_BEATS_FEATURE_AUDIT",
        "qualified_rows": len(qualified),
        "feature_rows": len(features),
        "audited_unique": len(seen),
        "split_counts": dict(split_counts),
        "total_windows": total_windows,
        "embedding_dim": 768,
        "error_count": len(errors),
        "errors_first20": errors[:20],
        "missing_count": len(missing),
        "extra_feature_file_count": len(extra_files),
        "qualified_manifest_sha256": sha256(args.qualified_manifest),
        "feature_manifest_sha256": sha256(args.feature_manifest),
        "checkpoint_sha256": EXPECTED_CHECKPOINT,
        "training_performed": False,
        "effect_accessed": False,
        "test_accessed": False,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result["status"].startswith("FAIL"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
