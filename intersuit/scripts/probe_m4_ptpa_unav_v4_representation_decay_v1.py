#!/usr/bin/env python3
"""定位 correct/mismatch 选择性在 v4→adapter→fusion 链路中的衰减。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import torch
from safetensors import safe_open
import torch.nn.functional as F

from intersuit.harness.runners.run_predictions_from_plan import _load_scene_audio_tensor
from intersuit.model.scene_audio_encoder.scene_audio_encoder import FrozenBEATsV4SceneAudioEncoder


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def vector_metrics(correct: torch.Tensor, mismatch: torch.Tensor) -> dict:
    correct = correct.detach().float().cpu()
    mismatch = mismatch.detach().float().cpu()
    correct_mean = correct.mean(dim=0)
    mismatch_mean = mismatch.mean(dim=0)
    cosine = float(F.cosine_similarity(correct_mean, mismatch_mean, dim=0))
    difference = float(torch.linalg.vector_norm(correct_mean - mismatch_mean))
    scale = float(
        0.5
        * (
            torch.linalg.vector_norm(correct_mean)
            + torch.linalg.vector_norm(mismatch_mean)
        )
    )
    return {
        "mean_cosine": cosine,
        "mean_difference_norm": difference,
        "relative_mean_difference": difference / max(scale, 1e-12),
        "correct_mean_norm": float(torch.linalg.vector_norm(correct_mean)),
        "mismatch_mean_norm": float(torch.linalg.vector_norm(mismatch_mean)),
    }


def audio_path(row: dict) -> str:
    return str(row["generation_debug"]["tokens"]["audio_input"]["source_path"])


def load_windows(path: str) -> torch.Tensor:
    windows, _timestamps, _metadata = _load_scene_audio_tensor(
        {
            "scene_audio_path": path,
            "scene_audio_sample_rate": 16000,
            "scene_audio_window_sec": 1.0,
            "scene_audio_hop_sec": 0.5,
        }
    )
    return windows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--correct", type=Path, required=True)
    parser.add_argument("--mismatch", type=Path, required=True)
    parser.add_argument("--beats-checkpoint", type=Path, required=True)
    parser.add_argument("--beats-code-root", type=Path, required=True)
    parser.add_argument("--v4-checkpoint", type=Path, required=True)
    parser.add_argument("--v4-sha256", required=True)
    parser.add_argument("--audio-overlay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args()

    correct_rows = {row["sample_id"]: row for row in read_jsonl(args.correct)}
    mismatch_rows = {row["sample_id"]: row for row in read_jsonl(args.mismatch)}
    selected_ids = [
        sample_id
        for sample_id, row in correct_rows.items()
        if row.get("task_type") == "audio"
    ]
    if len(selected_ids) != 3 or set(correct_rows) != set(mismatch_rows):
        raise ValueError("探针要求已锁定10条结果中恰好3条audio任务且三条件ID对齐")

    encoder = FrozenBEATsV4SceneAudioEncoder(
        hidden_size=3584,
        checkpoint_path=str(args.beats_checkpoint),
        code_root=str(args.beats_code_root),
        semantic_projector_path=str(args.v4_checkpoint),
        semantic_projector_sha256=args.v4_sha256,
        encode_chunk_size=32,
    ).to(args.device)
    fusion_weight: torch.Tensor
    with safe_open(args.audio_overlay, framework="pt", device="cpu") as handle:
        encoder.m4_adapter.weight.data.copy_(
            handle.get_tensor("model.scene_audio_encoder.audio_projector.weight")
        )
        encoder.m4_adapter.bias.data.copy_(
            handle.get_tensor("model.scene_audio_encoder.audio_projector.bias")
        )
        fusion_weight = handle.get_tensor(
            "model.streaming_av_module.fusion.audio_projector.weight"
        ).float().to(args.device)
    encoder.eval()
    encoder.requires_grad_(False)

    pair_results = []
    adapter_weight_signal_norms: list[float] = []
    adapter_output_norms: list[float] = []
    with torch.inference_mode():
        for sample_id in selected_ids:
            correct_windows = load_windows(audio_path(correct_rows[sample_id])).to(args.device)
            mismatch_windows = load_windows(audio_path(mismatch_rows[sample_id])).to(args.device)
            correct_raw = encoder.encode_raw(correct_windows.unsqueeze(0)).features[0]
            mismatch_raw = encoder.encode_raw(mismatch_windows.unsqueeze(0)).features[0]
            correct_v4 = F.normalize(
                correct_raw.float() + 0.1 * encoder.semantic_projector(correct_raw.float()),
                dim=-1,
            )
            mismatch_v4 = F.normalize(
                mismatch_raw.float() + 0.1 * encoder.semantic_projector(mismatch_raw.float()),
                dim=-1,
            )
            correct_linear = F.linear(
                correct_v4,
                encoder.m4_adapter.weight,
                None,
            )
            mismatch_linear = F.linear(
                mismatch_v4,
                encoder.m4_adapter.weight,
                None,
            )
            correct_adapter = correct_linear + encoder.m4_adapter.bias
            mismatch_adapter = mismatch_linear + encoder.m4_adapter.bias
            correct_fusion = F.linear(correct_adapter, fusion_weight)
            mismatch_fusion = F.linear(mismatch_adapter, fusion_weight)
            adapter_weight_signal_norms.extend(
                [
                    float(correct_linear.norm(dim=-1).mean()),
                    float(mismatch_linear.norm(dim=-1).mean()),
                ]
            )
            adapter_output_norms.extend(
                [
                    float(correct_adapter.norm(dim=-1).mean()),
                    float(mismatch_adapter.norm(dim=-1).mean()),
                ]
            )
            pair_results.append(
                {
                    "sample_id": sample_id,
                    "correct_audio_path": audio_path(correct_rows[sample_id]),
                    "mismatch_audio_path": audio_path(mismatch_rows[sample_id]),
                    "correct_windows": int(correct_raw.shape[0]),
                    "mismatch_windows": int(mismatch_raw.shape[0]),
                    "stages": {
                        "raw_beats": vector_metrics(correct_raw, mismatch_raw),
                        "v4_semantic": vector_metrics(correct_v4, mismatch_v4),
                        "m4_adapter": vector_metrics(correct_adapter, mismatch_adapter),
                        "fusion_delta_pre_gate": vector_metrics(
                            correct_fusion, mismatch_fusion
                        ),
                    },
                }
            )

    stage_names = ("raw_beats", "v4_semantic", "m4_adapter", "fusion_delta_pre_gate")
    aggregate = {
        name: {
            "mean_pair_cosine": sum(
                item["stages"][name]["mean_cosine"] for item in pair_results
            )
            / len(pair_results),
            "mean_relative_difference": sum(
                item["stages"][name]["relative_mean_difference"]
                for item in pair_results
            )
            / len(pair_results),
        }
        for name in stage_names
    }
    bias_norm = float(encoder.m4_adapter.bias.detach().float().norm())
    weight_signal_norm = sum(adapter_weight_signal_norms) / len(adapter_weight_signal_norms)
    output_norm = sum(adapter_output_norms) / len(adapter_output_norms)
    result = {
        "status": "PASS_V4_REPRESENTATION_DECAY_PROBE",
        "pairs": pair_results,
        "aggregate": aggregate,
        "adapter_scale": {
            "bias_norm": bias_norm,
            "mean_weight_only_window_norm": weight_signal_norm,
            "mean_output_window_norm": output_norm,
            "bias_to_weight_signal_ratio": bias_norm / max(weight_signal_norm, 1e-12),
        },
        "source_sha256": {
            "correct_predictions": sha256_file(args.correct),
            "mismatch_predictions": sha256_file(args.mismatch),
            "beats_checkpoint": sha256_file(args.beats_checkpoint),
            "v4_checkpoint": sha256_file(args.v4_checkpoint),
            "audio_overlay": sha256_file(args.audio_overlay),
        },
        "physical_gpu": 2,
        "test_accessed": False,
        "training_performed": False,
        "m4_generation_performed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
