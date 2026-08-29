#!/usr/bin/env python3
"""分阶段重算 UnAV v4/raw 的物理视频级 bootstrap CI。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Callable

import numpy as np
import torch
from torch import nn
import torch.nn.functional as F


class V4Projector(nn.Module):
    def __init__(self, dimension: int = 768, hidden_dimension: int = 256) -> None:
        super().__init__()
        self.down = nn.Linear(dimension, hidden_dimension)
        self.up = nn.Linear(hidden_dimension, dimension)

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        residual = self.up(F.gelu(self.down(features.float())))
        return F.normalize(features.float() + 0.1 * residual, dim=-1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def pool_events(payload: dict) -> list[tuple[torch.Tensor, str]]:
    timestamps = payload["timestamps"].float()
    embeddings = payload["audio_embedding"].float()
    pooled: list[tuple[torch.Tensor, str]] = []
    for event in payload["events"]:
        onset = torch.tensor(float(event["onset"]))
        offset = torch.tensor(float(event["offset"]))
        overlap = (
            torch.minimum(timestamps[:, 1], offset)
            - torch.maximum(timestamps[:, 0], onset)
        ).clamp_min(0)
        if overlap.sum() > 0:
            vector = (embeddings * overlap[:, None]).sum(0) / overlap.sum()
            pooled.append((vector, str(event["event_label"])))
    return pooled


def build_prototypes(
    train_events: list[tuple[str, list[tuple[torch.Tensor, str]]]],
    labels: list[str],
    transform: Callable[[torch.Tensor], torch.Tensor],
) -> torch.Tensor:
    vectors: dict[str, list[torch.Tensor]] = defaultdict(list)
    for _video_id, events in train_events:
        for vector, label in events:
            vectors[label].append(transform(vector))
    missing = sorted(set(labels) - set(vectors))
    if missing:
        raise ValueError(f"训练原型缺少类别：{missing}")
    return torch.stack(
        [F.normalize(torch.stack(vectors[label]).mean(0), dim=0) for label in labels]
    )


def prepare_cache(
    feature_manifest: Path,
    checkpoint: Path,
    cache_path: Path,
    progress_every: int,
) -> dict:
    rows = read_jsonl(feature_manifest)
    if any(row.get("official_split") == "test" for row in rows):
        raise ValueError("CI审计禁止读取test split")
    try:
        checkpoint_payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
    except TypeError:
        checkpoint_payload = torch.load(checkpoint, map_location="cpu")
    labels = [str(value) for value in checkpoint_payload["labels"]]
    projector = V4Projector()
    projector.load_state_dict(checkpoint_payload["state_dict"])
    projector.eval()

    train_events: list[tuple[str, list[tuple[torch.Tensor, str]]]] = []
    validation_events: list[tuple[str, list[tuple[torch.Tensor, str]]]] = []
    for index, row in enumerate(rows, start=1):
        payload = torch.load(row["feature_path"], map_location="cpu", weights_only=False)
        if payload.get("test_accessed") is not False:
            raise ValueError(f"特征未声明test_accessed=false：{row['video_id']}")
        target = train_events if row["official_split"] == "train" else validation_events
        target.append((str(row["video_id"]), pool_events(payload)))
        if progress_every > 0 and index % progress_every == 0:
            print(f"PREPARE_PROGRESS {index}/{len(rows)}", flush=True)

    with torch.no_grad():
        raw_transform = lambda value: F.normalize(value.float(), dim=0)
        projected_transform = lambda value: projector(value[None])[0]
        raw_prototypes = build_prototypes(train_events, labels, raw_transform)
        projected_prototypes = build_prototypes(train_events, labels, projected_transform)

        label_index = {label: index for index, label in enumerate(labels)}
        video_ids: list[str] = []
        counts: list[np.ndarray] = []
        raw_correct: list[np.ndarray] = []
        projected_correct: list[np.ndarray] = []
        raw_margin: list[float] = []
        projected_margin: list[float] = []
        preservation_numerator: list[int] = []
        preservation_denominator: list[int] = []
        video_label_queries: list[int] = []
        for video_index, (video_id, events) in enumerate(validation_events, start=1):
            video_count = np.zeros(len(labels), dtype=np.int32)
            video_raw_correct = np.zeros(len(labels), dtype=np.int32)
            video_projected_correct = np.zeros(len(labels), dtype=np.int32)
            raw_margins: list[float] = []
            projected_margins: list[float] = []
            last_decision: dict[str, tuple[bool, bool]] = {}
            for vector, label in events:
                target = label_index[label]
                raw_scores = raw_prototypes @ raw_transform(vector)
                projected_scores = projected_prototypes @ projected_transform(vector)
                raw_wrong = torch.cat((raw_scores[:target], raw_scores[target + 1 :])).max()
                projected_wrong = torch.cat(
                    (projected_scores[:target], projected_scores[target + 1 :])
                ).max()
                raw_ok = int(raw_scores.argmax()) == target
                projected_ok = int(projected_scores.argmax()) == target
                video_count[target] += 1
                video_raw_correct[target] += int(raw_ok)
                video_projected_correct[target] += int(projected_ok)
                raw_margins.append(float(raw_scores[target] - raw_wrong))
                projected_margins.append(float(projected_scores[target] - projected_wrong))
                last_decision[label] = (raw_ok, projected_ok)
            if not events:
                continue
            video_ids.append(video_id)
            counts.append(video_count)
            raw_correct.append(video_raw_correct)
            projected_correct.append(video_projected_correct)
            raw_margin.append(float(np.mean(raw_margins)))
            projected_margin.append(float(np.mean(projected_margins)))
            preservation_denominator.append(sum(raw for raw, _projected in last_decision.values()))
            preservation_numerator.append(
                sum(raw and projected for raw, projected in last_decision.values())
            )
            video_label_queries.append(len(last_decision))
            if progress_every > 0 and video_index % progress_every == 0:
                print(
                    f"SCORE_PROGRESS {video_index}/{len(validation_events)}",
                    flush=True,
                )

    arrays = {
        "video_ids": np.asarray(video_ids),
        "labels": np.asarray(labels),
        "counts": np.stack(counts),
        "raw_correct": np.stack(raw_correct),
        "projected_correct": np.stack(projected_correct),
        "raw_margin": np.asarray(raw_margin, dtype=np.float64),
        "projected_margin": np.asarray(projected_margin, dtype=np.float64),
        "preservation_numerator": np.asarray(preservation_numerator, dtype=np.int32),
        "preservation_denominator": np.asarray(preservation_denominator, dtype=np.int32),
        "video_label_queries": np.asarray(video_label_queries, dtype=np.int32),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache_path, **arrays)
    result = point_metrics(arrays)
    result.update(
        {
            "status": "PASS_CI_CACHE_PREPARED",
            "feature_manifest": str(feature_manifest.resolve()),
            "feature_manifest_sha256": sha256_file(feature_manifest),
            "checkpoint": str(checkpoint.resolve()),
            "checkpoint_sha256": sha256_file(checkpoint),
            "cache": str(cache_path.resolve()),
            "cache_sha256": sha256_file(cache_path),
            "train_videos": len(train_events),
            "validation_videos": len(video_ids),
            "test_accessed": False,
            "beats_training_performed": False,
            "m4_training_performed": False,
        }
    )
    return result


def macro_recall(correct: np.ndarray, counts: np.ndarray) -> float:
    valid = counts > 0
    return float(np.mean(correct[valid] / counts[valid]))


def point_metrics(arrays: dict[str, np.ndarray]) -> dict:
    total_counts = arrays["counts"].sum(axis=0)
    raw_macro = macro_recall(arrays["raw_correct"].sum(axis=0), total_counts)
    projected_macro = macro_recall(
        arrays["projected_correct"].sum(axis=0), total_counts
    )
    raw_margin = float(arrays["raw_margin"].mean())
    projected_margin = float(arrays["projected_margin"].mean())
    denominator = int(arrays["preservation_denominator"].sum())
    numerator = int(arrays["preservation_numerator"].sum())
    return {
        "raw_macro_recall_at_1": raw_macro,
        "projected_macro_recall_at_1": projected_macro,
        "macro_delta": projected_macro - raw_macro,
        "raw_margin": raw_margin,
        "projected_margin": projected_margin,
        "margin_delta": projected_margin - raw_margin,
        "raw_correct_preservation_rate": numerator / denominator if denominator else None,
        "validation_event_queries": int(total_counts.sum()),
        "validation_video_label_queries": int(arrays["video_label_queries"].sum()),
    }


def percentile_ci(values: np.ndarray) -> list[float]:
    lower, upper = np.quantile(values, [0.025, 0.975])
    return [float(lower), float(upper)]


def bootstrap_cache(cache_path: Path, seed: int, replicates: int, batch_size: int) -> dict:
    with np.load(cache_path) as cache:
        arrays = {key: cache[key] for key in cache.files}
    videos = int(arrays["counts"].shape[0])
    if videos <= 0 or replicates <= 0 or batch_size <= 0:
        raise ValueError("videos、replicates和batch_size必须为正")
    rng = np.random.default_rng(seed)
    probability = np.full(videos, 1.0 / videos)
    samples: dict[str, list[np.ndarray]] = defaultdict(list)
    completed = 0
    while completed < replicates:
        current = min(batch_size, replicates - completed)
        weights = rng.multinomial(videos, probability, size=current).astype(np.float64)
        counts = weights @ arrays["counts"]
        raw_correct = weights @ arrays["raw_correct"]
        projected_correct = weights @ arrays["projected_correct"]
        valid = counts > 0
        raw_recall = np.divide(raw_correct, counts, where=valid)
        projected_recall = np.divide(projected_correct, counts, where=valid)
        raw_macro = np.asarray(
            [row[mask].mean() for row, mask in zip(raw_recall, valid)],
            dtype=np.float64,
        )
        projected_macro = np.asarray(
            [row[mask].mean() for row, mask in zip(projected_recall, valid)],
            dtype=np.float64,
        )
        raw_margin = (weights @ arrays["raw_margin"]) / videos
        projected_margin = (weights @ arrays["projected_margin"]) / videos
        preservation_denominator = weights @ arrays["preservation_denominator"]
        preservation = np.divide(
            weights @ arrays["preservation_numerator"],
            preservation_denominator,
            out=np.full(current, np.nan),
            where=preservation_denominator > 0,
        )
        samples["raw_macro_recall_at_1"].append(raw_macro)
        samples["projected_macro_recall_at_1"].append(projected_macro)
        samples["macro_delta"].append(projected_macro - raw_macro)
        samples["raw_margin"].append(raw_margin)
        samples["projected_margin"].append(projected_margin)
        samples["margin_delta"].append(projected_margin - raw_margin)
        samples["raw_correct_preservation_rate"].append(preservation)
        completed += current
        print(f"BOOTSTRAP_PROGRESS seed={seed} {completed}/{replicates}", flush=True)
    intervals = {
        name: percentile_ci(np.concatenate(chunks)) for name, chunks in samples.items()
    }
    point = point_metrics(arrays)
    return {
        "status": "PASS_V4_PHYSICAL_VIDEO_BOOTSTRAP_CI",
        "seed": seed,
        "replicates": replicates,
        "cluster_unit": "physical_video",
        "point": point,
        "ci95": intervals,
        "checks": {
            "projected_margin_ci_lower_positive": intervals["projected_margin"][0] > 0,
            "macro_delta_ci_includes_zero": intervals["macro_delta"][0]
            <= 0
            <= intervals["macro_delta"][1],
            "margin_delta_ci_lower_positive": intervals["margin_delta"][0] > 0,
        },
        "cache": str(cache_path.resolve()),
        "cache_sha256": sha256_file(cache_path),
        "test_accessed": False,
        "beats_training_performed": False,
        "m4_training_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("prepare", "bootstrap"), required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--feature-manifest", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--progress-every", type=int, default=250)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--replicates", type=int, default=2000)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    if args.phase == "prepare":
        if args.feature_manifest is None or args.checkpoint is None:
            parser.error("prepare要求--feature-manifest和--checkpoint")
        result = prepare_cache(
            args.feature_manifest,
            args.checkpoint,
            args.cache,
            args.progress_every,
        )
    else:
        result = bootstrap_cache(args.cache, args.seed, args.replicates, args.batch_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
