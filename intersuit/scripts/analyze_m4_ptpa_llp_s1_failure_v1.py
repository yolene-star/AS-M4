#!/usr/bin/env python3
"""诊断 LLP S1 FAIL 的弱标签原型污染、Speech 占比和类别不平衡。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
import torch
from scipy.stats import spearmanr


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/m4_ptpa_llp_s1_failure_analysis_v1.json"
DEFAULT_RESPONSIBILITY = INTERSUIT_ROOT / "configs/m4_ptpa_audio_dataset_responsibility_v2.json"
DEFAULT_OUTPUT = INTERSUIT_ROOT / "datasets/LLP_PILOT_MEDIA_V2/s1_failure_analysis_v1"


def import_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


effect = import_script_module(
    "llp_s1_effect_tools_for_failure_analysis",
    INTERSUIT_ROOT / "scripts/evaluate_m4_ptpa_llp_s1_effect_v1.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_repo(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def validate_environment() -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    return {
        "conda_env": "M4",
        "python": sys.executable,
        "python_version": platform.python_version(),
        "device": "cpu",
    }


def validate_authorization(path: Path) -> dict[str, Any]:
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock["permissions"].get("llp_s1_internal_failure_analysis_allowed") is not True:
        raise PermissionError("责任锁未授权 S1 failure analysis")
    if lock["permissions"].get("llp_s1_internal_validation_effect_evaluation_allowed") is not False:
        raise PermissionError("原 validation effect 权限必须保持关闭")
    downloader = lock["datasets"]["llp"]["pre_download_qualification"][
        "official_protocol_downloader"
    ]
    authorization = downloader.get("s1_failure_analysis_authorization", {})
    if authorization.get("scope") != "internal_noncommercial_s1_failure_analysis_only":
        raise PermissionError("S1 failure analysis scope 缺失或漂移")
    if any(
        authorization.get(key) is not False
        for key in ("training_allowed", "formal_test_allowed", "downstream_execution_allowed")
    ):
        raise PermissionError("S1 failure analysis 禁止项未 fail-closed")
    if downloader["s1_effect_evaluation"]["status"] != "FAIL":
        raise PermissionError("S1 原始 FAIL 结论漂移")
    return authorization


def validate_contract(contract: dict[str, Any]) -> dict[str, Path]:
    if contract.get("scope") != "internal_noncommercial_s1_failure_analysis_only":
        raise ValueError("failure analysis contract scope 漂移")
    if contract["frozen_result"].get("must_not_change_original_decision") is not True:
        raise ValueError("原 S1 决策保护缺失")
    if any(
        contract["permissions"].get(key) is not False
        for key in (
            "training_allowed", "formal_test_allowed", "downstream_execution_allowed",
            "original_s1_decision_change_allowed", "redistribution_allowed",
        )
    ):
        raise ValueError("failure analysis contract 禁止项漂移")
    paths = {}
    for name, item in contract["inputs"].items():
        path = resolve_repo(item["path"])
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise ValueError(f"failure analysis 输入缺失或 SHA 漂移：{name}")
        paths[name] = path
    return paths


def video_equal_mean(rows: list[dict[str, Any]], key: str) -> float:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[row["physical_media_id"]].append(float(row[key]))
    return mean(mean(values) for values in grouped.values())


def permutation_spearman(
    x: list[float], y: list[float], replicates: int, seed: int
) -> dict[str, Any]:
    if len(x) != len(y) or len(x) < 3:
        raise ValueError("Spearman 输入长度非法")
    observed = float(spearmanr(x, y).statistic)
    if not np.isfinite(observed):
        return {"rho": None, "permutation_p_two_sided": None, "n": len(x)}
    rng = np.random.default_rng(seed)
    values = np.asarray(y, dtype=np.float64)
    extreme = 0
    for _ in range(replicates):
        permuted = rng.permutation(values)
        rho = float(spearmanr(x, permuted).statistic)
        extreme += int(abs(rho) >= abs(observed) - 1e-15)
    return {
        "rho": observed,
        "permutation_p_two_sided": (extreme + 1) / (replicates + 1),
        "n": len(x),
        "replicates": replicates,
        "seed": seed,
    }


def count_bin(value: int, bins: dict[str, list[int | None]]) -> str:
    for name, (lower, upper) in bins.items():
        if value >= int(lower) and (upper is None or value <= int(upper)):
            return name
    raise ValueError(f"计数未落入预注册 bin：{value}")


def aggregate_bins(
    labels: list[str], counts: dict[str, int], recalls: dict[str, float],
    margins: dict[str, float], bins: dict[str, list[int | None]],
) -> dict[str, Any]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for label in labels:
        grouped[count_bin(counts[label], bins)].append(label)
    return {
        name: {
            "labels": sorted(items),
            "label_count": len(items),
            "count_mean": mean(counts[label] for label in items),
            "recall_mean": mean(recalls[label] for label in items),
            "semantic_margin_mean": mean(margins[label] for label in items),
        }
        for name, items in grouped.items()
    }


def build_counterfactual(
    train: list[dict[str, Any]], validation: list[dict[str, Any]], minimum: int
) -> dict[str, Any]:
    labels = sorted({label for row in train for label in row["weak_labels"]})
    pure_by_label = {
        label: [
            row for row in train
            if row["weak_labels"] == {label}
        ]
        for label in labels
    }
    eligible = sorted(
        label for label, rows in pure_by_label.items() if len(rows) >= minimum
    )
    pure_prototypes = torch.stack(
        [effect.normalize(torch.stack([row["clip_mean"] for row in pure_by_label[label]]).mean(0)) for label in eligible]
    )
    all_prototypes = torch.stack(
        [
            effect.normalize(
                torch.stack([row["clip_mean"] for row in train if label in row["weak_labels"]]).mean(0)
            )
            for label in eligible
        ]
    )
    label_to_index = {label: index for index, label in enumerate(eligible)}
    query_rows = []
    for row in validation:
        for label in sorted(set(row["dense_labels"]) & set(eligible)):
            query = effect.representation_vector(row, "dense_target_window_mean", label)
            target = label_to_index[label]
            for name, prototypes in (("all_weak_sources", all_prototypes), ("single_label_sources", pure_prototypes)):
                similarities = query @ prototypes.T
                wrong = similarities.clone()
                wrong[target] = -torch.inf
                query_rows.append(
                    {
                        "condition": name,
                        "physical_media_id": row["physical_media_id"],
                        "label": label,
                        "correct": int(similarities.argmax().item()) == target,
                        "margin": float(similarities[target].item() - wrong.max().item()),
                    }
                )
    result = {
        "eligible_labels": eligible,
        "eligible_label_count": len(eligible),
        "pure_source_counts": {label: len(pure_by_label[label]) for label in eligible},
        "query_count": sum(row["condition"] == "all_weak_sources" for row in query_rows),
        "conditions": {},
    }
    for condition in ("all_weak_sources", "single_label_sources"):
        rows = [row for row in query_rows if row["condition"] == condition]
        label_total = Counter(row["label"] for row in rows)
        label_correct = Counter(row["label"] for row in rows if row["correct"])
        result["conditions"][condition] = {
            "macro_recall_at_1": mean(
                label_correct[label] / label_total[label] for label in eligible
            ),
            "video_equal_margin_mean": video_equal_mean(rows, "margin"),
            "per_class_recall_at_1": {
                label: label_correct[label] / label_total[label] for label in eligible
            },
        }
    original = result["conditions"]["all_weak_sources"]
    pure = result["conditions"]["single_label_sources"]
    result["delta_single_label_minus_all_weak"] = {
        "macro_recall_at_1": pure["macro_recall_at_1"] - original["macro_recall_at_1"],
        "video_equal_margin_mean": pure["video_equal_margin_mean"] - original["video_equal_margin_mean"],
    }
    per_class_delta = {
        label: (
            pure["per_class_recall_at_1"][label]
            - original["per_class_recall_at_1"][label]
        )
        for label in eligible
    }
    result["per_class_recall_delta"] = per_class_delta
    result["improved_labels"] = sorted(
        label for label, delta in per_class_delta.items() if delta > 0
    )
    result["degraded_labels"] = sorted(
        label for label, delta in per_class_delta.items() if delta < 0
    )
    result["unchanged_labels"] = sorted(
        label for label, delta in per_class_delta.items() if delta == 0
    )
    result["diagnostic_only"] = True
    result["candidate_promotion_allowed"] = False
    return result


def build_report(result: dict[str, Any]) -> str:
    pollution = result["prototype_pollution"]
    speech = result["speech"]
    imbalance = result["imbalance"]
    counterfactual = result["single_label_counterfactual"]
    lines = [
        "# LLP S1 失败分析：弱标签污染、Speech 与类别不平衡", "",
        f"- 原始 S1 决策：`{result['original_s1_status']}`（本分析不得修改）。",
        f"- train 弱标签多标签视频比例：{pollution['train_multilabel_video_fraction']:.6f}。",
        f"- train 每视频弱标签数均值：{pollution['train_label_cardinality_mean']:.6f}。",
        f"- Speech train 视频占比：{speech['train_video_prevalence']:.6f}。",
        f"- Speech validation query 占比：{speech['validation_query_prevalence']:.6f}。",
        f"- prototype pair cosine mean/p95/max：{pollution['prototype_pair_cosine_mean']:.6f}/"
        f"{pollution['prototype_pair_cosine_p95_mean']:.6f}/{pollution['prototype_pair_cosine_max']:.6f}。",
        f"- 共现类对相对非共现类对 cosine 增量：{pollution['cooccurring_minus_noncooccurring_cosine_mean']:.6f}。",
        "- 未访问 test，未训练参数。", "", "## 相关性", "",
        "| 关系 | rho | permutation p | n |", "| --- | ---: | ---: | ---: |",
    ]
    for name, item in imbalance["correlations"].items():
        rho = "None" if item["rho"] is None else f"{item['rho']:.6f}"
        p = "None" if item["permutation_p_two_sided"] is None else f"{item['permutation_p_two_sided']:.6f}"
        lines.append(f"| {name} | {rho} | {p} | {item['n']} |")
    lines.extend(["", "## 单标签 prototype 反事实", "",
                  f"- 合格类别：{counterfactual['eligible_label_count']}；query：{counterfactual['query_count']}。",
                  f"- all weak macro R@1：{counterfactual['conditions']['all_weak_sources']['macro_recall_at_1']:.6f}。",
                  f"- single-label macro R@1：{counterfactual['conditions']['single_label_sources']['macro_recall_at_1']:.6f}。",
                  f"- delta：{counterfactual['delta_single_label_minus_all_weak']['macro_recall_at_1']:.6f}。",
                  f"- 改善/退化/不变类别数：{len(counterfactual['improved_labels'])}/"
                  f"{len(counterfactual['degraded_labels'])}/{len(counterfactual['unchanged_labels'])}。",
                  f"- margin delta：{counterfactual['delta_single_label_minus_all_weak']['video_equal_margin_mean']:.6f}。",
                  "- 该反事实只用于归因，不得晋级为候选。", "", "## 每类污染", "",
                  "| 类别 | train count | 单标签比例 | Speech共标签比例 | mean recall | mean margin |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for label, item in pollution["per_label"].items():
        lines.append(
            f"| {label} | {item['train_weak_count']} | {item['single_label_source_fraction_mean']:.6f} "
            f"| {item['speech_colabel_fraction_mean']:.6f} | {item['mean_recall_at_1']:.6f} "
            f"| {item['mean_semantic_margin']:.6f} |"
        )
    lines.extend(["", "## 边界", "",
                  "- train weak 标签是音画 union，不能把共标签全部解释成纯音频污染。",
                  "- 相关性和反事实不具有因果证明力，只用于定位后续数据审计方向。",
                  "- 原 S1 FAIL、门禁、test lock 和训练禁止状态保持不变。", ""])
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    environment = validate_environment()
    authorization = validate_authorization(args.responsibility_lock)
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    paths = validate_contract(contract)
    metrics = json.loads(paths["s1_metrics"].read_text(encoding="utf-8"))
    if metrics["status"] != "FAIL" or metrics["selected_representation"] is not None:
        raise ValueError("原 S1 FAIL 资产漂移")
    per_query = effect.read_jsonl(paths["s1_per_query"])
    selections = json.loads(paths["prototype_selection"].read_text(encoding="utf-8"))
    dense, _ = effect.load_dense_audio(paths["validation_dense_audio"])
    feature_rows = effect.read_jsonl(paths["feature_manifest"])
    rows = effect.load_feature_rows(feature_rows, dense)
    train = [row for row in rows if row["official_split"] == "train"]
    validation = [row for row in rows if row["official_split"] == "validation"]
    by_id = {row["physical_media_id"]: row for row in train}
    diagnostic_name = contract["frozen_result"]["diagnostic_representation"]
    diagnostic_queries = [row for row in per_query if row["representation"] == diagnostic_name]
    labels = sorted(metrics["labels"])
    train_counts = Counter(label for row in train for label in row["weak_labels"])
    val_counts = Counter(label for row in validation for label in row["dense_labels"])
    recall_by_label = {
        label: mean(
            seed_values[label]
            for seed_values in metrics["representations"][diagnostic_name]["per_class_recall_at_1_per_seed"]
        )
        for label in labels
    }
    margin_by_label = {
        label: mean(
            row["semantic_margin"] for row in diagnostic_queries if row["target_label"] == label
        )
        for label in labels
    }

    selection = selections[diagnostic_name]
    per_label_pollution = {}
    for label in labels:
        per_seed = []
        for seed in contract["frozen_result"]["fixed_seeds"]:
            selected_ids = selection[str(seed)][label]
            source_labels = [by_id[media_id]["weak_labels"] for media_id in selected_ids]
            colabels = Counter(
                other for values in source_labels for other in values if other != label
            )
            vectors = [by_id[media_id]["clip_mean"] for media_id in selected_ids]
            per_seed.append(
                {
                    "seed": seed,
                    "selected_source_count": len(selected_ids),
                    "weak_label_cardinality_mean": mean(len(values) for values in source_labels),
                    "single_label_source_fraction": mean(len(values) == 1 for values in source_labels),
                    "speech_colabel_fraction": (
                        mean("Speech" in values for values in source_labels)
                        if label != "Speech" else 0.0
                    ),
                    "top_colabels": colabels.most_common(5),
                    "prototype": effect.normalize(torch.stack(vectors).mean(0)),
                }
            )
        per_label_pollution[label] = {
            "train_weak_count": train_counts[label],
            "validation_query_count": val_counts[label],
            "weak_label_cardinality_mean": mean(item["weak_label_cardinality_mean"] for item in per_seed),
            "single_label_source_fraction_mean": mean(item["single_label_source_fraction"] for item in per_seed),
            "speech_colabel_fraction_mean": mean(item["speech_colabel_fraction"] for item in per_seed),
            "top_colabels_aggregated": Counter(
                pair[0] for item in per_seed for pair in item["top_colabels"]
            ).most_common(5),
            "mean_recall_at_1": recall_by_label[label],
            "mean_semantic_margin": margin_by_label[label],
            "per_seed": [
                {key: value for key, value in item.items() if key != "prototype"}
                for item in per_seed
            ],
        }
    prototype_nearest = {}
    prototype_geometry = []
    cooccurring_pairs = {
        tuple(sorted((left, right)))
        for row in train
        for left in row["weak_labels"]
        for right in row["weak_labels"]
        if left < right
    }
    for seed in contract["frozen_result"]["fixed_seeds"]:
        prototypes = torch.stack(
            [
                effect.normalize(
                    torch.stack(
                        [
                            by_id[media_id]["clip_mean"]
                            for media_id in selection[str(seed)][label]
                        ]
                    ).mean(0)
                )
                for label in labels
            ]
        )
        similarity = prototypes @ prototypes.T
        pair_values = []
        cooccurring_values = []
        noncooccurring_values = []
        for left_index, left in enumerate(labels):
            for right_index in range(left_index + 1, len(labels)):
                right = labels[right_index]
                value = float(similarity[left_index, right_index].item())
                pair_values.append(value)
                if tuple(sorted((left, right))) in cooccurring_pairs:
                    cooccurring_values.append(value)
                else:
                    noncooccurring_values.append(value)
        prototype_geometry.append(
            {
                "seed": seed,
                "pair_count": len(pair_values),
                "cosine_mean": mean(pair_values),
                "cosine_median": float(np.median(pair_values)),
                "cosine_p95": float(np.quantile(pair_values, 0.95)),
                "cosine_max": max(pair_values),
                "cooccurring_pair_count": len(cooccurring_values),
                "cooccurring_cosine_mean": mean(cooccurring_values),
                "noncooccurring_pair_count": len(noncooccurring_values),
                "noncooccurring_cosine_mean": mean(noncooccurring_values),
                "cooccurring_minus_noncooccurring_mean": (
                    mean(cooccurring_values) - mean(noncooccurring_values)
                ),
            }
        )
        for index, label in enumerate(labels):
            similarity[index, index] = -torch.inf
            wrong_index = int(similarity[index].argmax().item())
            prototype_nearest.setdefault(label, []).append(
                {
                    "seed": seed,
                    "nearest_label": labels[wrong_index],
                    "cosine": float(similarity[index, wrong_index].item()),
                }
            )
    for label in labels:
        per_label_pollution[label]["nearest_prototype_per_seed"] = prototype_nearest[label]

    cardinalities = [len(row["weak_labels"]) for row in train]
    prototype_entries = [
        (label, by_id[media_id]["weak_labels"])
        for seed in contract["frozen_result"]["fixed_seeds"]
        for label in labels if label != "Speech"
        for media_id in selection[str(seed)][label]
    ]
    pollution = {
        "train_label_cardinality_distribution": dict(sorted(Counter(cardinalities).items())),
        "train_label_cardinality_mean": mean(cardinalities),
        "train_multilabel_video_fraction": mean(value > 1 for value in cardinalities),
        "train_single_label_video_fraction": mean(value == 1 for value in cardinalities),
        "non_speech_prototype_source_entries": len(prototype_entries),
        "non_speech_prototype_sources_with_speech_fraction": mean(
            "Speech" in values for _, values in prototype_entries
        ),
        "prototype_geometry_per_seed": prototype_geometry,
        "prototype_pair_cosine_mean": mean(
            item["cosine_mean"] for item in prototype_geometry
        ),
        "prototype_pair_cosine_p95_mean": mean(
            item["cosine_p95"] for item in prototype_geometry
        ),
        "prototype_pair_cosine_max": max(
            item["cosine_max"] for item in prototype_geometry
        ),
        "cooccurring_minus_noncooccurring_cosine_mean": mean(
            item["cooccurring_minus_noncooccurring_mean"]
            for item in prototype_geometry
        ),
        "per_label": dict(sorted(per_label_pollution.items())),
    }

    speech_rows = [row for row in diagnostic_queries if row["target_label"] == "Speech"]
    non_speech_rows = [row for row in diagnostic_queries if row["target_label"] != "Speech"]
    outgoing = Counter(
        row["predicted_label"] for row in speech_rows if row["predicted_label"] != "Speech"
    )
    incoming = Counter(
        row["target_label"] for row in non_speech_rows if row["predicted_label"] == "Speech"
    )
    speech = {
        "train_video_count": sum("Speech" in row["weak_labels"] for row in train),
        "train_video_prevalence": mean("Speech" in row["weak_labels"] for row in train),
        "validation_video_count": sum("Speech" in row["dense_labels"] for row in validation),
        "validation_video_prevalence": mean("Speech" in row["dense_labels"] for row in validation),
        "validation_query_count": len(speech_rows) // 3,
        "validation_query_prevalence": (len(speech_rows) // 3) / metrics["query_count"],
        "target_recall_per_seed": [
            item["per_class_recall_at_1"]["Speech"]
            for item in metrics["representations"][diagnostic_name]["per_seed"]
        ],
        "target_recall_mean": recall_by_label["Speech"],
        "false_positive_rate_among_non_speech_queries": mean(
            row["predicted_label"] == "Speech" for row in non_speech_rows
        ),
        "top_outgoing_confusions": outgoing.most_common(10),
        "top_incoming_confusions": incoming.most_common(10),
        "non_speech_prototype_sources_with_speech_fraction": pollution[
            "non_speech_prototype_sources_with_speech_fraction"
        ],
    }

    recall_values = [recall_by_label[label] for label in labels]
    margin_values = [margin_by_label[label] for label in labels]
    single_fraction = [per_label_pollution[label]["single_label_source_fraction_mean"] for label in labels]
    non_speech_labels = [label for label in labels if label != "Speech"]
    replicates = int(contract["imbalance_analysis"]["spearman_permutation"]["replicates"])
    corr_seed = int(contract["imbalance_analysis"]["spearman_permutation"]["seed"])
    correlations = {
        "train_weak_count_vs_mean_recall": permutation_spearman(
            [train_counts[label] for label in labels], recall_values, replicates, corr_seed
        ),
        "validation_query_count_vs_mean_recall": permutation_spearman(
            [val_counts[label] for label in labels], recall_values, replicates, corr_seed + 1
        ),
        "train_weak_count_vs_mean_semantic_margin": permutation_spearman(
            [train_counts[label] for label in labels], margin_values, replicates, corr_seed + 2
        ),
        "single_label_fraction_vs_mean_recall": permutation_spearman(
            single_fraction, recall_values, replicates, corr_seed + 3
        ),
        "speech_colabel_fraction_vs_mean_recall_non_speech": permutation_spearman(
            [per_label_pollution[label]["speech_colabel_fraction_mean"] for label in non_speech_labels],
            [recall_by_label[label] for label in non_speech_labels], replicates, corr_seed + 4,
        ),
    }
    imbalance = {
        "train_weak_count_min": min(train_counts.values()),
        "train_weak_count_max": max(train_counts.values()),
        "train_max_min_ratio": max(train_counts.values()) / min(train_counts.values()),
        "validation_query_count_min": min(val_counts.values()),
        "validation_query_count_max": max(val_counts.values()),
        "validation_max_min_ratio": max(val_counts.values()) / min(val_counts.values()),
        "correlations": correlations,
        "train_count_bins": aggregate_bins(
            labels, train_counts, recall_by_label, margin_by_label,
            contract["imbalance_analysis"]["train_count_bins"],
        ),
        "validation_query_bins": aggregate_bins(
            labels, val_counts, recall_by_label, margin_by_label,
            contract["imbalance_analysis"]["validation_query_bins"],
        ),
    }
    counterfactual = build_counterfactual(
        train, validation,
        int(contract["counterfactual"]["minimum_single_label_sources_per_class"]),
    )
    result = {
        "status": "PASS_DIAGNOSTIC_COMPLETE",
        "original_s1_status": "FAIL_UNCHANGED",
        "prototype_pollution": pollution,
        "speech": speech,
        "imbalance": imbalance,
        "single_label_counterfactual": counterfactual,
        "formal_test_accessed": False,
        "training_performed": False,
        "downstream_execution_performed": False,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    result_path = args.output / "analysis.json"
    report_path = args.output / "REPORT_ZH.md"
    config_path = args.output / "run_config.json"
    write_json(result_path, result)
    report_path.write_text(build_report(result), encoding="utf-8")
    write_json(
        config_path,
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "environment": environment,
            "authorization": authorization,
            "contract_path": str(args.contract.resolve()),
            "contract_sha256": sha256(args.contract),
            "implementation_sha256": sha256(Path(__file__).resolve()),
            "formal_test_accessed": False,
            "training_performed": False,
        },
    )
    write_json(
        args.output / "manifest_lock.json",
        {
            "inputs": {
                "contract": {"path": str(args.contract.resolve()), "sha256": sha256(args.contract)},
                **{
                    name: {"path": str(path.resolve()), "sha256": sha256(path)}
                    for name, path in paths.items()
                },
            },
            "outputs": {
                result_path.name: sha256(result_path),
                report_path.name: sha256(report_path),
                config_path.name: sha256(config_path),
            },
        },
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--responsibility-lock", type=Path, default=DEFAULT_RESPONSIBILITY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    result = run(parse_args())
    print(json.dumps({"status": result["status"], "original_s1": result["original_s1_status"]}, ensure_ascii=False))
