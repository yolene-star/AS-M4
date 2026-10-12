#!/usr/bin/env python3
"""评测 LLP pilot150 的冻结 BEATs S1 音频语义上界。

只读取 train/validation 的预计算特征与官方标注；不访问 test、不训练参数、
不修改 projector/fusion。统计单位始终是物理视频，重复 label-query 在 bootstrap
中按视频聚类。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Callable

import numpy as np
import torch
from scipy.io import wavfile


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/m4_ptpa_llp_s1_effect_eval_v1.json"
DEFAULT_RESPONSIBILITY = INTERSUIT_ROOT / "configs/m4_ptpa_audio_dataset_responsibility_v2.json"
DEFAULT_OUTPUT = INTERSUIT_ROOT / "datasets/LLP_PILOT_MEDIA_V2/s1_effect_eval_v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not rows:
        raise ValueError(f"JSONL 为空：{path}")
    return rows


def resolve_repo(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else REPO_ROOT / path


def validate_environment() -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("拒绝运行：必须通过 conda run -n M4 启动")
    if torch.cuda.is_initialized():
        raise RuntimeError("S1 evaluator 必须保持 CPU-only")
    return {
        "conda_env": "M4",
        "python": sys.executable,
        "python_version": platform.python_version(),
        "torch_version": torch.__version__,
        "device": "cpu",
    }


def validate_authorization(path: Path) -> dict[str, Any]:
    lock = json.loads(path.read_text(encoding="utf-8"))
    permissions = lock["permissions"]
    if permissions.get("llp_s1_internal_validation_effect_evaluation_allowed") is not True:
        raise PermissionError("责任锁未授权 LLP S1 validation 效果评测")
    review = lock["datasets"]["llp"]["pre_download_qualification"][
        "official_protocol_downloader"
    ]["license_review"]
    authorization = review.get("s1_effect_evaluation_authorization", {})
    expected_scope = "local_server_internal_noncommercial_llp_validation_only"
    if authorization.get("scope") != expected_scope:
        raise PermissionError("S1 validation 用户授权 scope 缺失或漂移")
    if any(
        authorization.get(key) is not False
        for key in ("training_allowed", "formal_test_allowed", "redistribution_allowed")
    ):
        raise PermissionError("S1 validation 禁止项未保持 fail-closed")
    return authorization


def validate_contract(contract: dict[str, Any]) -> dict[str, Path]:
    if contract.get("scope") != "internal_noncommercial_llp_train_validation_only":
        raise ValueError("S1 contract scope 漂移")
    permissions = contract["permissions"]
    if any(
        permissions.get(key) is not False
        for key in (
            "training_allowed",
            "projector_or_fusion_changes_allowed",
            "formal_test_allowed",
            "redistribution_allowed",
        )
    ):
        raise ValueError("S1 contract 禁止项未保持 fail-closed")
    paths = {}
    for name, item in contract["inputs"].items():
        if not isinstance(item, dict) or "path" not in item:
            continue
        path = resolve_repo(item["path"])
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise ValueError(f"S1 输入缺失或 SHA 漂移：{name}")
        paths[name] = path
    return paths


def stable_order(values: list[str], seed: int, namespace: str) -> list[str]:
    return sorted(
        values,
        key=lambda value: hashlib.sha256(
            f"{namespace}\0{seed}\0{value}".encode("utf-8")
        ).hexdigest(),
    )


def stable_pick(values: list[str], seed: int, namespace: str) -> str:
    if not values:
        raise ValueError(f"候选为空：{namespace}")
    return stable_order(values, seed, namespace)[0]


def normalize(vector: torch.Tensor) -> torch.Tensor:
    return torch.nn.functional.normalize(vector.float(), dim=-1, eps=1e-12)


def interval_overlaps(left: float, right: float, onset: float, offset: float) -> bool:
    return min(right, offset) - max(left, onset) > 1e-9


def active_indices(
    timestamps: torch.Tensor, intervals: list[tuple[float, float]]
) -> list[int]:
    return [
        index
        for index, (left, right) in enumerate(timestamps.tolist())
        if any(interval_overlaps(left, right, onset, offset) for onset, offset in intervals)
    ]


def load_dense_audio(
    path: Path,
) -> tuple[dict[str, dict[str, list[tuple[float, float]]]], list[dict[str, str]]]:
    grouped: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    skipped = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            onset, offset = float(row["onset"]), float(row["offset"])
            if offset <= onset:
                skipped.append(dict(row))
                continue
            grouped[row["filename"]][row["event_labels"]].append((onset, offset))
    return {video: dict(labels) for video, labels in grouped.items()}, skipped


def load_weak_labels(path: Path) -> dict[str, set[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {
            row["filename"]: {label for label in row["event_labels"].split(",") if label}
            for row in csv.DictReader(handle, delimiter="\t")
        }


def window_rms(audio_path: Path) -> torch.Tensor:
    rate, data = wavfile.read(audio_path)
    if rate != 16_000 or data.ndim != 1 or data.shape[0] != 160_000:
        raise ValueError(f"S1 WAV 合同不匹配：{audio_path}")
    values = torch.from_numpy(data.copy()).float() / 32768.0
    return values.reshape(10, 16_000).square().mean(dim=1).sqrt()


def load_feature_rows(
    rows: list[dict[str, Any]], dense: dict[str, dict[str, list[tuple[float, float]]]]
) -> list[dict[str, Any]]:
    output = []
    seen = set()
    for row in rows:
        media_id = str(row["physical_media_id"])
        if media_id in seen:
            raise ValueError(f"物理视频重复：{media_id}")
        seen.add(media_id)
        if row["official_split"] not in {"train", "validation"}:
            raise ValueError("S1 evaluator 拒绝非 train/validation")
        payload = torch.load(row["feature_path"], map_location="cpu", weights_only=True)
        embeddings = payload["audio_embedding"].float()
        timestamps = payload["timestamps"].float()
        if embeddings.shape != (10, 768) or timestamps.shape != (10, 2):
            raise ValueError(f"S1 特征合同不匹配：{media_id}")
        if not torch.isfinite(embeddings).all():
            raise ValueError(f"S1 特征含 NaN/Inf：{media_id}")
        clip_id = str(row["sample_id"]).split(":", 2)[2]
        rms = window_rms(Path(row["audio_path"]))
        top5 = torch.topk(rms, k=5, largest=True, sorted=True).indices
        dense_labels = dense.get(clip_id, {}) if row["official_split"] == "validation" else {}
        union_intervals = [interval for intervals in dense_labels.values() for interval in intervals]
        union_indices = active_indices(timestamps, union_intervals) if union_intervals else []
        output.append(
            {
                **row,
                "clip_id": clip_id,
                "embeddings": embeddings,
                "timestamps": timestamps,
                "rms": rms,
                "weak_labels": set(row["event_labels"]),
                "dense_labels": dense_labels,
                "clip_mean": normalize(embeddings.mean(dim=0)),
                "rms_top5": normalize(embeddings[top5].mean(dim=0)),
                "dense_event_union_mean": (
                    normalize(embeddings[union_indices].mean(dim=0))
                    if union_indices
                    else None
                ),
            }
        )
    return output


def representation_vector(row: dict[str, Any], representation: str, label: str) -> torch.Tensor:
    if representation in {"clip_mean", "rms_top5", "dense_event_union_mean"}:
        value = row[representation]
        if value is None:
            raise ValueError(f"表示缺失：{row['clip_id']}/{representation}")
        return value
    if representation == "dense_target_window_mean":
        intervals = row["dense_labels"].get(label, [])
        indices = active_indices(row["timestamps"], intervals)
        if not indices:
            raise ValueError(f"target dense 窗口缺失：{row['clip_id']}/{label}")
        return normalize(row["embeddings"][indices].mean(dim=0))
    raise ValueError(f"未知表示：{representation}")


def negative_vector(row: dict[str, Any], representation: str) -> torch.Tensor:
    if representation == "dense_target_window_mean":
        return row["dense_event_union_mean"]
    return representation_vector(row, representation, next(iter(row["dense_labels"]), ""))


def build_prototypes(
    train: list[dict[str, Any]], labels: list[str], representation: str, seed: int,
    fraction: float, minimum: int,
) -> tuple[torch.Tensor, dict[str, list[str]]]:
    selected: dict[str, list[str]] = {}
    prototypes = []
    for label in labels:
        candidates = [row for row in train if label in row["weak_labels"]]
        count = max(minimum, int(math.floor(len(candidates) * fraction)))
        count = min(count, len(candidates))
        ordered_ids = stable_order(
            [row["physical_media_id"] for row in candidates], seed, f"prototype:{label}"
        )
        selected_ids = ordered_ids[:count]
        selected[label] = selected_ids
        by_id = {row["physical_media_id"]: row for row in candidates}
        source_representation = representation if representation in {"clip_mean", "rms_top5"} else "clip_mean"
        vectors = [by_id[media_id][source_representation] for media_id in selected_ids]
        prototypes.append(normalize(torch.stack(vectors).mean(dim=0)))
    return torch.stack(prototypes), selected


def cluster_bootstrap_ci(
    rows: list[dict[str, Any]], value_key: str, seed: int, replicates: int
) -> list[float] | None:
    selected = [row for row in rows if row.get(value_key) is not None]
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in selected:
        grouped[row["physical_media_id"]].append(float(row[value_key]))
    ids = sorted(grouped)
    if not ids:
        return None
    rng = np.random.default_rng(seed)
    cluster_values = np.asarray(
        [np.mean(grouped[media_id], dtype=np.float64) for media_id in ids],
        dtype=np.float64,
    )
    means = np.empty(replicates, dtype=np.float64)
    for index in range(replicates):
        sampled = rng.integers(0, len(ids), size=len(ids))
        means[index] = cluster_values[sampled].mean()
    return [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]


def cluster_mean(rows: list[dict[str, Any]], value_key: str) -> float | None:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get(value_key) is not None:
            grouped[row["physical_media_id"]].append(float(row[value_key]))
    if not grouped:
        return None
    return mean(mean(values) for values in grouped.values())


def summarize_seed(
    per_query: list[dict[str, Any]], labels: list[str], seed: int, replicates: int
) -> dict[str, Any]:
    label_total = Counter(row["target_label"] for row in per_query)
    label_correct = Counter(row["target_label"] for row in per_query if row["correct"])
    per_class_recall = {
        label: label_correct[label] / label_total[label] for label in labels
    }
    macro = mean(per_class_recall.values())
    micro = mean(float(row["correct"]) for row in per_query)
    mismatch_recall = cluster_mean(per_query, "mismatch_correct_value")
    semantic_margin_mean = cluster_mean(per_query, "semantic_margin")
    mismatch_diff_mean = cluster_mean(per_query, "correct_minus_mismatch_target_score")
    acoustic_diff_mean = cluster_mean(per_query, "correct_minus_acoustic_target_score")
    same_video_count = sum(row["same_video_margin"] is not None for row in per_query)
    same_class = [
        float(row["cross_video_same_class_cosine"])
        for row in per_query
        if row["cross_video_same_class_cosine"] is not None
    ]
    return {
        "seed": seed,
        "query_count": len(per_query),
        "physical_video_count": len({row["physical_media_id"] for row in per_query}),
        "macro_recall_at_1": macro,
        "balanced_accuracy": macro,
        "micro_accuracy": micro,
        "rank_first": cluster_mean(per_query, "rank_first_value"),
        "mean_reciprocal_rank": cluster_mean(per_query, "reciprocal_rank"),
        "mismatch_recall_at_1": mismatch_recall,
        "correct_minus_mismatch_recall_at_1": cluster_mean(
            per_query, "correct_minus_mismatch_recall"
        ),
        "semantic_margin_mean": semantic_margin_mean,
        "semantic_margin_ci95": cluster_bootstrap_ci(
            per_query, "semantic_margin", seed + 100_000, replicates
        ),
        "correct_target_score_mean": cluster_mean(per_query, "positive_score"),
        "mismatch_target_score_mean": cluster_mean(per_query, "mismatch_target_score"),
        "correct_minus_mismatch_target_score_mean": mismatch_diff_mean,
        "correct_minus_mismatch_target_score_ci95": cluster_bootstrap_ci(
            per_query, "correct_minus_mismatch_target_score", seed + 200_000, replicates
        ),
        "correct_minus_acoustic_target_score_mean": acoustic_diff_mean,
        "correct_minus_acoustic_target_score_ci95": cluster_bootstrap_ci(
            per_query, "correct_minus_acoustic_target_score", seed + 300_000, replicates
        ),
        "same_video_eligible_query_count": same_video_count,
        "same_video_margin_mean": cluster_mean(per_query, "same_video_margin"),
        "same_video_margin_ci95": cluster_bootstrap_ci(
            per_query, "same_video_margin", seed + 400_000, replicates
        ),
        "cross_video_same_class_eligible_query_count": len(same_class),
        "cross_video_same_class_cosine_mean": cluster_mean(
            per_query, "cross_video_same_class_cosine"
        ),
        "per_class_recall_at_1": per_class_recall,
        "per_class_query_count": dict(sorted(label_total.items())),
        "confusion": [
            {"true": true, "predicted": predicted, "count": count}
            for (true, predicted), count in sorted(
                Counter((row["target_label"], row["predicted_label"]) for row in per_query).items()
            )
        ],
    }


def evaluate_representation_seed(
    train: list[dict[str, Any]], validation: list[dict[str, Any]], labels: list[str],
    representation: str, seed: int, fraction: float, minimum: int, replicates: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[str]]]:
    prototypes, selected = build_prototypes(
        train, labels, representation, seed, fraction, minimum
    )
    label_to_index = {label: index for index, label in enumerate(labels)}
    validation_by_id = {row["physical_media_id"]: row for row in validation}
    per_query = []
    for row in validation:
        dense_label_set = set(row["dense_labels"])
        for target_label in sorted(dense_label_set):
            target_index = label_to_index[target_label]
            query = representation_vector(row, representation, target_label)
            similarities = query @ prototypes.T
            order = torch.argsort(similarities, descending=True).tolist()
            rank = order.index(target_index) + 1
            predicted_index = order[0]
            wrong = similarities.clone()
            wrong[target_index] = -torch.inf
            hard_index = int(wrong.argmax().item())
            positive_score = float(similarities[target_index].item())

            eligible = [
                other["physical_media_id"]
                for other in validation
                if other["physical_media_id"] != row["physical_media_id"]
                and target_label not in other["dense_labels"]
            ]
            mismatch_id = stable_pick(
                eligible, seed, f"mismatch:{representation}:{row['physical_media_id']}:{target_label}"
            )
            mismatch_vec = negative_vector(validation_by_id[mismatch_id], representation)
            mismatch_scores = mismatch_vec @ prototypes.T
            mismatch_target = float(mismatch_scores[target_index].item())
            mismatch_predicted = int(mismatch_scores.argmax().item())

            acoustic_id = max(
                eligible,
                key=lambda media_id: (
                    float((query @ negative_vector(validation_by_id[media_id], representation)).item()),
                    -int(hashlib.sha256(media_id.encode()).hexdigest(), 16),
                ),
            )
            acoustic_vec = negative_vector(validation_by_id[acoustic_id], representation)
            acoustic_target = float((acoustic_vec @ prototypes[target_index]).item())

            target_indices = set(
                active_indices(row["timestamps"], row["dense_labels"][target_label])
            )
            inactive = [index for index in range(10) if index not in target_indices]
            same_video_index = max(inactive, key=lambda index: float(row["rms"][index])) if inactive else None
            same_video_score = (
                float((normalize(row["embeddings"][same_video_index]) @ prototypes[target_index]).item())
                if same_video_index is not None
                else None
            )

            same_class_ids = [
                other["physical_media_id"]
                for other in validation
                if other["physical_media_id"] != row["physical_media_id"]
                and target_label in other["dense_labels"]
            ]
            same_class_id = (
                stable_pick(
                    same_class_ids, seed,
                    f"same_class:{representation}:{row['physical_media_id']}:{target_label}",
                )
                if same_class_ids
                else None
            )
            same_class_cosine = None
            if same_class_id is not None:
                same_class_vec = representation_vector(
                    validation_by_id[same_class_id], representation, target_label
                )
                same_class_cosine = float((query @ same_class_vec).item())

            per_query.append(
                {
                    "representation": representation,
                    "seed": seed,
                    "physical_media_id": row["physical_media_id"],
                    "clip_id": row["clip_id"],
                    "target_label": target_label,
                    "predicted_label": labels[predicted_index],
                    "correct": predicted_index == target_index,
                    "rank": rank,
                    "rank_first_value": float(rank == 1),
                    "reciprocal_rank": 1.0 / rank,
                    "positive_score": positive_score,
                    "hardest_wrong_label": labels[hard_index],
                    "hardest_wrong_score": float(wrong[hard_index].item()),
                    "semantic_margin": positive_score - float(wrong[hard_index].item()),
                    "mismatch_media_id": mismatch_id,
                    "mismatch_predicted_label": labels[mismatch_predicted],
                    "mismatch_correct": mismatch_predicted == target_index,
                    "mismatch_correct_value": float(mismatch_predicted == target_index),
                    "correct_minus_mismatch_recall": float(
                        (predicted_index == target_index) - (mismatch_predicted == target_index)
                    ),
                    "mismatch_target_score": mismatch_target,
                    "correct_minus_mismatch_target_score": positive_score - mismatch_target,
                    "acoustic_hard_negative_media_id": acoustic_id,
                    "acoustic_hard_negative_target_score": acoustic_target,
                    "correct_minus_acoustic_target_score": positive_score - acoustic_target,
                    "same_video_high_rms_negative_window": same_video_index,
                    "same_video_high_rms_negative_score": same_video_score,
                    "same_video_margin": (
                        positive_score - same_video_score if same_video_score is not None else None
                    ),
                    "cross_video_same_class_media_id": same_class_id,
                    "cross_video_same_class_cosine": same_class_cosine,
                }
            )
    return summarize_seed(per_query, labels, seed, replicates), per_query, selected


def aggregate_representation(
    seed_metrics: list[dict[str, Any]], random_baseline: float, gate_delta: float
) -> dict[str, Any]:
    scalar_keys = (
        "macro_recall_at_1", "balanced_accuracy", "micro_accuracy", "rank_first",
        "mean_reciprocal_rank", "mismatch_recall_at_1",
        "correct_minus_mismatch_recall_at_1", "semantic_margin_mean",
        "correct_target_score_mean", "mismatch_target_score_mean",
        "correct_minus_mismatch_target_score_mean",
        "correct_minus_acoustic_target_score_mean",
    )
    aggregate = {}
    for key in scalar_keys:
        values = [float(item[key]) for item in seed_metrics]
        aggregate[key] = {
            "mean": mean(values), "std": pstdev(values), "worst_seed": min(values),
            "best_seed": max(values), "per_seed": values,
        }
    same_class_values = [
        item["cross_video_same_class_cosine_mean"]
        for item in seed_metrics
        if item["cross_video_same_class_cosine_mean"] is not None
    ]
    aggregate["cross_video_same_class_cosine_mean"] = {
        "mean": mean(same_class_values) if same_class_values else None,
        "per_seed": [item["cross_video_same_class_cosine_mean"] for item in seed_metrics],
        "eligible_queries_per_seed": [
            item["cross_video_same_class_eligible_query_count"] for item in seed_metrics
        ],
    }
    aggregate["semantic_margin_ci95_lower_worst_seed"] = min(
        item["semantic_margin_ci95"][0] for item in seed_metrics
    )
    aggregate["mismatch_score_diff_ci95_lower_worst_seed"] = min(
        item["correct_minus_mismatch_target_score_ci95"][0] for item in seed_metrics
    )
    aggregate["acoustic_score_diff_ci95_lower_worst_seed"] = min(
        item["correct_minus_acoustic_target_score_ci95"][0] for item in seed_metrics
    )
    same_ci = [item["same_video_margin_ci95"] for item in seed_metrics if item["same_video_margin_ci95"]]
    aggregate["same_video_margin_ci95_lower_worst_seed"] = min(item[0] for item in same_ci) if same_ci else None
    aggregate["same_video_margin_mean"] = {
        "per_seed": [item["same_video_margin_mean"] for item in seed_metrics],
        "eligible_queries_per_seed": [item["same_video_eligible_query_count"] for item in seed_metrics],
    }
    aggregate["per_class_recall_at_1_per_seed"] = [item["per_class_recall_at_1"] for item in seed_metrics]
    aggregate["per_class_query_count"] = seed_metrics[0]["per_class_query_count"]
    aggregate["confusion_per_seed"] = [item["confusion"] for item in seed_metrics]
    gates = {
        "macro_recall_at_1_minus_random_at_least_0_20_all_seeds": all(
            item["macro_recall_at_1"] >= random_baseline + gate_delta for item in seed_metrics
        ),
        "balanced_accuracy_minus_random_at_least_0_20_all_seeds": all(
            item["balanced_accuracy"] >= random_baseline + gate_delta for item in seed_metrics
        ),
        "semantic_margin_mean_positive_all_seeds": all(
            item["semantic_margin_mean"] > 0 for item in seed_metrics
        ),
        "semantic_margin_ci95_lower_positive_all_seeds": all(
            item["semantic_margin_ci95"][0] > 0 for item in seed_metrics
        ),
        "correct_target_score_above_mismatch_all_seeds": all(
            item["correct_minus_mismatch_target_score_mean"] > 0 for item in seed_metrics
        ),
        "correct_recall_above_mismatch_all_seeds": all(
            item["correct_minus_mismatch_recall_at_1"] > 0 for item in seed_metrics
        ),
        "formal_test_unaccessed": True,
    }
    aggregate["gates"] = gates
    aggregate["status"] = "PASS" if all(gates.values()) else "FAIL"
    aggregate["per_seed"] = seed_metrics
    return aggregate


def build_report(metrics: dict[str, Any]) -> str:
    lines = [
        "# LLP S1 冻结 BEATs 音频语义上界详细报告", "",
        f"- 总结论：`{metrics['status']}`",
        f"- 选中表示：`{metrics.get('selected_representation')}`",
        f"- 最强但未通过表示：`{metrics.get('best_failing_representation')}`",
        f"- train/validation：{metrics['train_video_count']}/{metrics['validation_video_count']} 个物理视频；query={metrics['query_count']}。",
        f"- 类别：{metrics['label_count']}；随机基线={metrics['random_baseline']:.6f}。",
        "- 固定 seed：17/29/43；每个 seed 使用 10,000 次物理视频 cluster bootstrap。",
        "- 未访问 LLP test，未训练任何参数。", "", "## 表示汇总", "",
        "| 表示 | 状态 | macro R@1 mean/worst | rank-first | margin mean / CI下界最差 | correct-mismatch R@1 | correct-mismatch score |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, item in metrics["representations"].items():
        lines.append(
            f"| {name} | {item['status']} | {item['macro_recall_at_1']['mean']:.6f}/{item['macro_recall_at_1']['worst_seed']:.6f} "
            f"| {item['rank_first']['mean']:.6f} | {item['semantic_margin_mean']['mean']:.6f}/{item['semantic_margin_ci95_lower_worst_seed']:.6f} "
            f"| {item['correct_minus_mismatch_recall_at_1']['mean']:.6f} | {item['correct_minus_mismatch_target_score_mean']['mean']:.6f} |"
        )
    lines.extend(["", "## 逐 seed 主指标", "",
                  "| 表示 | seed | macro R@1 | rank-first(视频等权) | MRR | semantic margin | margin CI95 | mismatch R@1 | correct-mismatch score |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |"])
    for name, item in metrics["representations"].items():
        for seed_item in item["per_seed"]:
            lines.append(
                f"| {name} | {seed_item['seed']} | {seed_item['macro_recall_at_1']:.6f} "
                f"| {seed_item['rank_first']:.6f} | {seed_item['mean_reciprocal_rank']:.6f} "
                f"| {seed_item['semantic_margin_mean']:.6f} "
                f"| [{seed_item['semantic_margin_ci95'][0]:.6f}, {seed_item['semantic_margin_ci95'][1]:.6f}] "
                f"| {seed_item['mismatch_recall_at_1']:.6f} "
                f"| {seed_item['correct_minus_mismatch_target_score_mean']:.6f} |"
            )
    lines.extend(["", "## 负样本诊断", "",
                  "| 表示 | correct-mismatch score / CI下界 | correct-acoustic score / CI下界 | same-video margin / CI下界 | same-class cosine |",
                  "| --- | ---: | ---: | ---: | ---: |"])
    for name, item in metrics["representations"].items():
        same_values = [value for value in item["same_video_margin_mean"]["per_seed"] if value is not None]
        lines.append(
            f"| {name} | {item['correct_minus_mismatch_target_score_mean']['mean']:.6f}/{item['mismatch_score_diff_ci95_lower_worst_seed']:.6f} "
            f"| {item['correct_minus_acoustic_target_score_mean']['mean']:.6f}/{item['acoustic_score_diff_ci95_lower_worst_seed']:.6f} "
            f"| {(mean(same_values) if same_values else float('nan')):.6f}/{item['same_video_margin_ci95_lower_worst_seed']:.6f} "
            f"| {item['cross_video_same_class_cosine_mean']['mean']:.6f} |"
        )
    lines.extend(["", "## 全部门禁", ""])
    for name, item in metrics["representations"].items():
        lines.append(f"### {name}")
        lines.append("")
        for gate, passed in item["gates"].items():
            lines.append(f"- `{gate}`：{'PASS' if passed else 'FAIL'}")
        lines.append("")
    diagnostic_name = metrics["best_failing_representation"]
    diagnostic = metrics["representations"][diagnostic_name]
    lines.extend([f"## `{diagnostic_name}` 逐类 Recall@1", "",
                  "| 类别 | query | seed17 | seed29 | seed43 | mean | worst |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
    per_seed_class = diagnostic["per_class_recall_at_1_per_seed"]
    for label, count in diagnostic["per_class_query_count"].items():
        values = [seed_values[label] for seed_values in per_seed_class]
        lines.append(
            f"| {label} | {count} | {values[0]:.6f} | {values[1]:.6f} | {values[2]:.6f} "
            f"| {mean(values):.6f} | {min(values):.6f} |"
        )
    confusion = Counter()
    for seed_confusion in diagnostic["confusion_per_seed"]:
        for item in seed_confusion:
            if item["true"] != item["predicted"]:
                confusion[(item["true"], item["predicted"])] += item["count"]
    lines.extend(["", "## 最强失败表示的主要混淆（3 seed 合计）", "",
                  "| true | predicted | count |", "| --- | --- | ---: |"])
    for (true, predicted), count in confusion.most_common(20):
        lines.append(f"| {true} | {predicted} | {count} |")
    lines.extend(
        [
            "", "## 限制", "",
            "- LLP train 只有弱 clip 标签；没有把弱标签复制为逐秒标签。",
            "- dense event/target window 表示使用 validation 官方逐秒 audio 标注，属于 oracle 上界，不是部署时可直接使用的 selector。",
            "- balanced accuracy 按项目既有多类合同定义为每类 Recall@1 的宏平均，因此数值与 macro Recall@1 相同。",
            "- 逐条 YouTube license 仍未核验；本结果仅限内部非商业研究，不构成法律或机构批准。",
            "",
        ]
    )
    return "\n".join(lines)


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def run(args: argparse.Namespace) -> dict[str, Any]:
    environment = validate_environment()
    authorization = validate_authorization(args.responsibility_lock)
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    paths = validate_contract(contract)
    source_rows = read_jsonl(paths["feature_manifest"])
    dense, skipped_dense_rows = load_dense_audio(paths["validation_dense_audio"])
    weak_train = load_weak_labels(paths["train_weak_labels"])
    weak_val = load_weak_labels(paths["validation_weak_labels"])
    rows = load_feature_rows(source_rows, dense)
    train = [row for row in rows if row["official_split"] == "train"]
    validation = [row for row in rows if row["official_split"] == "validation"]
    expected = contract["expected"]
    labels = sorted({label for row in train for label in row["weak_labels"]})
    query_count = sum(len(row["dense_labels"]) for row in validation)
    selected_clip_ids = {row["clip_id"] for row in validation}
    selected_skipped_count = sum(
        row["filename"] in selected_clip_ids for row in skipped_dense_rows
    )
    parsing_contract = contract["annotation_parsing"]
    if (
        len(skipped_dense_rows)
        != parsing_contract["expected_full_file_nonpositive_intervals"]
        or selected_skipped_count
        != parsing_contract["expected_selected_validation_overlap"]
    ):
        raise ValueError("dense audio 非正区间审计数量漂移")
    observed = (len(train), len(validation), len(labels), query_count)
    required = (
        expected["train_physical_videos"], expected["validation_physical_videos"],
        expected["labels"], expected["validation_video_label_queries"],
    )
    if observed != required:
        raise ValueError(f"S1 expected counts 漂移：observed={observed}, required={required}")
    if any(row["clip_id"] not in weak_train for row in train):
        raise ValueError("train feature 不在官方 weak split")
    if any(row["clip_id"] not in weak_val for row in validation):
        raise ValueError("validation feature 不在官方 weak split")
    if any(not row["dense_labels"] for row in validation):
        raise ValueError("validation 缺少 dense audio 标注")
    weak_dense_disagreements = [
        {
            "clip_id": row["clip_id"],
            "weak_only": sorted(row["weak_labels"] - set(row["dense_labels"])),
            "dense_audio_only": sorted(set(row["dense_labels"]) - row["weak_labels"]),
        }
        for row in validation if row["weak_labels"] != set(row["dense_labels"])
    ]

    seeds = list(contract["prototype_sampling"]["fixed_seeds"])
    fraction = float(contract["prototype_sampling"]["per_label_fraction"])
    minimum = int(contract["prototype_sampling"]["minimum_per_label"])
    replicates = int(contract["metrics"]["bootstrap"]["replicates"])
    random_baseline = 1.0 / len(labels)
    gate_delta = float(contract["gates"]["macro_recall_at_1_minus_random_min"])
    representation_results = {}
    all_per_query = []
    prototype_selection = {}
    for representation in contract["representations"]:
        seed_metrics = []
        prototype_selection[representation] = {}
        for seed in seeds:
            seed_result, per_query, selected = evaluate_representation_seed(
                train, validation, labels, representation, seed, fraction,
                minimum, replicates,
            )
            seed_metrics.append(seed_result)
            all_per_query.extend(per_query)
            prototype_selection[representation][str(seed)] = selected
        representation_results[representation] = aggregate_representation(
            seed_metrics, random_baseline, gate_delta
        )

    eligible = [name for name, result in representation_results.items() if result["status"] == "PASS"]
    selected_representation = None
    if eligible:
        selected_representation = sorted(
            eligible,
            key=lambda name: (
                -representation_results[name]["macro_recall_at_1"]["worst_seed"],
                -representation_results[name]["semantic_margin_ci95_lower_worst_seed"],
                name,
            ),
        )[0]
    best_failing_representation = sorted(
        representation_results,
        key=lambda name: (
            -representation_results[name]["macro_recall_at_1"]["worst_seed"],
            -representation_results[name]["semantic_margin_ci95_lower_worst_seed"],
            name,
        ),
    )[0]
    status = "PASS" if selected_representation else "FAIL"
    metrics = {
        "status": status,
        "selected_representation": selected_representation,
        "best_failing_representation": best_failing_representation,
        "train_video_count": len(train),
        "validation_video_count": len(validation),
        "query_count": query_count,
        "label_count": len(labels),
        "labels": labels,
        "random_baseline": random_baseline,
        "gate_random_plus_0_20": random_baseline + gate_delta,
        "weak_dense_disagreement_count": len(weak_dense_disagreements),
        "dense_audio_nonpositive_intervals_skipped_full_file": len(skipped_dense_rows),
        "dense_audio_nonpositive_intervals_skipped_selected_validation": selected_skipped_count,
        "representations": representation_results,
        "formal_test_accessed": False,
        "training_performed": False,
        "redistribution_allowed": False,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    metrics_path = args.output / "metrics.json"
    per_query_path = args.output / "per_query.jsonl"
    prototype_path = args.output / "prototype_selection.json"
    disagreement_path = args.output / "weak_dense_disagreements.json"
    report_path = args.output / "DETAILED_REPORT_ZH.md"
    write_json(metrics_path, metrics)
    write_jsonl(per_query_path, all_per_query)
    write_json(prototype_path, prototype_selection)
    write_json(disagreement_path, weak_dense_disagreements)
    report_path.write_text(build_report(metrics), encoding="utf-8")
    run_config = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "environment": environment,
        "authorization": authorization,
        "contract_path": str(args.contract.resolve()),
        "contract_sha256": sha256(args.contract),
        "implementation_sha256": sha256(Path(__file__).resolve()),
        "git_head": git_head(),
        "fixed_seeds": seeds,
        "bootstrap_replicates": replicates,
        "formal_test_accessed": False,
        "training_performed": False,
    }
    config_path = args.output / "run_config.json"
    write_json(config_path, run_config)
    lock = {
        "inputs": {
            "contract": {"path": str(args.contract.resolve()), "sha256": sha256(args.contract)},
            **{
                name: {"path": str(path.resolve()), "sha256": sha256(path)}
                for name, path in paths.items()
            },
        },
        "outputs": {
            path.name: sha256(path)
            for path in (metrics_path, per_query_path, prototype_path, disagreement_path, report_path, config_path)
        },
    }
    write_json(args.output / "manifest_lock.json", lock)
    return {"status": status, "selected_representation": selected_representation, "output": str(args.output)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--responsibility-lock", type=Path, default=DEFAULT_RESPONSIBILITY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))
