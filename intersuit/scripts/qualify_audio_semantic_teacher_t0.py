#!/usr/bin/env python
"""在 pilot300 选择并在 teacher_confirm 复核 CLAP 的 T0 预处理与 prompt。"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import wave
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
import torch
from transformers import ClapModel, ClapProcessor


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
ARTIFACT_ROOT = INTERSUIT_ROOT / "harness/artifacts/audio_semantic_teacher_t0"
VIEW_ROOT = INTERSUIT_ROOT / "datasets/AUDIO_SEMANTIC_TEACHER_T0"
VIEW_MANIFEST = VIEW_ROOT / "teacher_view_pilot_confirm_manifest.jsonl"
VIEW_LOCK = VIEW_ROOT / "teacher_view_pilot_confirm_lock.json"
ASSET_LOCK = ARTIFACT_ROOT / "teacher_asset_lock.json"
LEDGER = ARTIFACT_ROOT / "candidate_selection_ledger.json"
R0_ROOT = INTERSUIT_ROOT / "harness/artifacts/audio_semantic_reconstruction_r0_contract"
R2_ROOT = INTERSUIT_ROOT / "harness/artifacts/audio_semantic_reconstruction_r2_targets"
SNAPSHOT = Path(json.loads(ASSET_LOCK.read_text())["teacher"]["snapshot_path"])

SEED = 20260727
SAMPLE_RATE = 48_000
MAX_SAMPLES = 480_000
BATCH_SIZE = 8
PADDINGS = ("pad", "repeatpad")
PROMPT_SETS = {
    "label": ("{label}",),
    "sound_of": ("the sound of {label}",),
    "audio_recording": ("an audio recording of {label}",),
    "ensemble3": (
        "{label}",
        "the sound of {label}",
        "an audio recording of {label}",
    ),
}
PROMPT_TIE_PRIORITY = {
    "ensemble3": 3,
    "sound_of": 2,
    "audio_recording": 1,
    "label": 0,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            )


def normalize(values: torch.Tensor) -> torch.Tensor:
    return torch.nn.functional.normalize(values.float(), dim=-1, eps=1e-12)


def load_waveform(path: str) -> np.ndarray:
    with wave.open(path, "rb") as handle:
        if (
            handle.getframerate() != SAMPLE_RATE
            or handle.getnchannels() != 1
            or handle.getsampwidth() != 2
        ):
            raise ValueError(f"WAV contract 失败：{path}")
        raw = handle.readframes(handle.getnframes())
    waveform = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return waveform[:MAX_SAMPLES]


def text_prototypes(
    model: ClapModel,
    processor: ClapProcessor,
    labels: list[str],
    device: torch.device,
) -> dict[str, torch.Tensor]:
    outputs = {}
    for name, templates in PROMPT_SETS.items():
        template_embeddings = []
        for template in templates:
            texts = [template.format(label=label) for label in labels]
            tokens = processor.tokenizer(
                text=texts, padding=True, return_tensors="pt"
            )
            tokens = {key: value.to(device) for key, value in tokens.items()}
            with torch.inference_mode():
                embedding = normalize(model.get_text_features(**tokens))
            template_embeddings.append(embedding.cpu())
        outputs[name] = normalize(torch.stack(template_embeddings).mean(dim=0))
    return outputs


def audio_embeddings(
    model: ClapModel,
    processor: ClapProcessor,
    rows: list[dict[str, Any]],
    waveforms: list[np.ndarray],
    padding: str,
    device: torch.device,
) -> torch.Tensor:
    outputs = []
    for start in range(0, len(rows), BATCH_SIZE):
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
            embedding = normalize(model.get_audio_features(**features))
        outputs.append(embedding.cpu())
        done = min(start + BATCH_SIZE, len(rows))
        if done % 64 == 0 or done == len(rows):
            print(f"{padding} 音频编码 {done}/{len(rows)}", flush=True)
    return torch.cat(outputs, dim=0)


def evaluate(
    rows: list[dict[str, Any]],
    audio: torch.Tensor,
    prototypes: torch.Tensor,
    labels: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    label_to_index = {label: index for index, label in enumerate(labels)}
    similarities = normalize(audio) @ normalize(prototypes).T
    predicted_indices = similarities.argmax(dim=1)
    class_correct: Counter[str] = Counter()
    class_total: Counter[str] = Counter()
    per_sample = []
    for index, row in enumerate(rows):
        label = row["canonical_label"]
        true_index = label_to_index[label]
        positive = float(similarities[index, true_index])
        wrong = similarities[index].clone()
        wrong[true_index] = -torch.inf
        hard_index = int(wrong.argmax())
        predicted = labels[int(predicted_indices[index])]
        margin = positive - float(wrong[hard_index])
        correct = predicted == label
        class_total[label] += 1
        class_correct[label] += int(correct)
        per_sample.append(
            {
                "physical_media_id": row["physical_media_id"],
                "teacher_view_role": row["teacher_view_role"],
                "canonical_label": label,
                "predicted_label": predicted,
                "hard_negative_label": labels[hard_index],
                "positive_similarity": positive,
                "hard_negative_similarity": float(wrong[hard_index]),
                "margin": margin,
                "correct": correct,
            }
        )
    recalls = {
        label: class_correct[label] / class_total[label]
        for label in sorted(class_total)
    }
    return {
        "count": len(rows),
        "label_count": len(recalls),
        "accuracy": sum(item["correct"] for item in per_sample) / len(rows),
        "macro_recall_at_1": mean(recalls.values()),
        "rank_first": sum(item["margin"] > 0 for item in per_sample) / len(rows),
        "margin_mean": mean(item["margin"] for item in per_sample),
        "per_class_recall_at_1": recalls,
        "per_class_count": dict(sorted(class_total.items())),
    }, per_sample


def golden_features(
    processor: ClapProcessor,
    row: dict[str, Any],
    waveform: np.ndarray,
    padding: str,
) -> dict[str, Any]:
    np.random.seed(SEED)
    first = processor.feature_extractor(
        waveform,
        sampling_rate=SAMPLE_RATE,
        truncation="rand_trunc",
        padding=padding,
        max_length=MAX_SAMPLES,
        return_tensors="pt",
    )
    np.random.seed(SEED)
    second = processor.feature_extractor(
        waveform,
        sampling_rate=SAMPLE_RATE,
        truncation="rand_trunc",
        padding=padding,
        max_length=MAX_SAMPLES,
        return_tensors="pt",
    )
    first_hash = sha256_bytes(first["input_features"].numpy().tobytes())
    second_hash = sha256_bytes(second["input_features"].numpy().tobytes())
    if first_hash != second_hash:
        raise ValueError("固定 waveform 的 input_features hash 不一致")
    return {
        "physical_media_id": row["physical_media_id"],
        "teacher_audio_sha256": row["teacher_audio_sha256"],
        "input_features_sha256": first_hash,
        "shape": list(first["input_features"].shape),
        "dtype": str(first["input_features"].dtype),
        "is_longer": bool(first["is_longer"].any()),
    }


def run() -> dict[str, Any]:
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("必须通过 CUDA_VISIBLE_DEVICES 显式暴露一张 GPU")
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    device = torch.device("cuda:0")

    rows = read_jsonl(VIEW_MANIFEST)
    if len(rows) != 384:
        raise ValueError(f"teacher view 数量异常：{len(rows)}")
    if any(row["validation_accessed"] or row["test_locked_accessed"] for row in rows):
        raise ValueError("T0 view manifest 污染")
    pilot = [row for row in rows if row["teacher_view_role"].startswith("pilot300_")]
    confirm = [row for row in rows if row["teacher_view_role"] == "teacher_confirm"]
    if len(pilot) != 300 or len(confirm) != 84:
        raise ValueError("pilot/confirm 数量异常")
    labels = sorted({row["canonical_label"] for row in rows})
    if len(labels) != 28:
        raise ValueError("canonical label 必须为 28 类")

    waveforms = [load_waveform(row["teacher_audio_path"]) for row in rows]
    processor = ClapProcessor.from_pretrained(SNAPSHOT, local_files_only=True)
    model = (
        ClapModel.from_pretrained(
            SNAPSHOT, local_files_only=True, trust_remote_code=False
        )
        .eval()
        .requires_grad_(False)
        .to(device)
    )
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise ValueError("教师没有完全冻结")

    prototypes = text_prototypes(model, processor, labels, device)
    metrics: dict[str, Any] = {}
    per_sample_output = []
    configurations = []
    for padding in PADDINGS:
        audio = audio_embeddings(
            model, processor, rows, waveforms, padding, device
        )
        for prompt_name, text_vectors in prototypes.items():
            config_name = f"{padding}__{prompt_name}"
            metrics[config_name] = {}
            for split_name, split_rows in (("pilot300", pilot), ("teacher_confirm", confirm)):
                indices = [rows.index(row) for row in split_rows]
                result, samples = evaluate(
                    split_rows,
                    audio[indices],
                    text_vectors,
                    labels,
                )
                metrics[config_name][split_name] = result
                per_sample_output.extend(
                    {
                        "config": config_name,
                        "evaluation_role": split_name,
                        **sample,
                    }
                    for sample in samples
                )
            pilot_metrics = metrics[config_name]["pilot300"]
            configurations.append(
                (
                    (
                        pilot_metrics["macro_recall_at_1"],
                        pilot_metrics["rank_first"],
                        pilot_metrics["margin_mean"],
                        int(padding == "pad"),
                        PROMPT_TIE_PRIORITY[prompt_name],
                    ),
                    padding,
                    prompt_name,
                )
            )
    _, chosen_padding, chosen_prompt = max(configurations, key=lambda item: item[0])
    chosen_name = f"{chosen_padding}__{chosen_prompt}"
    chosen_templates = list(PROMPT_SETS[chosen_prompt])
    golden_row_index = next(
        index
        for index, row in enumerate(rows)
        if row["teacher_view_role"] == "teacher_confirm"
    )
    golden = golden_features(
        processor,
        rows[golden_row_index],
        waveforms[golden_row_index],
        chosen_padding,
    )

    config = {
        "stage": "R2-T0",
        "selection_role": "pilot300 only",
        "confirmation_role": "teacher_confirm only",
        "selection_rule": (
            "maximize pilot macro Recall@1, then rank-first, margin, "
            "pad preference, fixed prompt priority"
        ),
        "paddings": list(PADDINGS),
        "prompt_sets": {key: list(value) for key, value in PROMPT_SETS.items()},
        "chosen_config": chosen_name,
        "seed": SEED,
        "batch_size": BATCH_SIZE,
        "external_clip_rule": "take samples [0,480000); shorter clips remain short before padding",
        "validation_accessed": False,
        "test_locked_accessed": False,
    }
    write_json(ARTIFACT_ROOT / "config.json", config)
    write_json(ARTIFACT_ROOT / "metrics.json", metrics)
    write_jsonl(ARTIFACT_ROOT / "per_sample.jsonl", per_sample_output)

    ledger = read_json(LEDGER)
    event = {
        "event_id": f"t0_preprocessing_selection_{SEED}",
        "attempt_id": (
            "laion/clap-htsat-unfused@"
            "8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a"
        ),
        "selection_data_accessed": ["pilot300"],
        "confirmation_data_accessed": ["teacher_confirm"],
        "validation_accessed": False,
        "test_locked_accessed": False,
        "candidate_configs": sorted(metrics),
        "chosen_config": chosen_name,
    }
    ledger.setdefault("events", [])
    existing = [item for item in ledger["events"] if item["event_id"] == event["event_id"]]
    if existing and existing != [event]:
        raise ValueError("candidate ledger event 冲突")
    if not existing:
        ledger["events"].append(event)
    write_json(LEDGER, ledger)
    ledger_sha = sha256(LEDGER)

    asset_lock = read_json(ASSET_LOCK)
    view_lock = read_json(VIEW_LOCK)
    baseline_lock = R2_ROOT / "old_r2_baseline_lock.json"
    confirm_manifest = (
        INTERSUIT_ROOT
        / "datasets/AVE_HF_EXPANDED/teacher_confirm_manifest.jsonl"
    )
    student_manifest = (
        INTERSUIT_ROOT
        / "datasets/AVE_HF_EXPANDED/"
        "teacher_confirm_excluded_from_student_train.jsonl"
    )
    label_mapping = R0_ROOT / "label_mapping.json"
    np_state_hash = sha256_bytes(repr(np.random.get_state()).encode())
    torch_state_hash = sha256_bytes(torch.random.get_rng_state().numpy().tobytes())
    qualification_lock = {
        "schema_version": 1,
        "status": "T0_PASS",
        "teacher": {
            "model_id": asset_lock["teacher"]["model_id"],
            "official_source": asset_lock["teacher"]["official_source"],
            "revision": asset_lock["teacher"]["revision"],
            "architecture": asset_lock["teacher"]["architecture"],
            "license": asset_lock["teacher"]["license"],
            "checkpoint_files": sorted(asset_lock["teacher"]["files"]),
            "checkpoint_sha256": {
                name: item["sha256"]
                for name, item in asset_lock["teacher"]["files"].items()
            },
            "model_code_sha": asset_lock["transformers_clap_code"]["combined_sha256"],
            "config_sha256": asset_lock["teacher"]["files"]["config.json"]["sha256"],
            "tokenizer_sha256": asset_lock["teacher"]["files"]["tokenizer.json"]["sha256"],
            "feature_extractor_sha256": asset_lock["teacher"]["files"]["preprocessor_config.json"]["sha256"],
        },
        "preprocessing": {
            "teacher_sample_rate": SAMPLE_RATE,
            "student_sample_rate": 16_000,
            "clip_interval_rule": "samples[0:480000]",
            "max_length_seconds": 10.0,
            "max_length_samples": MAX_SAMPLES,
            "truncation": "rand_trunc_after_external_crop",
            "padding": chosen_padding,
            "disable_random_mel_crop": True,
            "longer_flag_policy": "must_be_false_after_external_crop",
            "window_sec": 1.0,
            "hop_sec": 1.0,
            "tail_policy": "right_zero_pad_with_valid_sample_count",
            "pooling": "L2-normalized equal-weight prompt ensemble",
            "normalization": "L2 eps=1e-12",
            "golden_waveform_sha256": golden["teacher_audio_sha256"],
            "golden_input_features_sha256": golden["input_features_sha256"],
        },
        "embedding": {"audio_dim": 512, "text_dim": 512},
        "prompts": {
            "name": chosen_prompt,
            "templates": chosen_templates,
            "synonym_table_sha256": sha256(label_mapping),
            "description_table_sha256": "not_used_in_t0",
            "ensemble_rule": "normalize each template embedding, equal mean, renormalize",
        },
        "runtime": {
            "conda_env": "M4",
            "inference_dtype": "float32",
            "device": "physical GPU 1 via CUDA_VISIBLE_DEVICES=1; logical cuda:0",
            "seeds": [SEED],
            "numpy_rng_state_sha256": np_state_hash,
            "torch_rng_state_sha256": torch_state_hash,
            "transformers_version": asset_lock["runtime"]["transformers"],
            "torch_version": asset_lock["runtime"]["torch"],
            "ffmpeg_version": view_lock["decode_contract"]["ffmpeg_version"],
        },
        "data": {
            "manifest_sha256": sha256(VIEW_MANIFEST),
            "split_lock_sha256": sha256(
                INTERSUIT_ROOT
                / "datasets/AVE_HF_EXPANDED/teacher_confirm_split_lock.json"
            ),
            "teacher_view_manifest_sha256": sha256(VIEW_MANIFEST),
            "teacher_confirm_manifest_path": str(confirm_manifest),
            "teacher_confirm_manifest_sha256": sha256(confirm_manifest),
            "student_train_exclusion_manifest_path": str(student_manifest),
            "student_train_exclusion_manifest_sha256": sha256(student_manifest),
            "old_r2_baseline_lock_sha256": sha256(baseline_lock),
            "candidate_selection_ledger_prevalidation_sha256": ledger_sha,
        },
        "capabilities": {
            "clip_teacher": False,
            "window_teacher": False,
            "allowed_student_targets": [],
        },
        "golden": golden,
        "selection_metrics": metrics[chosen_name],
        "validation_accessed": False,
        "test_locked_accessed": False,
        "implementation_sha256": sha256(Path(__file__).resolve()),
    }
    write_json(ARTIFACT_ROOT / "teacher_qualification_lock.json", qualification_lock)
    decision = {
        "stage": "R2-T0",
        "status": "PASS",
        "chosen_config": chosen_name,
        "teacher_qualification_lock_sha256": sha256(
            ARTIFACT_ROOT / "teacher_qualification_lock.json"
        ),
        "authorized_next_stage": "R2-Q/T1/T2C transaction preparation",
        "validation_accessed": False,
        "test_locked_accessed": False,
    }
    write_json(ARTIFACT_ROOT / "decision.json", decision)
    review = (
        "# 音频语义教师 T0 资格准备复核\n\n"
        f"- 结论：PASS\n"
        f"- 冻结配置：`{chosen_name}`\n"
        f"- pilot rank-first：{metrics[chosen_name]['pilot300']['rank_first']:.6f}\n"
        f"- teacher_confirm rank-first：{metrics[chosen_name]['teacher_confirm']['rank_first']:.6f}\n"
        "- validation/test：未访问\n"
        "- 下一步：只授权准备唯一一次 R2-Q validation transaction。\n"
    )
    (ARTIFACT_ROOT / "review.md").write_text(review, encoding="utf-8")
    return {
        "status": "PASS",
        "chosen_config": chosen_name,
        "pilot_metrics": metrics[chosen_name]["pilot300"],
        "teacher_confirm_metrics": metrics[chosen_name]["teacher_confirm"],
        "teacher_qualification_lock_sha256": decision[
            "teacher_qualification_lock_sha256"
        ],
    }


def main() -> int:
    try:
        result = run()
    except Exception as exc:
        print(f"T0 教师资格准备失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
