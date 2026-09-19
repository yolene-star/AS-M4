#!/usr/bin/env python3
"""审计 LLP modality manifest 的 yes/no 预测并计算条件选择性指标。"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "intersuit/harness/artifacts/llp_modality_evaluator_v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize_yes_no(value: str) -> str | None:
    text = value.strip().lower()
    if text.startswith("yes"):
        return "yes"
    if text.startswith("no"):
        return "no"
    return None


def make_contract_mock(manifest: list[dict[str, Any]]) -> list[dict[str, str]]:
    predictions = []
    for row in manifest:
        target = row["target_answer"]
        if row["query_type"] == "visual_presence" or row["condition"] == "correct_audio":
            prediction = target
        else:
            prediction = "no"
        predictions.append({"eval_id": row["eval_id"], "prediction": prediction})
    return predictions


def evaluate(manifest: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    manifest_by_id = {row["eval_id"]: row for row in manifest}
    if len(manifest_by_id) != len(manifest):
        raise ValueError("manifest eval_id 重复")
    prediction_by_id: dict[str, str | None] = {}
    for row in predictions:
        eval_id = row["eval_id"]
        if eval_id in prediction_by_id:
            raise ValueError(f"预测 eval_id 重复：{eval_id}")
        prediction_by_id[eval_id] = normalize_yes_no(str(row.get("prediction", "")))
    if set(prediction_by_id) != set(manifest_by_id):
        missing = set(manifest_by_id) - set(prediction_by_id)
        extra = set(prediction_by_id) - set(manifest_by_id)
        raise ValueError(f"预测键集合不一致：missing={len(missing)} extra={len(extra)}")

    grouped_accuracy: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    by_base_query_condition: dict[tuple[str, str, str], str | None] = {}
    invalid_count = 0
    for eval_id, row in manifest_by_id.items():
        prediction = prediction_by_id[eval_id]
        invalid_count += int(prediction is None)
        correct = float(prediction == row["target_answer"])
        grouped_accuracy[(row["query_type"], row["condition"], row["modality_role"])].append(correct)
        by_base_query_condition[(row["base_id"], row["query_type"], row["condition"])] = prediction

    accuracy = {
        f"{query}:{condition}:{role}": mean(values)
        for (query, condition, role), values in sorted(grouped_accuracy.items())
    }
    positive_roles = {"audio_only", "audio_visual"}
    positive_base_ids = {
        row["base_id"]
        for row in manifest
        if row["query_type"] == "audio_presence" and row["modality_role"] in positive_roles
    }

    def condition_accuracy(query: str, condition: str, base_ids: set[str]) -> float:
        values = []
        for base_id in sorted(base_ids):
            prediction = by_base_query_condition[(base_id, query, condition)]
            target = next(
                row["target_answer"]
                for row in manifest
                if row["base_id"] == base_id and row["query_type"] == query
            )
            values.append(float(prediction == target))
        return mean(values)

    correct_audio = condition_accuracy("audio_presence", "correct_audio", positive_base_ids)
    mismatch_audio = condition_accuracy("audio_presence", "mismatch_audio", positive_base_ids)
    silence = condition_accuracy("audio_presence", "silence", positive_base_ids)
    video_only = condition_accuracy("audio_presence", "video_only", positive_base_ids)
    all_base_ids = {row["base_id"] for row in manifest}
    visual_flips = []
    silence_matches = []
    for base_id in sorted(all_base_ids):
        visual_video = by_base_query_condition[(base_id, "visual_presence", "video_only")]
        visual_flips.extend(
            [
                float(by_base_query_condition[(base_id, "visual_presence", "correct_audio")] != visual_video),
                float(by_base_query_condition[(base_id, "visual_presence", "mismatch_audio")] != visual_video),
            ]
        )
        silence_matches.append(
            float(
                by_base_query_condition[(base_id, "audio_presence", "silence")]
                == by_base_query_condition[(base_id, "audio_presence", "video_only")]
            )
        )
    return {
        "prediction_count": len(predictions),
        "invalid_prediction_count": invalid_count,
        "accuracy_by_query_condition_role": accuracy,
        "audio_positive_correct_accuracy": correct_audio,
        "correct_mismatch_margin": correct_audio - mismatch_audio,
        "correct_silence_margin": correct_audio - silence,
        "correct_video_only_margin": correct_audio - video_only,
        "visual_only_flip_rate": mean(visual_flips),
        "silence_video_only_consistency": mean(silence_matches),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    manifest = read_jsonl(args.manifest)
    if args.mock_contract:
        predictions = make_contract_mock(manifest)
        prediction_path = args.output_root / "mock_contract_predictions.jsonl"
        args.output_root.mkdir(parents=True, exist_ok=True)
        prediction_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in predictions),
            encoding="utf-8",
        )
        evidence_type = "synthetic_contract_mock_not_model_evidence"
    else:
        if args.predictions is None:
            raise ValueError("必须提供 --predictions 或 --mock-contract")
        predictions = read_jsonl(args.predictions)
        evidence_type = "model_predictions"
    metrics = evaluate(manifest, predictions)
    status = (
        "PASS_LLP_MODALITY_CPU_MOCK_ONLY"
        if args.mock_contract and metrics["invalid_prediction_count"] == 0
        else "PASS_LLP_MODALITY_PREDICTION_AUDIT"
        if metrics["invalid_prediction_count"] == 0
        else "FAIL_INVALID_PREDICTIONS"
    )
    result = {"status": status, "evidence_type": evidence_type, **metrics}
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_ROOT / "manifest.jsonl")
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--mock-contract", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, sort_keys=True))
