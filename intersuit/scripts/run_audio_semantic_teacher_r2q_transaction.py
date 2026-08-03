#!/usr/bin/env python
"""冻结并一次性执行 CLAP 教师 R2-Q（T1/T2C）正式评估事务。

`develop` 使用全部 524 条 teacher-development train 与 84 条 teacher_confirm，
生成不可变 transaction lock。
`execute` 在 fail-closed 预检后只访问一次 expanded validation；开始访问后禁止重跑。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import wave
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import numpy as np
import torch
from transformers import ClapModel, ClapProcessor


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
T0_ROOT = INTERSUIT_ROOT / "harness/artifacts/audio_semantic_teacher_t0_v2"
T0_V1_ROOT = INTERSUIT_ROOT / "harness/artifacts/audio_semantic_teacher_t0"
R0_ROOT = INTERSUIT_ROOT / "harness/artifacts/audio_semantic_reconstruction_r0_contract"
OLD_R2_ROOT = INTERSUIT_ROOT / "harness/artifacts/audio_semantic_reconstruction_r2_targets"
OUTPUT_ROOT = INTERSUIT_ROOT / "harness/artifacts/r2_teacher_validation_transaction"
VIEW_ROOT = INTERSUIT_ROOT / "datasets/AUDIO_SEMANTIC_TEACHER_T0_V2"
DEV_VIEW_MANIFEST = VIEW_ROOT / "teacher_view_full_train_confirm_manifest.jsonl"
VALIDATION_VIEW_ROOT = VIEW_ROOT / "validation_48k_mono"
TRAIN_VALIDATION_MANIFEST = R0_ROOT / "expanded_train_validation_manifest.jsonl"
FULL_SPLIT_MANIFEST = R0_ROOT / "expanded_split_manifest.jsonl"
T0_LOCK = T0_ROOT / "teacher_qualification_lock.json"
ASSET_LOCK = T0_V1_ROOT / "teacher_asset_lock.json"
LEDGER = T0_ROOT / "candidate_selection_ledger.json"
OLD_R2_LOCK = OLD_R2_ROOT / "old_r2_baseline_lock.json"
TRANSACTION_LOCK = OUTPUT_ROOT / "transaction_lock.json"
EXECUTION_STATE = OUTPUT_ROOT / "execution_state.json"

SAMPLE_RATE = 48_000
MAX_SAMPLES = 480_000
BATCH_SIZE = 8
BOOTSTRAP_REPLICATES = 10_000
SEEDS = (17, 23, 47)
DETERMINISM_MAX_ABS_TOLERANCE = 1e-6
CONFIDENCE = {
    "high": {
        "prediction_margin_min": 0.05,
        "augmentation_stability_min": 0.90,
        "prompt_agreement_min": 1.0,
        "weight": 1.0,
    },
    "medium": {
        "prediction_margin_min": 0.02,
        "augmentation_stability_min": 0.75,
        "prompt_agreement_min": 2.0 / 3.0,
        "weight": 0.5,
    },
    "low": {"weight": 0.0, "action": "reject_teacher_audio_target"},
}
AUGMENTATIONS = {
    "gain": {"gain_db_range": [-2.0, 2.0]},
    "noise": {"snr_db_range": [30.0, 40.0]},
    "reverb": {
        "wet_mix_range": [0.05, 0.15],
        "decay_seconds_range": [0.10, 0.30],
    },
    "time_jitter": {"shift_seconds_range": [-0.10, 0.10]},
}
T1_GATES = {
    "rank_first_min": 0.65,
    "margin_ci_lower_strictly_positive": True,
    "old_r2_improvement_min": 0.10,
    "determinism_max_abs_tolerance": DETERMINISM_MAX_ABS_TOLERANCE,
    "all_outputs_finite": True,
}
T2C_GATES = {
    "augmentation_top1_consistency_min": 0.90,
    "usable_coverage_min": 0.70,
    "usable_subset_rank_first_min": 0.65,
    "usable_subset_margin_direction": "strictly_positive_mean",
    "per_class_usable_min": 3,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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


def normalize(values: torch.Tensor) -> torch.Tensor:
    return torch.nn.functional.normalize(values.float(), dim=-1, eps=1e-12)


def stable_rng(physical_media_id: str, seed: int, name: str) -> np.random.Generator:
    material = f"{physical_media_id}\0{seed}\0{name}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(material).digest()[:8], "little")
    return np.random.default_rng(value)


def load_waveform(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        if (
            handle.getframerate() != SAMPLE_RATE
            or handle.getnchannels() != 1
            or handle.getsampwidth() != 2
        ):
            raise ValueError(f"WAV contract 失败：{path}")
        raw = handle.readframes(handle.getnframes())
    waveform = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if waveform.size > MAX_SAMPLES:
        waveform = waveform[:MAX_SAMPLES]
    if not np.isfinite(waveform).all():
        raise ValueError(f"WAV 包含非有限值：{path}")
    return waveform


def augment_waveform(
    waveform: np.ndarray,
    physical_media_id: str,
    seed: int,
    name: str,
) -> tuple[np.ndarray, dict[str, float | int | str]]:
    rng = stable_rng(physical_media_id, seed, name)
    output = waveform.astype(np.float32, copy=True)
    parameters: dict[str, float | int | str] = {"name": name, "seed": seed}
    if name == "gain":
        gain_db = float(rng.uniform(*AUGMENTATIONS[name]["gain_db_range"]))
        output *= float(10.0 ** (gain_db / 20.0))
        parameters["gain_db"] = gain_db
    elif name == "noise":
        snr_db = float(rng.uniform(*AUGMENTATIONS[name]["snr_db_range"]))
        rms = float(np.sqrt(np.mean(np.square(output), dtype=np.float64)))
        noise_rms = max(rms, 1e-4) / float(10.0 ** (snr_db / 20.0))
        output += rng.normal(0.0, noise_rms, size=output.shape).astype(np.float32)
        parameters["snr_db"] = snr_db
    elif name == "reverb":
        wet_mix = float(rng.uniform(*AUGMENTATIONS[name]["wet_mix_range"]))
        decay_seconds = float(
            rng.uniform(*AUGMENTATIONS[name]["decay_seconds_range"])
        )
        # 三个稀疏反射保持 O(n)，避免对 10 秒波形执行超长直接卷积。
        wet = output.copy()
        tap_delays = (
            max(1, int(round(decay_seconds * SAMPLE_RATE / 4.0))),
            max(1, int(round(decay_seconds * SAMPLE_RATE / 2.0))),
            max(1, int(round(decay_seconds * SAMPLE_RATE))),
        )
        tap_gains = (0.45, 0.25, 0.12)
        for delay, gain in zip(tap_delays, tap_gains):
            if delay < output.size:
                wet[delay:] += gain * output[: output.size - delay]
        wet /= 1.0 + sum(tap_gains)
        output = (1.0 - wet_mix) * output + wet_mix * wet
        parameters["wet_mix"] = wet_mix
        parameters["decay_seconds"] = decay_seconds
    elif name == "time_jitter":
        shift_seconds = float(
            rng.uniform(*AUGMENTATIONS[name]["shift_seconds_range"])
        )
        shift_samples = int(round(shift_seconds * SAMPLE_RATE))
        shifted = np.zeros_like(output)
        if shift_samples >= 0:
            if shift_samples < output.size:
                shifted[shift_samples:] = output[: output.size - shift_samples]
        else:
            amount = -shift_samples
            if amount < output.size:
                shifted[: output.size - amount] = output[amount:]
        output = shifted
        parameters["shift_seconds"] = shift_seconds
        parameters["shift_samples"] = shift_samples
    else:
        raise ValueError(f"未知增强：{name}")
    output = np.clip(output, -1.0, 1.0).astype(np.float32)
    if not np.isfinite(output).all():
        raise ValueError(f"增强包含非有限值：{name}")
    return output, parameters


def load_model() -> tuple[ClapModel, ClapProcessor, torch.device, Path]:
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("必须通过 CUDA_VISIBLE_DEVICES 显式暴露一张 GPU")
    asset_lock = read_json(ASSET_LOCK)
    snapshot = Path(asset_lock["teacher"]["snapshot_path"])
    processor = ClapProcessor.from_pretrained(snapshot, local_files_only=True)
    model = (
        ClapModel.from_pretrained(
            snapshot, local_files_only=True, trust_remote_code=False
        )
        .eval()
        .requires_grad_(False)
        .to("cuda:0")
    )
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise ValueError("教师没有完全冻结")
    return model, processor, torch.device("cuda:0"), snapshot


def text_prototypes(
    model: ClapModel,
    processor: ClapProcessor,
    labels: list[str],
    device: torch.device,
    prompt_templates: tuple[str, ...],
) -> tuple[torch.Tensor, list[torch.Tensor]]:
    individual = []
    for template in prompt_templates:
        tokens = processor.tokenizer(
            text=[template.format(label=label) for label in labels],
            padding=True,
            return_tensors="pt",
        )
        tokens = {key: value.to(device) for key, value in tokens.items()}
        with torch.inference_mode():
            individual.append(normalize(model.get_text_features(**tokens)).cpu())
    ensemble = normalize(torch.stack(individual).mean(dim=0))
    return ensemble, individual


def audio_embeddings(
    model: ClapModel,
    processor: ClapProcessor,
    waveforms: list[np.ndarray],
    device: torch.device,
    progress_name: str,
    padding: str,
) -> torch.Tensor:
    outputs = []
    for start in range(0, len(waveforms), BATCH_SIZE):
        batch = waveforms[start : start + BATCH_SIZE]
        features = processor.feature_extractor(
            batch,
            sampling_rate=SAMPLE_RATE,
            truncation="rand_trunc",
            padding=padding,
            max_length=MAX_SAMPLES,
            return_tensors="pt",
        )
        if bool(features["is_longer"].any()):
            raise ValueError("外部裁剪后仍出现 is_longer=true")
        features = {key: value.to(device) for key, value in features.items()}
        with torch.inference_mode():
            embeddings = normalize(model.get_audio_features(**features))
        if not torch.isfinite(embeddings).all():
            raise ValueError(f"{progress_name} embedding 包含 NaN/Inf")
        outputs.append(embeddings.cpu())
        done = min(start + BATCH_SIZE, len(waveforms))
        if done % 64 == 0 or done == len(waveforms):
            print(f"{progress_name} {done}/{len(waveforms)}", flush=True)
    return torch.cat(outputs, dim=0)


def similarity_rows(
    rows: list[dict[str, Any]],
    embeddings: torch.Tensor,
    prototypes: torch.Tensor,
    labels: list[str],
) -> list[dict[str, Any]]:
    label_to_index = {label: index for index, label in enumerate(labels)}
    similarities = normalize(embeddings) @ normalize(prototypes).T
    output = []
    for index, row in enumerate(rows):
        scores = similarities[index]
        order = torch.argsort(scores, descending=True, stable=True)
        predicted_index = int(order[0])
        runner_up_index = int(order[1])
        true_index = label_to_index[row["canonical_label"]]
        wrong = scores.clone()
        wrong[true_index] = -torch.inf
        hard_index = int(torch.argmax(wrong))
        output.append(
            {
                "physical_media_id": row["physical_media_id"],
                "canonical_label": row["canonical_label"],
                "predicted_label": labels[predicted_index],
                "runner_up_label": labels[runner_up_index],
                "hard_negative_label": labels[hard_index],
                "positive_similarity": float(scores[true_index]),
                "hard_negative_similarity": float(scores[hard_index]),
                "true_margin": float(scores[true_index] - scores[hard_index]),
                "prediction_margin": float(
                    scores[predicted_index] - scores[runner_up_index]
                ),
                "correct": predicted_index == true_index,
                "similarities": [float(value) for value in scores],
            }
        )
    return output


def bootstrap_ci(values: list[float], seed: int) -> list[float]:
    array = np.asarray(values, dtype=np.float64)
    rng = np.random.default_rng(seed)
    indices = rng.integers(
        0, len(array), size=(BOOTSTRAP_REPLICATES, len(array)), endpoint=False
    )
    estimates = array[indices].mean(axis=1)
    return [
        float(np.quantile(estimates, 0.025)),
        float(np.quantile(estimates, 0.975)),
    ]


def bootstrap_indices_sha(count: int, seed: int) -> str:
    rng = np.random.default_rng(seed)
    indices = rng.integers(
        0, count, size=(BOOTSTRAP_REPLICATES, count), endpoint=False
    )
    return sha256_bytes(indices.tobytes())


def base_metrics(
    samples: list[dict[str, Any]], labels: list[str]
) -> dict[str, Any]:
    per_class_total: Counter[str] = Counter()
    per_class_correct: Counter[str] = Counter()
    confusion = {
        label: {predicted: 0 for predicted in labels} for label in labels
    }
    margins = []
    for sample in samples:
        label = sample["canonical_label"]
        predicted = sample["predicted_label"]
        per_class_total[label] += 1
        per_class_correct[label] += int(sample["correct"])
        confusion[label][predicted] += 1
        margins.append(float(sample["true_margin"]))
    missing_labels = [
        label for label in labels if per_class_total[label] == 0
    ]
    if missing_labels:
        raise ValueError(
            f"评估数据类别覆盖不完整，缺少：{missing_labels}"
        )
    recalls = {
        label: per_class_correct[label] / per_class_total[label] for label in labels
    }
    accuracy = sum(bool(sample["correct"]) for sample in samples) / len(samples)
    return {
        "query_count": len(samples),
        "label_count": len(labels),
        "accuracy": accuracy,
        "rank_first": sum(value > 0 for value in margins) / len(margins),
        "hard_cross_label_rank_first": sum(value > 0 for value in margins)
        / len(margins),
        "macro_recall_at_1": mean(recalls.values()),
        "balanced_accuracy": mean(recalls.values()),
        "margin_mean": mean(margins),
        "per_class_recall_at_1": recalls,
        "per_class_query_count": dict(sorted(per_class_total.items())),
        "confusion_matrix": confusion,
        "random_class_baseline": 1.0 / len(labels),
        "majority_class_baseline": max(per_class_total.values()) / len(samples),
    }


def t1_metrics(
    clean_samples: list[dict[str, Any]],
    labels: list[str],
    old_rank_first: float,
    determinism: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, bool]]:
    base = base_metrics(clean_samples, labels)
    margins = [float(sample["true_margin"]) for sample in clean_samples]
    seed_metrics = []
    for seed in SEEDS:
        seed_metrics.append(
            {
                **{key: base[key] for key in (
                    "accuracy",
                    "rank_first",
                    "hard_cross_label_rank_first",
                    "macro_recall_at_1",
                    "balanced_accuracy",
                    "margin_mean",
                )},
                "seed": seed,
                "margin_bootstrap_95_ci": bootstrap_ci(margins, seed),
            }
        )
    metrics = {
        **base,
        "seeds": seed_metrics,
        "seed_summary": {
            key: {
                "mean": mean(float(item[key]) for item in seed_metrics),
                "std": pstdev(float(item[key]) for item in seed_metrics),
                "worst_seed": min(float(item[key]) for item in seed_metrics),
            }
            for key in (
                "accuracy",
                "rank_first",
                "macro_recall_at_1",
                "balanced_accuracy",
                "margin_mean",
            )
        },
        "margin_ci_lower_worst_seed": min(
            item["margin_bootstrap_95_ci"][0] for item in seed_metrics
        ),
        "old_r2_rank_first": old_rank_first,
        "old_r2_absolute_improvement": base["rank_first"] - old_rank_first,
        "determinism": determinism,
        "all_outputs_finite": all(
            math.isfinite(float(value))
            for sample in clean_samples
            for value in sample["similarities"]
        ),
    }
    gates = {
        "rank_first": metrics["rank_first"] >= T1_GATES["rank_first_min"],
        "margin_ci": metrics["margin_ci_lower_worst_seed"] > 0,
        "old_r2_improvement": metrics["old_r2_absolute_improvement"]
        >= T1_GATES["old_r2_improvement_min"],
        "finite": metrics["all_outputs_finite"],
        "determinism": bool(determinism["pass"]),
    }
    return metrics, gates


def assign_confidence(
    prediction_margin: float,
    augmentation_stability: float,
    prompt_agreement: float,
) -> str:
    high = CONFIDENCE["high"]
    medium = CONFIDENCE["medium"]
    if (
        prediction_margin >= high["prediction_margin_min"]
        and augmentation_stability >= high["augmentation_stability_min"]
        and prompt_agreement >= high["prompt_agreement_min"]
    ):
        return "high"
    if (
        prediction_margin >= medium["prediction_margin_min"]
        and augmentation_stability >= medium["augmentation_stability_min"]
        and prompt_agreement >= medium["prompt_agreement_min"]
    ):
        return "medium"
    return "low"


def t2c_metrics(
    clean_samples: list[dict[str, Any]],
    augmented_samples: list[dict[str, Any]],
    prompt_samples: list[dict[str, Any]],
    labels: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, bool]]:
    augmented_by_id: dict[str, list[dict[str, Any]]] = {}
    for sample in augmented_samples:
        augmented_by_id.setdefault(sample["physical_media_id"], []).append(sample)
    prompt_by_id = {sample["physical_media_id"]: sample for sample in prompt_samples}
    confidence_rows = []
    comparison_matches = 0
    comparison_count = 0
    for clean in clean_samples:
        sample_augmented = augmented_by_id[clean["physical_media_id"]]
        stable = sum(
            item["predicted_label"] == clean["predicted_label"]
            for item in sample_augmented
        )
        comparison_matches += stable
        comparison_count += len(sample_augmented)
        augmentation_stability = stable / len(sample_augmented)
        prompt_agreement = float(
            prompt_by_id[clean["physical_media_id"]]["prompt_agreement"]
        )
        confidence = assign_confidence(
            float(clean["prediction_margin"]),
            augmentation_stability,
            prompt_agreement,
        )
        confidence_rows.append(
            {
                "physical_media_id": clean["physical_media_id"],
                "canonical_label": clean["canonical_label"],
                "predicted_label": clean["predicted_label"],
                "true_margin": clean["true_margin"],
                "prediction_margin": clean["prediction_margin"],
                "augmentation_stability": augmentation_stability,
                "prompt_agreement": prompt_agreement,
                "confidence": confidence,
                "usable": confidence in {"high", "medium"},
                "teacher_weight": CONFIDENCE[confidence]["weight"],
            }
        )
    usable = [row for row in confidence_rows if row["usable"]]
    per_class_usable = Counter(row["canonical_label"] for row in usable)
    usable_margins = [float(row["true_margin"]) for row in usable]
    metrics = {
        "augmentation_top1_consistency": comparison_matches / comparison_count,
        "augmentation_comparison_count": comparison_count,
        "usable_count": len(usable),
        "usable_coverage": len(usable) / len(confidence_rows),
        "usable_subset_rank_first": (
            sum(value > 0 for value in usable_margins) / len(usable_margins)
            if usable_margins
            else 0.0
        ),
        "usable_subset_margin_mean": (
            mean(usable_margins) if usable_margins else float("-inf")
        ),
        "per_class_usable_count": {
            label: int(per_class_usable[label]) for label in labels
        },
        "confidence_counts": dict(
            sorted(Counter(row["confidence"] for row in confidence_rows).items())
        ),
    }
    gates = {
        "augmentation_consistency": metrics["augmentation_top1_consistency"]
        >= T2C_GATES["augmentation_top1_consistency_min"],
        "usable_coverage": metrics["usable_coverage"]
        >= T2C_GATES["usable_coverage_min"],
        "usable_subset_rank_first": metrics["usable_subset_rank_first"]
        >= T2C_GATES["usable_subset_rank_first_min"],
        "usable_subset_margin": metrics["usable_subset_margin_mean"] > 0,
        "per_class_usable": min(metrics["per_class_usable_count"].values())
        >= T2C_GATES["per_class_usable_min"],
    }
    return metrics, confidence_rows, gates


def prompt_agreement_rows(
    rows: list[dict[str, Any]],
    embeddings: torch.Tensor,
    ensemble: torch.Tensor,
    individual: list[torch.Tensor],
    labels: list[str],
) -> list[dict[str, Any]]:
    ensemble_predictions = (normalize(embeddings) @ ensemble.T).argmax(dim=1)
    individual_predictions = [
        (normalize(embeddings) @ prototypes.T).argmax(dim=1)
        for prototypes in individual
    ]
    output = []
    for index, row in enumerate(rows):
        ensemble_index = int(ensemble_predictions[index])
        indices = [int(predictions[index]) for predictions in individual_predictions]
        output.append(
            {
                "physical_media_id": row["physical_media_id"],
                "ensemble_predicted_label": labels[ensemble_index],
                "individual_predicted_labels": [labels[value] for value in indices],
                "prompt_agreement": sum(
                    value == ensemble_index for value in indices
                )
                / len(indices),
            }
        )
    return output


def augmentation_result_rows(
    rows: list[dict[str, Any]],
    embeddings: torch.Tensor,
    prototypes: torch.Tensor,
    labels: list[str],
    name: str,
    seed: int,
    parameters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    samples = similarity_rows(rows, embeddings, prototypes, labels)
    return [
        {
            **sample,
            "augmentation": name,
            "seed": seed,
            "augmentation_parameters": parameters[index],
        }
        for index, sample in enumerate(samples)
    ]


def run_evaluation(
    rows: list[dict[str, Any]],
    waveforms: list[np.ndarray],
    model: ClapModel,
    processor: ClapProcessor,
    device: torch.device,
    labels: list[str],
    progress_prefix: str,
    prompt_templates: tuple[str, ...],
    padding: str,
) -> dict[str, Any]:
    ensemble, individual = text_prototypes(
        model, processor, labels, device, prompt_templates
    )
    clean_first = audio_embeddings(
        model,
        processor,
        waveforms,
        device,
        f"{progress_prefix} clean-1",
        padding,
    )
    clean_second = audio_embeddings(
        model,
        processor,
        waveforms,
        device,
        f"{progress_prefix} clean-2",
        padding,
    )
    max_abs = float(torch.max(torch.abs(clean_first - clean_second)))
    first_predictions = torch.argmax(clean_first @ ensemble.T, dim=1)
    second_predictions = torch.argmax(clean_second @ ensemble.T, dim=1)
    determinism = {
        "max_abs_embedding_difference": max_abs,
        "prediction_mismatch_count": int(
            torch.sum(first_predictions != second_predictions)
        ),
        "tolerance": DETERMINISM_MAX_ABS_TOLERANCE,
        "pass": max_abs <= DETERMINISM_MAX_ABS_TOLERANCE
        and bool(torch.equal(first_predictions, second_predictions)),
    }
    clean_samples = similarity_rows(rows, clean_first, ensemble, labels)
    prompt_samples = prompt_agreement_rows(
        rows, clean_first, ensemble, individual, labels
    )
    augmented_samples = []
    augmented_tensors = []
    augmented_index = []
    for seed in SEEDS:
        for name in AUGMENTATIONS:
            augmented_waveforms = []
            parameter_rows = []
            for row, waveform in zip(rows, waveforms):
                augmented, parameters = augment_waveform(
                    waveform, row["physical_media_id"], seed, name
                )
                augmented_waveforms.append(augmented)
                parameter_rows.append(parameters)
            embeddings = audio_embeddings(
                model,
                processor,
                augmented_waveforms,
                device,
                f"{progress_prefix} {name}/seed{seed}",
                padding,
            )
            augmented_samples.extend(
                augmentation_result_rows(
                    rows,
                    embeddings,
                    ensemble,
                    labels,
                    name,
                    seed,
                    parameter_rows,
                )
            )
            augmented_tensors.append(embeddings)
            augmented_index.append({"augmentation": name, "seed": seed})
    return {
        "clean_embeddings": clean_first,
        "augmented_embeddings": torch.stack(augmented_tensors),
        "augmented_embedding_index": augmented_index,
        "clean_samples": clean_samples,
        "augmented_samples": augmented_samples,
        "prompt_samples": prompt_samples,
        "determinism": determinism,
    }


def validate_t0_contract() -> dict[str, Any]:
    required = (
        T0_LOCK,
        ASSET_LOCK,
        LEDGER,
        OLD_R2_LOCK,
        DEV_VIEW_MANIFEST,
        TRAIN_VALIDATION_MANIFEST,
        FULL_SPLIT_MANIFEST,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"缺少前置资产：{missing}")
    t0 = read_json(T0_LOCK)
    if t0["status"] != "T0V2_PASS":
        raise ValueError("T0v2 未通过")
    if t0["validation_accessed"] or t0["test_locked_accessed"]:
        raise ValueError("T0 lock 已污染")
    if t0["preprocessing"]["padding"] not in {"pad", "repeatpad"}:
        raise ValueError("冻结 padding 不在允许集合")
    prompt_templates = tuple(t0["prompts"]["templates"])
    if not prompt_templates or any(
        "{label}" not in template for template in prompt_templates
    ):
        raise ValueError("冻结 prompts 为空或未使用 canonical label")
    selection = t0.get("selection_contract", {})
    if (
        selection.get("count") != 524
        or selection.get("label_count") != 28
        or selection.get("subsampled") is not False
        or selection.get("all_manifest_rows_used") is not True
    ):
        raise ValueError("T0v2 没有使用完整 524 条 train")
    if t0["runtime"]["conda_env"] != "M4":
        raise ValueError("教师 lock 不是 M4 环境")
    return t0


def append_ledger_event(event: dict[str, Any]) -> str:
    ledger = read_json(LEDGER)
    ledger.setdefault("events", [])
    existing = [
        item for item in ledger["events"] if item["event_id"] == event["event_id"]
    ]
    if existing and existing != [event]:
        raise ValueError("candidate ledger event 冲突")
    if not existing:
        ledger["events"].append(event)
        write_json(LEDGER, ledger)
    return sha256(LEDGER)


def develop() -> dict[str, Any]:
    if TRANSACTION_LOCK.exists() or EXECUTION_STATE.exists():
        raise FileExistsError("R2-Q transaction 已冻结或已开始，禁止重新 develop")
    t0 = validate_t0_contract()
    rows = read_jsonl(DEV_VIEW_MANIFEST)
    if len(rows) != 608:
        raise ValueError(f"开发视图数量异常：{len(rows)}")
    if any(row["validation_accessed"] or row["test_locked_accessed"] for row in rows):
        raise ValueError("开发视图污染")
    waveforms = [load_waveform(Path(row["teacher_audio_path"])) for row in rows]
    labels = sorted({row["canonical_label"] for row in rows})
    if len(labels) != 28:
        raise ValueError("canonical label 必须为 28 类")
    model, processor, device, _ = load_model()
    result = run_evaluation(
        rows,
        waveforms,
        model,
        processor,
        device,
        labels,
        "develop",
        tuple(t0["prompts"]["templates"]),
        t0["preprocessing"]["padding"],
    )
    development_indices = [
        index
        for index, row in enumerate(rows)
        if row["teacher_view_role"] == "teacher_development_full_train"
    ]
    confirm_indices = [
        index
        for index, row in enumerate(rows)
        if row["teacher_view_role"] == "teacher_confirm"
    ]
    if len(development_indices) != 524 or len(confirm_indices) != 84:
        raise ValueError(
            "develop 必须使用完整 524 train + 84 confirm："
            f"{len(development_indices)} + {len(confirm_indices)}"
        )
    development_labels = {
        rows[index]["canonical_label"] for index in development_indices
    }
    if len(development_labels) != 28:
        raise ValueError(
            f"完整 teacher-development train 只覆盖 "
            f"{len(development_labels)}/28 类"
        )

    def subset_samples(values: list[dict[str, Any]], indices: list[int], width: int):
        if len(values) == len(rows):
            return [values[index] for index in indices]
        output = []
        for start in range(0, len(values), len(rows)):
            output.extend(values[start + index] for index in indices)
        expected = width * len(indices)
        if len(output) != expected:
            raise ValueError("开发结果切分异常")
        return output

    development_metrics = {}
    development_confidence = {}
    for name, indices in (
        ("teacher_development_full_train", development_indices),
        ("teacher_confirm", confirm_indices),
    ):
        clean = subset_samples(result["clean_samples"], indices, 1)
        augmented = subset_samples(
            result["augmented_samples"], indices, len(SEEDS) * len(AUGMENTATIONS)
        )
        prompt = subset_samples(result["prompt_samples"], indices, 1)
        base = base_metrics(clean, labels)
        t2_metrics, confidence_rows, t2_gates = t2c_metrics(
            clean, augmented, prompt, labels
        )
        development_metrics[name] = {
            "clean": base,
            "t2c": t2_metrics,
            "t2c_diagnostic_gates": t2_gates,
        }
        development_confidence[name] = confidence_rows
    write_json(OUTPUT_ROOT / "development_metrics.json", development_metrics)
    write_json(
        OUTPUT_ROOT / "development_config.json",
        {
            "selection_data": "all 524 teacher_development_full_train",
            "selection_count": len(development_indices),
            "selection_label_count": len(
                {
                    rows[index]["canonical_label"]
                    for index in development_indices
                }
            ),
            "selection_subsampled": False,
            "confirmation_data": "teacher_confirm",
            "confidence_policy": CONFIDENCE,
            "augmentations": AUGMENTATIONS,
            "seeds": list(SEEDS),
            "threshold_rule": "fixed before validation; not optimized on validation",
            "validation_accessed": False,
            "test_locked_accessed": False,
        },
    )
    write_jsonl(
        OUTPUT_ROOT / "development_confidence.jsonl",
        [
            {"evaluation_role": split, **row}
            for split, values in development_confidence.items()
            for row in values
        ],
    )
    event = {
        "event_id": "r2q_transaction_freeze_v2_full_train",
        "attempt_id": (
            "laion/clap-htsat-unfused@"
            "8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a"
        ),
        "selection_data_accessed": ["teacher_development_full_train:524"],
        "selection_subsampled": False,
        "confirmation_data_accessed": ["teacher_confirm"],
        "validation_accessed": False,
        "test_locked_accessed": False,
        "confidence_policy": CONFIDENCE,
        "augmentations": AUGMENTATIONS,
        "t1_gates": T1_GATES,
        "t2c_gates": T2C_GATES,
        "seeds": list(SEEDS),
    }
    ledger_sha = append_ledger_event(event)
    old_lock = read_json(OLD_R2_LOCK)
    r0_manifest_lock = read_json(R0_ROOT / "manifest_lock.json")
    transaction_lock = {
        "schema_version": 1,
        "stage": "R2-Q",
        "status": "FROZEN_NOT_EXECUTED",
        "created_at": utc_now(),
        "single_use": True,
        "teacher_qualification_lock_sha256": sha256(T0_LOCK),
        "teacher_asset_lock_sha256": sha256(ASSET_LOCK),
        "old_r2_baseline_lock_sha256": sha256(OLD_R2_LOCK),
        "candidate_selection_ledger_prevalidation_sha256": ledger_sha,
        "evaluator": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256(Path(__file__).resolve()),
            "labels": old_lock["evaluator"]["label_order"],
            "prediction": old_lock["evaluator"]["prediction"],
            "hard_negative": old_lock["evaluator"]["hard_negative"],
            "rank_first": old_lock["evaluator"]["rank_first"],
            "similarity": old_lock["evaluator"]["similarity"],
            "normalization": old_lock["evaluator"]["audio_normalization"],
        },
        "validation": {
            "source_train_validation_manifest_path": str(
                TRAIN_VALIDATION_MANIFEST.resolve()
            ),
            "source_train_validation_manifest_sha256": r0_manifest_lock["outputs"][
                "expanded_train_validation_manifest.jsonl"
            ],
            "full_split_manifest_sha256_reference_only": r0_manifest_lock["outputs"][
                "expanded_split_manifest.jsonl"
            ],
            "expected_count": old_lock["validation"]["count"],
            "expected_ordered_physical_ids_sha256": old_lock["validation"][
                "ordered_physical_ids_sha256"
            ],
            "test_locked_accessed": False,
        },
        "preprocessing": {
            **t0["preprocessing"],
            "decode_command": [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                "<video>",
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-sample_fmt",
                "s16",
                "<wav>",
            ],
        },
        "prompts": t0["prompts"],
        "augmentations": AUGMENTATIONS,
        "confidence_policy": CONFIDENCE,
        "t1_gates": T1_GATES,
        "t2c_gates": T2C_GATES,
        "bootstrap": {
            "algorithm": "numpy.random.default_rng(seed).integers",
            "replicates": BOOTSTRAP_REPLICATES,
            "seeds": list(SEEDS),
            "indices_sha256_by_seed": {
                str(seed): bootstrap_indices_sha(
                    old_lock["validation"]["count"], seed
                )
                for seed in SEEDS
            },
        },
        "sample_order": "source manifest order filtered to diagnostic_split=validation",
        "runtime": {
            "conda_env": "M4",
            "device": "one explicit CUDA device",
            "batch_size": BATCH_SIZE,
            "inference_dtype": "float32",
        },
        "output_schema": {
            "clean_clip_results": "jsonl with full 28-way similarities",
            "augmented_clip_results": "jsonl with augmentation parameters",
            "prompt_agreement_results": "jsonl",
            "raw_embeddings_manifest": "json",
            "transaction_outputs_sha256": "json",
            "decision": "json",
        },
        "validation_accessed": False,
        "test_locked_accessed": False,
    }
    write_json(TRANSACTION_LOCK, transaction_lock)
    return {
        "status": "FROZEN_NOT_EXECUTED",
        "transaction_lock_sha256": sha256(TRANSACTION_LOCK),
        "full_train_t2c_diagnostic": development_metrics[
            "teacher_development_full_train"
        ]["t2c"],
        "teacher_confirm_t2c_diagnostic": development_metrics[
            "teacher_confirm"
        ]["t2c"],
    }


def ordered_ids_sha(rows: list[dict[str, Any]]) -> str:
    value = "\n".join(row["physical_media_id"] for row in rows) + "\n"
    return sha256_bytes(value.encode("utf-8"))


def preflight_execute() -> tuple[dict[str, Any], bool]:
    if not TRANSACTION_LOCK.is_file():
        raise FileNotFoundError("transaction_lock.json 不存在")
    if EXECUTION_STATE.exists():
        state = read_json(EXECUTION_STATE)
        if state["status"] == "COMPLETED":
            return state, True
        raise RuntimeError("正式 validation 事务已经开始且未完成，禁止重跑")
    lock = read_json(TRANSACTION_LOCK)
    if lock["status"] != "FROZEN_NOT_EXECUTED":
        raise ValueError("transaction lock 状态异常")
    if sha256(Path(lock["evaluator"]["path"])) != lock["evaluator"]["sha256"]:
        raise ValueError("evaluator 代码 SHA 漂移")
    checks = {
        T0_LOCK: lock["teacher_qualification_lock_sha256"],
        ASSET_LOCK: lock["teacher_asset_lock_sha256"],
        OLD_R2_LOCK: lock["old_r2_baseline_lock_sha256"],
        LEDGER: lock["candidate_selection_ledger_prevalidation_sha256"],
    }
    for path, expected in checks.items():
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"前置资产 SHA 漂移：{path} {actual} != {expected}")
    if shutil_free_bytes(REPO_ROOT) < 5 * 1024**3:
        raise OSError("磁盘可用空间不足 5 GiB")
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("必须显式暴露一张 GPU")
    return lock, False


def consume_validation_manifest(lock: dict[str, Any]) -> list[dict[str, Any]]:
    actual_manifest_sha = sha256(TRAIN_VALIDATION_MANIFEST)
    expected_manifest_sha = lock["validation"][
        "source_train_validation_manifest_sha256"
    ]
    if actual_manifest_sha != expected_manifest_sha:
        raise ValueError(
            "validation manifest SHA 漂移："
            f"{actual_manifest_sha} != {expected_manifest_sha}"
        )
    rows = [
        row
        for row in read_jsonl(TRAIN_VALIDATION_MANIFEST)
        if row["diagnostic_split"] == "validation"
    ]
    if len(rows) != lock["validation"]["expected_count"]:
        raise ValueError(f"validation 数量异常：{len(rows)}")
    if ordered_ids_sha(rows) != lock["validation"][
        "expected_ordered_physical_ids_sha256"
    ]:
        raise ValueError("validation physical ID 顺序 SHA 不匹配")
    if any(row["diagnostic_split"] == "test_locked" for row in rows):
        raise ValueError("validation 输入包含 test_locked")
    labels = sorted({row["canonical_label"] for row in rows})
    if labels != lock["evaluator"]["labels"]:
        raise ValueError("validation label order 不匹配")
    missing = [
        row["video_path"]
        for row in rows
        if not Path(row["video_path"]).is_file()
    ]
    if missing:
        raise FileNotFoundError(f"validation 原始视频缺失：{missing[:3]}")
    return rows


def shutil_free_bytes(path: Path) -> int:
    stat = os.statvfs(path)
    return stat.f_bavail * stat.f_frsize


def decode_validation_views(
    rows: list[dict[str, Any]],
) -> tuple[list[np.ndarray], list[dict[str, Any]]]:
    VALIDATION_VIEW_ROOT.mkdir(parents=True, exist_ok=True)
    waveforms = []
    view_rows = []
    for index, row in enumerate(rows):
        youtube_id = row["youtube_id"]
        output = VALIDATION_VIEW_ROOT / f"{youtube_id}.wav"
        command = [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            row["video_path"],
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-sample_fmt",
            "s16",
            str(output),
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"validation 解码失败 {row['physical_media_id']}: "
                f"{completed.stderr.strip()}"
            )
        waveform = load_waveform(output)
        waveforms.append(waveform)
        with wave.open(str(output), "rb") as handle:
            decoded_sample_count = handle.getnframes()
        view_rows.append(
            {
                "physical_media_id": row["physical_media_id"],
                "canonical_label": row["canonical_label"],
                "video_path": row["video_path"],
                "video_sha256": sha256(Path(row["video_path"])),
                "teacher_audio_path": str(output.resolve()),
                "teacher_audio_sha256": sha256(output),
                "sample_rate": SAMPLE_RATE,
                "sample_count_before_external_crop": decoded_sample_count,
                "sample_count_used": len(waveform),
            }
        )
        done = index + 1
        if done % 32 == 0 or done == len(rows):
            print(f"validation 解码 {done}/{len(rows)}", flush=True)
    return waveforms, view_rows


def output_hashes(paths: list[Path]) -> dict[str, str]:
    return {path.name: sha256(path) for path in paths}


def execute() -> dict[str, Any]:
    lock_or_state, already_completed = preflight_execute()
    if already_completed:
        return {
            "status": "ALREADY_COMPLETED",
            "decision_path": lock_or_state["decision_path"],
            "decision_sha256": lock_or_state["decision_sha256"],
        }
    lock = lock_or_state
    write_json(
        EXECUTION_STATE,
        {
            "status": "STARTED_VALIDATION_CONSUMED",
            "started_at": utc_now(),
            "transaction_lock_sha256": sha256(TRANSACTION_LOCK),
            "validation_accessed": True,
            "test_locked_accessed": False,
        },
    )
    rows = consume_validation_manifest(lock)
    waveforms, view_rows = decode_validation_views(rows)
    write_jsonl(OUTPUT_ROOT / "validation_teacher_view_manifest.jsonl", view_rows)
    labels = lock["evaluator"]["labels"]
    model, processor, device, _ = load_model()
    result = run_evaluation(
        rows,
        waveforms,
        model,
        processor,
        device,
        labels,
        "validation",
        tuple(lock["prompts"]["templates"]),
        lock["preprocessing"]["padding"],
    )
    clean_path = OUTPUT_ROOT / "clean_clip_results.jsonl"
    augmented_path = OUTPUT_ROOT / "augmented_clip_results.jsonl"
    prompt_path = OUTPUT_ROOT / "prompt_agreement_results.jsonl"
    write_jsonl(clean_path, result["clean_samples"])
    write_jsonl(augmented_path, result["augmented_samples"])
    write_jsonl(prompt_path, result["prompt_samples"])
    clean_tensor_path = OUTPUT_ROOT / "clean_audio_embeddings.pt"
    augmented_tensor_path = OUTPUT_ROOT / "augmented_audio_embeddings.pt"
    torch.save(result["clean_embeddings"], clean_tensor_path)
    torch.save(result["augmented_embeddings"], augmented_tensor_path)
    raw_manifest = {
        "clean": {
            "path": str(clean_tensor_path.resolve()),
            "sha256": sha256(clean_tensor_path),
            "shape": list(result["clean_embeddings"].shape),
            "dtype": str(result["clean_embeddings"].dtype),
        },
        "augmented": {
            "path": str(augmented_tensor_path.resolve()),
            "sha256": sha256(augmented_tensor_path),
            "shape": list(result["augmented_embeddings"].shape),
            "dtype": str(result["augmented_embeddings"].dtype),
            "index": result["augmented_embedding_index"],
        },
    }
    raw_manifest_path = OUTPUT_ROOT / "raw_embeddings_manifest.json"
    write_json(raw_manifest_path, raw_manifest)
    old_lock = read_json(OLD_R2_LOCK)
    old_rank = float(old_lock["historical_metrics"]["rank_first"]["mean"])
    t1_values, t1_gates = t1_metrics(
        result["clean_samples"],
        labels,
        old_rank,
        result["determinism"],
    )
    t1_pass = all(t1_gates.values())
    t2_values, confidence_rows, t2_gates = t2c_metrics(
        result["clean_samples"],
        result["augmented_samples"],
        result["prompt_samples"],
        labels,
    )
    t2_pass = t1_pass and all(t2_gates.values())
    confidence_path = OUTPUT_ROOT / "confidence_results.jsonl"
    write_jsonl(confidence_path, confidence_rows)
    metrics = {
        "stage": "R2-Q",
        "t1": t1_values,
        "t2c": t2_values,
        "validation_accessed": True,
        "test_locked_accessed": False,
    }
    metrics_path = OUTPUT_ROOT / "metrics.json"
    write_json(metrics_path, metrics)
    if not t1_pass:
        teacher_status = "rejected"
        authorized_next_stage = None
        blocking = [name for name, passed in t1_gates.items() if not passed]
    elif not t2_pass:
        teacher_status = "clip_semantic_only_not_authorized"
        authorized_next_stage = None
        blocking = [name for name, passed in t2_gates.items() if not passed]
    else:
        teacher_status = "clip_only_qualified"
        authorized_next_stage = "R2-T2W capability lock preparation"
        blocking = []
    decision = {
        "stage": "R2-Q",
        "status": "PASS" if t1_pass and t2_pass else "FAIL",
        "teacher_status": teacher_status,
        "t1_gate_results": t1_gates,
        "t2c_gate_results": t2_gates if t1_pass else {
            "archived_not_authorizing": True,
            **t2_gates,
        },
        "t2w_status": "NOT_QUALIFIED",
        "capabilities": {
            "clip_teacher": bool(t1_pass and t2_pass),
            "window_teacher": False,
            "allowed_student_targets": (
                ["text_prototype", "teacher_clip_relational", "mil"]
                if t1_pass and t2_pass
                else []
            ),
        },
        "authorized_next_stage": authorized_next_stage,
        "blocking_reasons": blocking,
        "validation_accessed": True,
        "test_locked_accessed": False,
    }
    decision_path = OUTPUT_ROOT / "decision.json"
    write_json(decision_path, decision)
    review_path = OUTPUT_ROOT / "review.md"
    review_path.write_text(
        "\n".join(
            [
                "# 冻结音频语义教师 R2-Q 正式评估",
                "",
                f"- 总结论：`{decision['status']}`",
                f"- teacher_status：`{teacher_status}`",
                f"- T1 rank-first：{t1_values['rank_first']:.6f}",
                f"- T1 margin CI 下界（worst seed）："
                f"{t1_values['margin_ci_lower_worst_seed']:.6f}",
                f"- 相对旧 R2 提升："
                f"{t1_values['old_r2_absolute_improvement']:+.6f}",
                f"- T2C 增强一致率："
                f"{t2_values['augmentation_top1_consistency']:.6f}",
                f"- T2C 可用覆盖率：{t2_values['usable_coverage']:.6f}",
                f"- validation 已消费：true",
                f"- test_locked 已访问：false",
                f"- 阻塞项：{', '.join(blocking) if blocking else '无'}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    hashed_paths = [
        clean_path,
        augmented_path,
        prompt_path,
        confidence_path,
        raw_manifest_path,
        metrics_path,
        decision_path,
        review_path,
        OUTPUT_ROOT / "validation_teacher_view_manifest.jsonl",
    ]
    output_hash_path = OUTPUT_ROOT / "transaction_outputs_sha256.json"
    write_json(output_hash_path, output_hashes(hashed_paths))
    write_json(
        EXECUTION_STATE,
        {
            "status": "COMPLETED",
            "started_at": read_json(EXECUTION_STATE)["started_at"],
            "completed_at": utc_now(),
            "transaction_lock_sha256": sha256(TRANSACTION_LOCK),
            "decision_path": str(decision_path.resolve()),
            "decision_sha256": sha256(decision_path),
            "transaction_outputs_sha256": sha256(output_hash_path),
            "validation_accessed": True,
            "test_locked_accessed": False,
        },
    )
    return {
        "status": decision["status"],
        "teacher_status": teacher_status,
        "t1": {
            "rank_first": t1_values["rank_first"],
            "margin_ci_lower": t1_values["margin_ci_lower_worst_seed"],
            "old_r2_improvement": t1_values["old_r2_absolute_improvement"],
            "gates": t1_gates,
        },
        "t2c": {
            "augmentation_consistency": t2_values[
                "augmentation_top1_consistency"
            ],
            "usable_coverage": t2_values["usable_coverage"],
            "gates": t2_gates,
        },
        "decision_sha256": sha256(decision_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("develop", "execute"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = develop() if args.phase == "develop" else execute()
    except Exception as exc:
        print(f"R2-Q {args.phase} 失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
