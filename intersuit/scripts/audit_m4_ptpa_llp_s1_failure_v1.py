#!/usr/bin/env python3
"""独立复核 LLP S1 failure analysis 的原始计数、Speech 占比和 prototype geometry。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
import torch


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_RESULT = INTERSUIT_ROOT / "datasets/LLP_PILOT_MEDIA_V2/s1_failure_analysis_v1"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/m4_ptpa_llp_s1_failure_analysis_v1.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def resolve_repo(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def normalize(value: torch.Tensor) -> torch.Tensor:
    return torch.nn.functional.normalize(value.float(), dim=-1, eps=1e-12)


def assert_close(observed: float, expected: float, label: str) -> None:
    if not math.isclose(observed, expected, rel_tol=0.0, abs_tol=1e-10):
        raise ValueError(f"独立复核不一致 {label}: {observed} != {expected}")


def prototype_geometry(
    labels: list[str], selected: dict[str, list[str]], clip_means: dict[str, torch.Tensor],
    weak_labels: dict[str, set[str]],
) -> dict[str, Any]:
    prototypes = torch.stack(
        [normalize(torch.stack([clip_means[media_id] for media_id in selected[label]]).mean(0)) for label in labels]
    )
    similarities = prototypes @ prototypes.T
    cooccurring_pairs = {
        tuple(sorted((left, right)))
        for values in weak_labels.values()
        for left in values for right in values if left < right
    }
    all_values, cooccurring, other = [], [], []
    for left_index, left in enumerate(labels):
        for right_index in range(left_index + 1, len(labels)):
            right = labels[right_index]
            value = float(similarities[left_index, right_index].item())
            all_values.append(value)
            (cooccurring if tuple(sorted((left, right))) in cooccurring_pairs else other).append(value)
    return {
        "pair_count": len(all_values),
        "cosine_mean": mean(all_values),
        "cosine_p95": float(np.quantile(all_values, 0.95)),
        "cosine_max": max(all_values),
        "cooccurring_pair_count": len(cooccurring),
        "cooccurring_cosine_mean": mean(cooccurring),
        "noncooccurring_pair_count": len(other),
        "noncooccurring_cosine_mean": mean(other),
        "cooccurring_minus_noncooccurring_mean": mean(cooccurring) - mean(other),
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    analysis_path = args.result_root / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    if analysis["status"] != "PASS_DIAGNOSTIC_COMPLETE" or analysis["original_s1_status"] != "FAIL_UNCHANGED":
        raise ValueError("failure analysis 状态漂移")
    feature_path = resolve_repo(contract["inputs"]["feature_manifest"]["path"])
    per_query_path = resolve_repo(contract["inputs"]["s1_per_query"]["path"])
    selection_path = resolve_repo(contract["inputs"]["prototype_selection"]["path"])
    for name, path in (
        ("feature_manifest", feature_path), ("s1_per_query", per_query_path),
        ("prototype_selection", selection_path),
    ):
        if sha256(path) != contract["inputs"][name]["sha256"]:
            raise ValueError(f"审计输入 SHA 漂移：{name}")
    features = read_jsonl(feature_path)
    train = [row for row in features if row["official_split"] == "train"]
    validation = [row for row in features if row["official_split"] == "validation"]
    if {row["official_split"] for row in features} != {"train", "validation"}:
        raise ValueError("发现非 train/validation 资产")
    weak = {row["physical_media_id"]: set(row["event_labels"]) for row in train}
    cardinalities = [len(values) for values in weak.values()]
    pollution = analysis["prototype_pollution"]
    if dict(sorted(Counter(cardinalities).items())) != {
        int(key): value for key, value in pollution["train_label_cardinality_distribution"].items()
    }:
        raise ValueError("train label cardinality distribution 不一致")
    assert_close(mean(cardinalities), pollution["train_label_cardinality_mean"], "cardinality mean")
    assert_close(mean(value > 1 for value in cardinalities), pollution["train_multilabel_video_fraction"], "multilabel fraction")
    assert_close(mean("Speech" in values for values in weak.values()), analysis["speech"]["train_video_prevalence"], "speech train")

    diagnostic = contract["frozen_result"]["diagnostic_representation"]
    per_query = [
        row for row in read_jsonl(per_query_path)
        if row["representation"] == diagnostic and int(row["seed"]) == 17
    ]
    if len(per_query) != 99 or len({row["physical_media_id"] for row in per_query}) != 50:
        raise ValueError("diagnostic query 计数不一致")
    speech_queries = sum(row["target_label"] == "Speech" for row in per_query)
    if speech_queries != analysis["speech"]["validation_query_count"]:
        raise ValueError("Speech query count 不一致")
    assert_close(speech_queries / len(per_query), analysis["speech"]["validation_query_prevalence"], "speech query")

    selections = json.loads(selection_path.read_text(encoding="utf-8"))[diagnostic]
    labels = sorted(analysis["prototype_pollution"]["per_label"])
    source_entries = [
        media_id
        for seed in contract["frozen_result"]["fixed_seeds"]
        for label in labels if label != "Speech"
        for media_id in selections[str(seed)][label]
    ]
    if len(source_entries) != pollution["non_speech_prototype_source_entries"]:
        raise ValueError("prototype source entry count 不一致")
    assert_close(
        mean("Speech" in weak[media_id] for media_id in source_entries),
        pollution["non_speech_prototype_sources_with_speech_fraction"],
        "Speech prototype contamination",
    )

    clip_means = {}
    for row in train:
        payload = torch.load(row["feature_path"], map_location="cpu", weights_only=True)
        clip_means[row["physical_media_id"]] = normalize(payload["audio_embedding"].float().mean(0))
    geometries = []
    for seed in contract["frozen_result"]["fixed_seeds"]:
        observed = prototype_geometry(labels, selections[str(seed)], clip_means, weak)
        expected = next(
            item for item in pollution["prototype_geometry_per_seed"] if item["seed"] == seed
        )
        for key in (
            "cosine_mean", "cosine_p95", "cosine_max",
            "cooccurring_cosine_mean", "noncooccurring_cosine_mean",
            "cooccurring_minus_noncooccurring_mean",
        ):
            assert_close(observed[key], expected[key], f"geometry {seed}/{key}")
        geometries.append(observed)

    counterfactual = analysis["single_label_counterfactual"]
    original = counterfactual["conditions"]["all_weak_sources"]
    pure = counterfactual["conditions"]["single_label_sources"]
    assert_close(
        pure["macro_recall_at_1"] - original["macro_recall_at_1"],
        counterfactual["delta_single_label_minus_all_weak"]["macro_recall_at_1"],
        "counterfactual recall delta",
    )
    assert_close(
        pure["video_equal_margin_mean"] - original["video_equal_margin_mean"],
        counterfactual["delta_single_label_minus_all_weak"]["video_equal_margin_mean"],
        "counterfactual margin delta",
    )
    if any(
        item["permutation_p_two_sided"] is not None
        and not 0 < item["permutation_p_two_sided"] <= 1
        for item in analysis["imbalance"]["correlations"].values()
    ):
        raise ValueError("permutation p 非法")

    audit = {
        "status": "PASS",
        "analysis_status_reproduced": analysis["status"],
        "original_s1_status_reproduced": analysis["original_s1_status"],
        "train_rows": len(train),
        "validation_rows": len(validation),
        "diagnostic_queries": len(per_query),
        "speech_queries": speech_queries,
        "prototype_source_entries": len(source_entries),
        "geometry_recomputed_seeds": len(geometries),
        "formal_test_rows": 0,
        "training_performed": False,
        "analysis_sha256": sha256(analysis_path),
        "contract_sha256": sha256(args.contract),
    }
    audit_path = args.result_root / "independent_audit.json"
    report_path = args.result_root / "INDEPENDENT_AUDIT_ZH.md"
    write_json(audit_path, audit)
    report_path.write_text(
        "\n".join(
            [
                "# LLP S1 失败分析独立审计", "",
                "- 状态：`PASS`",
                "- 原 S1：`FAIL_UNCHANGED`。",
                f"- train/validation/query：{len(train)}/{len(validation)}/{len(per_query)}。",
                f"- Speech query：{speech_queries}。",
                f"- prototype source entries：{len(source_entries)}。",
                f"- 独立重算 geometry seeds：{len(geometries)}。",
                "- formal test 访问0，训练0。", "",
            ]
        ),
        encoding="utf-8",
    )
    lock_path = args.result_root / "manifest_lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["outputs"][audit_path.name] = sha256(audit_path)
    lock["outputs"][report_path.name] = sha256(report_path)
    write_json(lock_path, lock)
    return audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    return parser.parse_args()


if __name__ == "__main__":
    result = run(parse_args())
    print(json.dumps({"status": result["status"], "original_s1": result["original_s1_status_reproduced"]}, ensure_ascii=False))
