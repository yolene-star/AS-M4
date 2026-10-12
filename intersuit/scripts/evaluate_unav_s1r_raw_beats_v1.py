#!/usr/bin/env python3
"""独立评估 UnAV S1-R raw BEATs 事件语义；只用 train/validation，不训练。"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import torch


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def normalize(vector: torch.Tensor) -> torch.Tensor:
    return torch.nn.functional.normalize(vector.float(), dim=0, eps=1e-12)


def label_vectors(payload: dict) -> dict[str, torch.Tensor]:
    features = payload["audio_embedding"].float()
    timestamps = payload["timestamps"].float()
    by_label = defaultdict(list)
    for event in payload["events"]:
        by_label[event["event_label"]].append((float(event["onset"]), float(event["offset"])))
    output = {}
    for label, intervals in by_label.items():
        weights = torch.zeros(len(timestamps), dtype=torch.float32)
        for onset, offset in intervals:
            overlap = torch.minimum(timestamps[:, 1], torch.tensor(offset)) - torch.maximum(timestamps[:, 0], torch.tensor(onset))
            weights = torch.maximum(weights, overlap.clamp_min(0))
        if weights.sum() > 0:
            output[label] = normalize((features * weights[:, None]).sum(0) / weights.sum())
    return output


def bootstrap_ci(values_by_video: dict[str, list[float]], seed: int, replicates: int) -> list[float]:
    keys = sorted(values_by_video)
    values = np.asarray([np.mean(values_by_video[key]) for key in keys], dtype=np.float64)
    rng = np.random.default_rng(seed)
    means = np.empty(replicates, dtype=np.float64)
    for index in range(replicates):
        means[index] = rng.choice(values, size=len(values), replace=True).mean()
    return [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]


def stable_order(seed: int, namespace: str, value: str) -> str:
    return hashlib.sha256(f"{seed}\0{namespace}\0{value}".encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("status") != "AUTHORIZED_RAW_BEATS_TRAIN_VALIDATION_EVALUATION_TRAINING_LOCKED":
        raise PermissionError("raw BEATs评估未授权")
    permissions = contract["permissions"]
    if permissions.get("train_validation_effect_access_allowed") is not True:
        raise PermissionError("train/validation效果访问未授权")
    if permissions.get("test_access_allowed") is not False or permissions.get("training_allowed") is not False:
        raise PermissionError("test/training未保持关闭")
    if sha256(args.feature_manifest) != contract["feature_manifest_sha256"]:
        raise ValueError("feature manifest SHA漂移")

    rows = read_jsonl(args.feature_manifest)
    train_by_label = defaultdict(list)
    validation = []
    for index, row in enumerate(rows, start=1):
        payload = torch.load(row["feature_path"], map_location="cpu", weights_only=False)
        vectors = label_vectors(payload)
        if row["official_split"] == "train":
            for label, vector in vectors.items():
                train_by_label[label].append(vector)
        elif row["official_split"] == "validation":
            validation.append((row["video_id"], set(vectors), vectors))
        else:
            raise ValueError("发现非train/validation特征")
        if index % 1000 == 0:
            print(json.dumps({"loaded": index, "total": len(rows)}, ensure_ascii=False), flush=True)

    labels = sorted(train_by_label)
    prototypes = torch.stack([normalize(torch.stack(train_by_label[label]).mean(0)) for label in labels])
    label_index = {label: index for index, label in enumerate(labels)}
    class_correct = defaultdict(list)
    margins_by_video = defaultdict(list)
    correct_scores_by_video = defaultdict(list)
    queries = []
    for video_id, video_labels, vectors in validation:
        for label, vector in vectors.items():
            if label not in label_index:
                raise ValueError(f"validation标签未见于train:{label}")
            scores = prototypes @ vector
            target = label_index[label]
            correct = int(int(scores.argmax()) == target)
            wrong = torch.cat((scores[:target], scores[target + 1 :])).max()
            margin = float(scores[target] - wrong)
            class_correct[label].append(correct)
            margins_by_video[video_id].append(margin)
            correct_scores_by_video[video_id].append(float(scores[target]))
            queries.append((video_id, label, video_labels, vector, float(scores[target])))

    per_class = {label: float(np.mean(values)) for label, values in class_correct.items()}
    macro_r1 = float(np.mean(list(per_class.values())))
    all_correct = [value for values in class_correct.values() for value in values]
    balanced_accuracy = macro_r1
    mean_margin = float(np.mean([np.mean(values) for values in margins_by_video.values()]))
    margin_ci = bootstrap_ci(margins_by_video, 20260828, contract["evaluation"]["bootstrap_replicates"])

    mismatch_results = {}
    mismatch_pools = {
        label: [
            (other_id, other_label, other_vector)
            for other_id, other_labels, other_vectors in validation
            if label not in other_labels
            for other_label, other_vector in other_vectors.items()
            if other_label != label
        ]
        for label in labels
    }
    for seed in contract["evaluation"]["mismatch_seeds"]:
        differences = defaultdict(list)
        for video_id, label, video_labels, vector, correct_score in queries:
            candidates = mismatch_pools[label]
            if not candidates:
                raise ValueError(f"无合法mismatch:{video_id}/{label}")
            digest = stable_order(seed, "mismatch", f"{video_id}:{label}")
            offset = int(digest[:16], 16) % len(candidates)
            mismatch_vector = candidates[offset][2]
            mismatch_score = float(prototypes[label_index[label]] @ mismatch_vector)
            differences[video_id].append(correct_score - mismatch_score)
        physical_video_values = [float(np.mean(values)) for values in differences.values()]
        mismatch_results[str(seed)] = {
            "mean_correct_minus_mismatch": float(np.mean(physical_video_values)),
            "physical_video_bootstrap_ci95": bootstrap_ci(differences, seed, contract["evaluation"]["bootstrap_replicates"]),
            "passed": float(np.mean(physical_video_values)) > 0,
        }

    thresholds = contract["evaluation"]["thresholds"]
    checks = {
        "macro_recall_at_1": macro_r1 >= thresholds["macro_recall_at_1_min"],
        "balanced_accuracy": balanced_accuracy >= thresholds["balanced_accuracy_min"],
        "mean_margin": mean_margin > 0,
        "margin_ci_lower": margin_ci[0] > 0,
        "correct_minus_mismatch_all_seeds": all(result["passed"] for result in mismatch_results.values()),
    }
    result = {
        "status": "PASS_RAW_BEATS_S1R_GATE" if all(checks.values()) else "FAIL_RAW_BEATS_S1R_GATE",
        "labels": len(labels),
        "train_physical_video_label_vectors": sum(len(values) for values in train_by_label.values()),
        "validation_physical_videos": len(validation),
        "validation_queries": len(queries),
        "micro_accuracy": float(np.mean(all_correct)),
        "macro_recall_at_1": macro_r1,
        "balanced_accuracy": balanced_accuracy,
        "mean_positive_minus_best_wrong_margin": mean_margin,
        "margin_physical_video_bootstrap_ci95": margin_ci,
        "mismatch": mismatch_results,
        "checks": checks,
        "per_class_recall_at_1": per_class,
        "feature_manifest_sha256": sha256(args.feature_manifest),
        "training_performed": False,
        "test_accessed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "labels", "validation_queries", "macro_recall_at_1", "mean_positive_minus_best_wrong_margin", "margin_physical_video_bootstrap_ci95", "checks")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
