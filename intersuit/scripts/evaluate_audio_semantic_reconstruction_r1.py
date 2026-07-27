#!/usr/bin/env python
"""评测音频语义重构 R1 的冻结 BEATs clip-level 可分性。

本脚本不训练模型、不读取 AVE 官方 test 指标。每个视频只形成一个 clip 表示，
reference split 形成类别质心，query split 只做一次冻结评测。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import numpy as np
import torch
from safetensors import safe_open


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_FEATURE_MANIFEST = (
    INTERSUIT_ROOT
    / "harness/artifacts/audio_semantic_reconstruction_r1_pilot300/full/features"
    / "ave_hf_window_feature_manifest.jsonl"
)
DEFAULT_R0_ROOT = (
    INTERSUIT_ROOT / "harness/artifacts/audio_semantic_reconstruction_r0_contract"
)
DEFAULT_CURRENT_PROJECTOR_SHARD = (
    INTERSUIT_ROOT
    / "checkpoints/AS-M4-BEATs-semantic-contrast-canary150-v1"
    / "model-00003-of-00004.safetensors"
)
DEFAULT_OUTPUT = (
    INTERSUIT_ROOT / "harness/artifacts/audio_semantic_reconstruction_r1_pilot300/eval"
)
FIXED_SEEDS = (17, 23, 47)
BOOTSTRAP_REPLICATES = 10_000
ROBUST_TOP_M = 5
QUALIFIED_CLASS_MIN_TOTAL = 3
PROJECTOR_WEIGHT_KEY = "model.scene_audio_encoder.audio_projector.weight"
PROJECTOR_BIAS_KEY = "model.scene_audio_encoder.audio_projector.bias"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tensor_sha256(tensors: list[torch.Tensor]) -> str:
    digest = hashlib.sha256()
    for tensor in tensors:
        values = tensor.detach().cpu().contiguous().numpy()
        digest.update(str(values.dtype).encode("ascii"))
        digest.update(str(values.shape).encode("ascii"))
        digest.update(values.tobytes())
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def sample_weighted_mean(
    embeddings: torch.Tensor, valid_sample_count: torch.Tensor
) -> torch.Tensor:
    if embeddings.ndim != 2:
        raise ValueError("embeddings 必须为 [T,D]")
    weights = valid_sample_count.float()
    if weights.shape != (embeddings.shape[0],) or (weights <= 0).any():
        raise ValueError("valid_sample_count 与窗口数不一致或包含非正值")
    return (embeddings.float() * weights[:, None]).sum(dim=0) / weights.sum()


def window_rms_from_wav(
    audio_path: Path,
    valid_sample_counts: torch.Tensor,
    sample_rate: int = 16000,
) -> torch.Tensor:
    from scipy.io import wavfile
    from scipy.signal import resample_poly

    source_rate, values = wavfile.read(audio_path)
    waveform = torch.as_tensor(values)
    if waveform.ndim == 2:
        waveform = waveform.float().mean(dim=1)
    else:
        waveform = waveform.float()
    if values.dtype.kind in {"i", "u"}:
        info = np.iinfo(values.dtype)
        waveform /= float(max(abs(info.min), abs(info.max)))
    if source_rate != sample_rate:
        divisor = np.gcd(source_rate, sample_rate)
        waveform = torch.from_numpy(
            resample_poly(
                waveform.numpy(),
                sample_rate // divisor,
                source_rate // divisor,
            )
        ).float()
    rms = []
    for index, valid in enumerate(valid_sample_counts.tolist()):
        start = index * sample_rate
        segment = waveform[start : start + int(valid)]
        if segment.numel() != int(valid):
            raise ValueError(f"WAV 长度与 valid_sample_count 不一致：{audio_path}")
        rms.append(float(segment.square().mean().sqrt().item()))
    return torch.tensor(rms, dtype=torch.float32)


def robust_top_m_mean(
    embeddings: torch.Tensor,
    valid_sample_count: torch.Tensor,
    rms: torch.Tensor,
    top_m: int,
) -> torch.Tensor:
    if rms.shape != (embeddings.shape[0],):
        raise ValueError("RMS 与窗口数不一致")
    count = min(int(top_m), int(embeddings.shape[0]))
    indices = torch.topk(rms, k=count, largest=True, sorted=True).indices
    return sample_weighted_mean(
        embeddings[indices],
        valid_sample_count[indices],
    )


def normalize_rows(values: torch.Tensor) -> torch.Tensor:
    return torch.nn.functional.normalize(values.float(), dim=-1, eps=1e-12)


def bootstrap_mean_ci(
    values: list[float],
    seed: int,
    replicates: int = BOOTSTRAP_REPLICATES,
) -> list[float]:
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0:
        raise ValueError("bootstrap 输入为空")
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, array.size, size=(replicates, array.size))
    means = array[indices].mean(axis=1)
    return [
        float(np.quantile(means, 0.025)),
        float(np.quantile(means, 0.975)),
    ]


def evaluate_centroids(
    reference: list[dict[str, Any]],
    query: list[dict[str, Any]],
    representation_key: str,
    qualified_labels: list[str],
    bootstrap_seed: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    label_to_reference: dict[str, list[torch.Tensor]] = defaultdict(list)
    for row in reference:
        if row["canonical_label"] in qualified_labels:
            label_to_reference[row["canonical_label"]].append(
                row[representation_key]
            )
    missing = [
        label for label in qualified_labels if not label_to_reference.get(label)
    ]
    if missing:
        raise ValueError(f"qualified label 缺少 reference：{missing}")
    labels = sorted(qualified_labels)
    prototypes = normalize_rows(
        torch.stack(
            [
                torch.stack(label_to_reference[label]).mean(dim=0)
                for label in labels
            ]
        )
    )
    label_to_index = {label: index for index, label in enumerate(labels)}
    selected_query = [
        row for row in query if row["canonical_label"] in label_to_index
    ]
    if not selected_query:
        raise ValueError("qualified query 为空")
    vectors = normalize_rows(
        torch.stack([row[representation_key] for row in selected_query])
    )
    similarities = vectors @ prototypes.T
    prediction_indices = similarities.argmax(dim=1)
    per_sample: list[dict[str, Any]] = []
    per_class_correct: Counter[str] = Counter()
    per_class_total: Counter[str] = Counter()
    margins: list[float] = []
    for row_index, row in enumerate(selected_query):
        true_index = label_to_index[row["canonical_label"]]
        positive = float(similarities[row_index, true_index].item())
        wrong = similarities[row_index].clone()
        wrong[true_index] = -torch.inf
        hard_index = int(wrong.argmax().item())
        hard = float(wrong[hard_index].item())
        margin = positive - hard
        predicted = labels[int(prediction_indices[row_index].item())]
        correct = predicted == row["canonical_label"]
        per_class_correct[row["canonical_label"]] += int(correct)
        per_class_total[row["canonical_label"]] += 1
        margins.append(margin)
        per_sample.append(
            {
                "sample_id": row["sample_id"],
                "canonical_label": row["canonical_label"],
                "predicted_label": predicted,
                "hard_negative_label": labels[hard_index],
                "positive_similarity": positive,
                "hard_negative_similarity": hard,
                "margin": margin,
                "correct": correct,
            }
        )
    per_class_recall = {
        label: per_class_correct[label] / per_class_total[label]
        for label in labels
        if per_class_total[label]
    }
    macro_recall = mean(per_class_recall.values())
    accuracy = sum(item["correct"] for item in per_sample) / len(per_sample)
    rank_first = sum(item["margin"] > 0 for item in per_sample) / len(per_sample)
    random_baseline = 1.0 / len(labels)
    passing_class_fraction = (
        sum(value > random_baseline for value in per_class_recall.values())
        / len(per_class_recall)
    )
    metrics = {
        "query_count": len(per_sample),
        "qualified_label_count": len(labels),
        "macro_class_recall_at_1": macro_recall,
        "balanced_accuracy": macro_recall,
        "accuracy": accuracy,
        "rank_first": rank_first,
        "margin_mean": mean(margins),
        "margin_bootstrap_95_ci": bootstrap_mean_ci(
            margins, bootstrap_seed
        ),
        "per_class_recall_at_1": per_class_recall,
        "per_class_query_count": dict(sorted(per_class_total.items())),
        "class_fraction_recall_above_random": passing_class_fraction,
        "random_class_baseline": random_baseline,
        "confusion": [
            {
                "true": true,
                "predicted": predicted,
                "count": count,
            }
            for (true, predicted), count in sorted(
                Counter(
                    (item["canonical_label"], item["predicted_label"])
                    for item in per_sample
                ).items()
            )
        ],
    }
    return metrics, per_sample


def load_current_projector(path: Path) -> tuple[torch.Tensor, torch.Tensor, str]:
    with safe_open(path, framework="pt", device="cpu") as handle:
        keys = set(handle.keys())
        required = {PROJECTOR_WEIGHT_KEY, PROJECTOR_BIAS_KEY}
        if not required <= keys:
            raise ValueError(f"当前 scene-audio projector 权重缺失：{required - keys}")
        weight = handle.get_tensor(PROJECTOR_WEIGHT_KEY).float()
        bias = handle.get_tensor(PROJECTOR_BIAS_KEY).float()
    if weight.shape[1] != 768 or weight.shape[0] != bias.shape[0]:
        raise ValueError(
            f"当前 scene-audio projector 形状异常：{tuple(weight.shape)}, {tuple(bias.shape)}"
        )
    return weight, bias, tensor_sha256([weight, bias])


def load_representations(
    feature_rows: list[dict[str, Any]],
    current_weight: torch.Tensor,
    current_bias: torch.Tensor,
) -> list[dict[str, Any]]:
    output = []
    random_generator = torch.Generator().manual_seed(FIXED_SEEDS[0])
    random_weight = torch.randn(
        128, 768, generator=random_generator, dtype=torch.float32
    ) / np.sqrt(768.0)
    for row in feature_rows:
        payload = torch.load(
            row["audio_feature_path"], map_location="cpu", weights_only=True
        )
        embeddings = payload["audio_embedding"].float()
        valid = torch.as_tensor(
            payload.get("valid_sample_count"), dtype=torch.long
        )
        if embeddings.ndim != 2 or embeddings.shape[1] != 768:
            raise ValueError(f"BEATs 特征形状异常：{row['audio_feature_path']}")
        if not torch.isfinite(embeddings).all():
            raise ValueError(f"BEATs 特征包含 NaN/Inf：{row['audio_feature_path']}")
        primary = sample_weighted_mean(embeddings, valid)
        rms = window_rms_from_wav(Path(row["audio_path"]), valid)
        robust = robust_top_m_mean(
            embeddings, valid, rms, top_m=ROBUST_TOP_M
        )
        output.append(
            {
                **row,
                "raw_beats_clip_mean": primary,
                "raw_beats_clip_robust_top5": robust,
                "current_scene_audio_projector": torch.nn.functional.linear(
                    primary, current_weight, current_bias
                ),
                "random_linear_projector": torch.nn.functional.linear(
                    primary, random_weight
                ),
            }
        )
    return output


def aggregate_seed_metrics(seed_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "macro_class_recall_at_1",
        "balanced_accuracy",
        "accuracy",
        "rank_first",
        "margin_mean",
        "class_fraction_recall_above_random",
    )
    output: dict[str, Any] = {}
    for key in keys:
        values = [float(item[key]) for item in seed_metrics]
        output[key] = {
            "mean": mean(values),
            "std": pstdev(values),
            "worst_seed": min(values),
            "per_seed": values,
        }
    output["margin_ci_lower_worst_seed"] = min(
        item["margin_bootstrap_95_ci"][0] for item in seed_metrics
    )
    output["margin_ci_upper_worst_seed"] = min(
        item["margin_bootstrap_95_ci"][1] for item in seed_metrics
    )
    output["per_class_recall_at_1"] = seed_metrics[0]["per_class_recall_at_1"]
    output["per_class_query_count"] = seed_metrics[0]["per_class_query_count"]
    output["random_class_baseline"] = seed_metrics[0]["random_class_baseline"]
    output["query_count"] = seed_metrics[0]["query_count"]
    output["qualified_label_count"] = seed_metrics[0][
        "qualified_label_count"
    ]
    output["confusion"] = seed_metrics[0]["confusion"]
    return output


def evaluate_oracle(
    reference: list[dict[str, Any]],
    query: list[dict[str, Any]],
    qualified_labels: list[str],
) -> dict[str, Any]:
    labels = sorted(qualified_labels)
    identity = torch.eye(len(labels))
    label_to_vector = {
        label: identity[index] for index, label in enumerate(labels)
    }
    ref = [
        {**row, "oracle": label_to_vector[row["canonical_label"]]}
        for row in reference
        if row["canonical_label"] in label_to_vector
    ]
    qry = [
        {**row, "oracle": label_to_vector[row["canonical_label"]]}
        for row in query
        if row["canonical_label"] in label_to_vector
    ]
    metrics, _ = evaluate_centroids(
        ref, qry, "oracle", labels, bootstrap_seed=FIXED_SEEDS[0]
    )
    return metrics


def build_review(
    metrics: dict[str, Any], decision: dict[str, Any], evaluation_scope: str
) -> str:
    raw = metrics["representations"]["raw_beats_clip_mean"]
    return "\n".join(
        [
            f"# 音频语义重构 R1：{evaluation_scope} 冻结 BEATs 评测",
            "",
            f"- 结论：`{decision['status']}`",
            f"- raw BEATs macro Recall@1：{raw['macro_class_recall_at_1']['mean']:.6f}",
            f"- majority baseline：{metrics['baselines']['majority_accuracy']:.6f}",
            f"- raw BEATs rank-first：{raw['rank_first']['mean']:.6f}",
            f"- raw BEATs margin CI 最差 seed 下界：{raw['margin_ci_lower_worst_seed']:.6f}",
            f"- 合格类别中 Recall@1 高于随机基线的比例：{raw['class_fraction_recall_above_random']['mean']:.6f}",
            f"- 合格类别数：{metrics['qualified_label_count']}；query：{raw['query_count']} 条。",
            "- 主 pooling 为按 `valid_sample_count` 加权的 clip mean；固定 Top-5 RMS pooling 只作敏感性分析。",
            "- 每个视频只产生一个 clip 表示；没有窗口级标签扩增。",
            "- 本阶段未训练模型，未读取 AVE 官方 test 指标。",
            "",
            "## 下一步",
            decision["next_step_zh"],
            "",
        ]
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    feature_manifest = Path(args.feature_manifest).resolve()
    r0_root = Path(args.r0_root).resolve()
    current_projector_shard = Path(args.current_projector_shard).resolve()
    output_root = Path(args.output_root).resolve()
    rows = read_jsonl(feature_manifest)
    if len(rows) != args.expected_count or len(
        {row["sample_id"] for row in rows}
    ) != args.expected_count:
        raise ValueError(
            f"R1 feature manifest 必须恰好包含 {args.expected_count} 个唯一视频"
        )
    if {row["supervision_level"] for row in rows} != {"clip"}:
        raise ValueError("R1 pilot 只能使用 clip-level supervision")
    expected_splits = {args.reference_split, args.query_split}
    if {row["split"] for row in rows} != expected_splits:
        raise ValueError(f"R1 split 必须为 {sorted(expected_splits)}")

    label_counts = Counter(row["canonical_label"] for row in rows)
    qualified_labels = sorted(
        label
        for label, count in label_counts.items()
        if count >= args.qualified_class_min_total
    )
    if not qualified_labels:
        raise ValueError("没有合格类别")
    current_weight, current_bias, current_tensor_sha = load_current_projector(
        current_projector_shard
    )
    represented = load_representations(rows, current_weight, current_bias)
    reference = [
        row for row in represented if row["split"] == args.reference_split
    ]
    query = [row for row in represented if row["split"] == args.query_split]

    representation_keys = (
        "raw_beats_clip_mean",
        "raw_beats_clip_robust_top5",
        "current_scene_audio_projector",
        "random_linear_projector",
    )
    representation_metrics: dict[str, Any] = {}
    per_sample_output: list[dict[str, Any]] = []
    for representation_key in representation_keys:
        seed_metrics = []
        seed_per_sample = None
        for seed in FIXED_SEEDS:
            result, per_sample = evaluate_centroids(
                reference,
                query,
                representation_key,
                qualified_labels,
                bootstrap_seed=seed,
            )
            seed_metrics.append(result)
            if seed_per_sample is None:
                seed_per_sample = per_sample
        representation_metrics[representation_key] = aggregate_seed_metrics(
            seed_metrics
        )
        assert seed_per_sample is not None
        for row in seed_per_sample:
            per_sample_output.append(
                {"representation": representation_key, **row}
            )

    oracle = evaluate_oracle(reference, query, qualified_labels)
    qualified_query = [
        row for row in query if row["canonical_label"] in qualified_labels
    ]
    majority_count = max(
        Counter(row["canonical_label"] for row in qualified_query).values()
    )
    majority_accuracy = majority_count / len(qualified_query)
    raw = representation_metrics["raw_beats_clip_mean"]
    gates = {
        "macro_recall_vs_majority_plus_20pp": (
            raw["macro_class_recall_at_1"]["mean"]
            >= majority_accuracy + 0.20
        ),
        "rank_first_at_least_0_65": raw["rank_first"]["mean"] >= 0.65,
        "margin_ci_lower_above_zero": raw[
            "margin_ci_lower_worst_seed"
        ]
        > 0,
        "at_least_80pct_classes_above_random": (
            raw["class_fraction_recall_above_random"]["mean"] >= 0.80
        ),
        "oracle_evaluator_sanity": (
            oracle["macro_class_recall_at_1"] == 1.0
            and oracle["rank_first"] == 1.0
        ),
    }
    status = "PASS" if all(gates.values()) else "FAIL"
    metrics = {
        "status": status,
        "sample_count": len(rows),
        "reference_count": len(reference),
        "query_count": len(query),
        "qualified_label_min_total": args.qualified_class_min_total,
        "qualified_label_count": len(qualified_labels),
        "qualified_labels": qualified_labels,
        "representations": representation_metrics,
        "oracle_label_prototype": oracle,
        "baselines": {
            "majority_accuracy": majority_accuracy,
            "random_class_baseline": 1.0 / len(qualified_labels),
        },
        "gates": gates,
        "formal_test_metric_accessed": False,
        "training_performed": False,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    metrics_path = output_root / "metrics.json"
    per_sample_path = output_root / "per_sample.jsonl"
    write_json(metrics_path, metrics)
    write_jsonl(per_sample_path, per_sample_output)

    config = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stage": f"R1_{args.evaluation_scope}",
        "fixed_seeds": list(FIXED_SEEDS),
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "primary_pooling": "valid_sample_count_weighted_clip_mean",
        "secondary_pooling": "fixed RMS Top-5 valid_sample_count_weighted mean",
        "robust_top_m": ROBUST_TOP_M,
        "qualified_class_min_total": args.qualified_class_min_total,
        "reference_split": args.reference_split,
        "query_split": args.query_split,
        "evaluation_scope": args.evaluation_scope,
        "representation_protocol": "one clip representation per physical video",
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "git_head": git_head(),
        "implementation_sha256": sha256(Path(__file__).resolve()),
        "formal_test_metric_accessed": False,
        "training_performed": False,
    }
    config_path = output_root / "config.json"
    write_json(config_path, config)
    decision = {
        "stage": f"R1_{args.evaluation_scope}",
        "status": status,
        "raw_beats_qualified": status == "PASS",
        "projector_training_authorized": False,
        "fusion_changes_authorized": False,
        "selector_training_authorized": False,
        "formal_test_authorized": False,
        "next_step": (
            (
                "extract_expanded_train_validation_features"
                if args.evaluation_scope == "pilot300"
                else "run_r2_target_semantic_diagnosis"
            )
            if status == "PASS"
            else "stop_and_audit_encoder_data_labels"
        ),
        "next_step_zh": (
            (
                "pilot 通过；为 AVE_HF_EXPANDED 的 train/validation 提取同契约特征，"
                "继续完成 R1，不访问 test_locked。"
                if args.evaluation_scope == "pilot300"
                else "扩展集 R1 通过；按顺序进入 R2 合法音频语义目标诊断，仍不训练 projector。"
            )
            if status == "PASS"
            else "raw BEATs pilot 未通过；停止 projector、fusion 与 selector，审查媒体、标签和 encoder。"
        ),
    }
    decision_path = output_root / "decision.json"
    write_json(decision_path, decision)
    review_path = output_root / "review.md"
    review_path.write_text(
        build_review(metrics, decision, args.evaluation_scope),
        encoding="utf-8",
    )
    manifest_lock = {
        "inputs": {
            "feature_manifest": {
                "path": str(feature_manifest),
                "sha256": sha256(feature_manifest),
            },
            "r0_manifest_lock": {
                "path": str(r0_root / "manifest_lock.json"),
                "sha256": sha256(r0_root / "manifest_lock.json"),
            },
            "current_projector_shard": {
                "path": str(current_projector_shard),
                "tensor_keys": [PROJECTOR_WEIGHT_KEY, PROJECTOR_BIAS_KEY],
                "tensor_sha256": current_tensor_sha,
            },
        },
        "outputs": {
            "config.json": sha256(config_path),
            "metrics.json": sha256(metrics_path),
            "per_sample.jsonl": sha256(per_sample_path),
            "review.md": sha256(review_path),
            "decision.json": sha256(decision_path),
        },
    }
    write_json(output_root / "manifest_lock.json", manifest_lock)
    return {
        "status": status,
        "output_root": str(output_root),
        "gates": gates,
        "raw_beats": raw,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-manifest", default=str(DEFAULT_FEATURE_MANIFEST))
    parser.add_argument("--r0-root", default=str(DEFAULT_R0_ROOT))
    parser.add_argument(
        "--current-projector-shard",
        default=str(DEFAULT_CURRENT_PROJECTOR_SHARD),
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--expected-count", type=int, default=300)
    parser.add_argument("--reference-split", default="reference")
    parser.add_argument("--query-split", default="query")
    parser.add_argument(
        "--qualified-class-min-total",
        type=int,
        default=QUALIFIED_CLASS_MIN_TOTAL,
    )
    parser.add_argument("--evaluation-scope", default="pilot300")
    return parser


def main() -> None:
    result = run(build_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
