#!/usr/bin/env python3
"""使用 UnAV 冻结 temporal projector 对 1 秒窗口打分并评估 Temporal Top-K。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import torch
import torch.nn.functional as F


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FEATURE_MANIFEST = REPO_ROOT / "intersuit/harness/artifacts/unav_s1r_beats_features_v1/feature_manifest.jsonl"
DEFAULT_VALIDATION_FEATURE_MANIFEST = REPO_ROOT / "intersuit/harness/artifacts/dynamic_selector_v2_v1/eventness_full/eventness_feature_manifest.jsonl"
DEFAULT_CHECKPOINT = REPO_ROOT / "intersuit/harness/artifacts/unav_s1r_temporal_projector_v2/summary.pt"
DEFAULT_ALLOWLIST = REPO_ROOT / "intersuit/harness/artifacts/dynamic_selector_v2_v1/full_validation_media_allowlist.json"
DEFAULT_VALIDATION_PARQUET = REPO_ROOT / "intersuit/datasets/UNAV_S1_V1/official_metadata/validation.parquet"
DEFAULT_OUTPUT = REPO_ROOT / "intersuit/harness/artifacts/unav_temporal_topk_v1"


def import_localization():
    path = REPO_ROOT / "intersuit/scripts/run_dynamic_selector_v2_v1_full_validation.py"
    spec = importlib.util.spec_from_file_location("unav_temporal_localization", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def import_projector():
    path = REPO_ROOT / "intersuit/scripts/evaluate_unav_s1r_temporal_projector_v2.py"
    spec = importlib.util.spec_from_file_location("unav_temporal_projector", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pool_events(data: dict[str, Any]) -> list[tuple[torch.Tensor, str]]:
    timestamps = data["timestamps"].float()
    embeddings = data["audio_embedding"].float()
    pooled = []
    for event in data["events"]:
        overlap = (
            torch.minimum(timestamps[:, 1], torch.tensor(float(event["offset"])))
            - torch.maximum(timestamps[:, 0], torch.tensor(float(event["onset"])))
        ).clamp_min(0)
        if float(overlap.sum()) > 0:
            pooled.append(((embeddings * overlap[:, None]).sum(0) / overlap.sum(), event["event_label"]))
    return pooled


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    localization = import_localization()
    projector_module = import_projector()
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    allowlist = json.loads(args.allowlist.read_text(encoding="utf-8"))
    allowed = {entry["video_id"]: entry for entry in allowlist["entries"]}
    if len(allowed) != 335 or sum(len(entry["event_labels"]) for entry in allowed.values()) != 443:
        raise ValueError("UnAV sparse allowlist 不是 335 videos / 443 samples")
    model = projector_module.ResidualProjector()
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    manifest_rows = read_jsonl(args.feature_manifest)
    train_rows = [row for row in manifest_rows if row["official_split"] == "train"]
    validation_feature_rows = read_jsonl(args.validation_feature_manifest)
    validation_rows = [
        row for row in validation_feature_rows if row["video_id"] in allowed
    ]
    if len(validation_rows) != 335:
        raise ValueError(f"UnAV sparse validation feature count mismatch: {len(validation_rows)}")

    prototype_items: dict[str, list[torch.Tensor]] = defaultdict(list)
    with torch.inference_mode():
        for row in train_rows:
            data = torch.load(row["feature_path"], map_location="cpu", weights_only=False)
            for embedding, label in pool_events(data):
                prototype_items[label].append(model(embedding[None])[0])
        labels = sorted(prototype_items)
        prototypes = torch.stack([F.normalize(torch.stack(prototype_items[label]).mean(0), dim=0) for label in labels])
        label_to_index = {label: index for index, label in enumerate(labels)}
        import pandas as pd
        parquet = pd.read_parquet(args.validation_parquet)
        official_events: dict[tuple[str, str], list[dict[str, float]]] = defaultdict(list)
        for record in parquet.to_dict(orient="records"):
            video_id = str(record["video_id"])
            label = str(record["event_label"])
            if video_id in allowed and label in set(allowed[video_id]["event_labels"]):
                timestamp = record["timestamp"]
                official_events[(video_id, label)].append(
                    {"onset": float(timestamp[0]), "offset": float(timestamp[1])}
                )
        by_k: dict[int, list[dict[str, Any]]] = {4: [], 8: []}
        for row in validation_rows:
            data = torch.load(row["feature_path"], map_location="cpu", weights_only=False)
            projected = model(data["audio_embedding"].float())
            timestamps = [tuple(float(value) for value in pair) for pair in data["timestamps"].tolist()]
            for label in allowed[row["video_id"]]["event_labels"]:
                events = official_events[(row["video_id"], label)]
                if not events:
                    raise ValueError(f"allowlist label 缺失：{row['video_id']}/{label}")
                score_vector = prototypes[label_to_index[label]] @ projected.T
                target = [[float(event["onset"]), float(event["offset"])] for event in events]
                for k in (4, 8):
                    ranked = torch.argsort(score_vector, descending=True).tolist()[:k]
                    metrics = localization.evaluate_selection(timestamps, target, ranked)
                    by_k[k].append(
                        {
                            "sample_id": f"{row['video_id']}:{label}",
                            "video_id": row["video_id"],
                            "event_label": label,
                            "ranked_indices": ranked,
                            "selected_timestamps": [timestamps[index] for index in sorted(ranked)],
                            **metrics.__dict__,
                        }
                    )
    result = {
        "status": "PASS_UNAV_TEMPORAL_TOPK_CPU_EVAL",
        "feature_manifest_sha256": sha256(args.feature_manifest),
        "validation_feature_manifest_sha256": sha256(args.validation_feature_manifest),
        "allowlist_sha256": sha256(args.allowlist),
        "validation_parquet_sha256": sha256(args.validation_parquet),
        "checkpoint_sha256": sha256(args.checkpoint),
        "window_schema": {"window_sec": 1.0, "hop_sec": 1.0, "overlapping": False},
        "train_video_count": len(train_rows),
        "validation_video_count": len(validation_rows),
        "validation_event_count": {str(k): len(rows) for k, rows in by_k.items()},
        "metrics": {
            str(k): {
                "event_coverage": mean(row["event_coverage"] for row in rows),
                "window_hit_at_k": mean(row["first_hit_rank"] is not None for row in rows),
                "positive_window_recall": mean(row["positive_window_recall"] for row in rows),
                "mean_best_tiou": mean(row["mean_best_tiou"] for row in rows),
            }
            for k, rows in by_k.items()
        },
        "model_training_performed": False,
        "beats_training_performed": False,
        "m4_fusion_training_performed": False,
        "test_accessed": False,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for k, rows in by_k.items():
        (args.output / f"per_sample_k{k}.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-manifest", type=Path, default=DEFAULT_FEATURE_MANIFEST)
    parser.add_argument("--validation-feature-manifest", type=Path, default=DEFAULT_VALIDATION_FEATURE_MANIFEST)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--validation-parquet", type=Path, default=DEFAULT_VALIDATION_PARQUET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, sort_keys=True))
