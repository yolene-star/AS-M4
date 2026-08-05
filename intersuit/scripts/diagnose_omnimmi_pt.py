#!/usr/bin/env python
"""采集 OmniMMI PT 的连续分数，供阈值与特殊 token 离线审计。"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
from decord import VideoReader, cpu

from intersuit.constants import IMAGE_TOKEN_INDEX
from intersuit.mm_utils import tokenizer_image_token
from intersuit.model.builder import load_pretrained_model


DEFAULT_TOKEN_IDS = (151643, 151644, 151645)


def build_pt_query(sample: dict[str, Any]) -> str:
    query = ""
    for conversation in sample["conversation"][-2:]:
        for role, text in conversation.items():
            query += f"{role}\n{text}\n"
    return query


def build_prompt(query: str) -> str:
    return (
        "<|im_start|>system\nYou are a helpful assistant.<|im_end|>\n"
        f"<|im_start|>user\n<image>\n{query}<|im_end|>\n"
        "<|im_start|>assistant\n"
    )


def select_frame_indices(
    total_frames: int,
    average_fps: float,
    policy: str,
    max_frames: int,
) -> list[int]:
    if total_frames <= 0:
        raise ValueError("视频必须至少包含一帧")
    if max_frames <= 0:
        raise ValueError("max_frames 必须为正整数")
    if policy == "fixed":
        count = min(max_frames, total_frames)
    elif policy in {"fps1", "fps1_max"}:
        if not math.isfinite(average_fps) or average_fps <= 0:
            raise ValueError("1 FPS 采样要求视频 FPS 为正数")
        count = min(math.ceil(total_frames / average_fps), total_frames)
        if policy == "fps1_max":
            count = min(count, max_frames)
    else:
        raise ValueError(f"未知采帧策略: {policy}")
    return np.linspace(0, total_frames - 1, count, dtype=int).tolist()


def score_logits(
    logits: torch.Tensor,
    temperature: float,
    beta: float,
    token_ids: tuple[int, ...] = DEFAULT_TOKEN_IDS,
) -> dict[str, Any]:
    if logits.ndim != 1:
        raise ValueError("logits 必须是一维 vocabulary 向量")
    if temperature <= 0 or beta < 0:
        raise ValueError("temperature 必须为正数且 beta 不得为负数")
    probabilities = torch.softmax(logits.float() / temperature, dim=-1)
    entropy = -(probabilities * torch.log(probabilities + 1e-5)).sum()
    reciprocal_perplexity = torch.exp(-entropy)
    threshold = beta * reciprocal_perplexity
    token_scores = {}
    for token_id in token_ids:
        probability = probabilities[token_id]
        token_scores[str(token_id)] = {
            "logit": float(logits[token_id]),
            "probability": float(probability),
            "silent": bool(probability > threshold),
        }
    return {
        "temperature": temperature,
        "beta": beta,
        "entropy": float(entropy),
        "reciprocal_perplexity": float(reciprocal_perplexity),
        "threshold": float(threshold),
        "tokens": token_scores,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_completed(path: Path) -> set[int]:
    if not path.is_file():
        return set()
    completed = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                completed.add(int(json.loads(line)["sample_index"]))
    return completed


@torch.inference_mode()
def run(args: argparse.Namespace) -> None:
    dataset_path = Path(args.questions_file).resolve()
    video_root = Path(args.video_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    logits_dir = output_dir / "logits"
    scores_path = output_dir / "scores.jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)
    logits_dir.mkdir(parents=True, exist_ok=True)

    samples = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(samples, list) or not samples:
        raise ValueError("questions_file 必须是非空 JSON list")
    selected = list(enumerate(samples))
    if args.limit is not None:
        selected = selected[: args.limit]

    completed = load_completed(scores_path) if args.resume else set()
    tokenizer, model, image_processor, _ = load_pretrained_model(
        str(Path(args.model_path).resolve()),
        None,
        "llava_qwen",
        device_map=args.device,
        attn_implementation=args.attn_implementation,
    )
    model = model.to(args.device).eval()

    token_metadata = {
        str(token_id): {
            "token": tokenizer.convert_ids_to_tokens(token_id),
            "decoded": tokenizer.decode([token_id]),
        }
        for token_id in DEFAULT_TOKEN_IDS
    }
    write_json(
        output_dir / "run_config.json",
        {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "model_path": str(Path(args.model_path).resolve()),
            "questions_file": str(dataset_path),
            "video_dir": str(video_root),
            "device": args.device,
            "attn_implementation": args.attn_implementation,
            "frame_policy": args.frame_policy,
            "max_frames": args.max_frames,
            "temperature": args.temperature,
            "betas": args.betas,
            "token_metadata": token_metadata,
            "sample_count_requested": len(selected),
            "stores_full_last_token_logits_fp16": True,
        },
    )

    with scores_path.open("a" if args.resume else "w", encoding="utf-8") as handle:
        for sample_index, sample in selected:
            if sample_index in completed:
                continue
            started = time.time()
            video_path = video_root / "videos" / sample["video"]
            reader = VideoReader(str(video_path), ctx=cpu(0))
            total_frames = len(reader)
            average_fps = float(reader.get_avg_fps())
            frame_indices = select_frame_indices(
                total_frames, average_fps, args.frame_policy, args.max_frames
            )
            frames = reader.get_batch(frame_indices).asnumpy()
            video_tensor = image_processor.preprocess(frames, return_tensors="pt")[
                "pixel_values"
            ].half().to(model.device)

            query = build_pt_query(sample)
            prompt = build_prompt(query)
            input_ids = tokenizer_image_token(
                prompt, tokenizer, IMAGE_TOKEN_INDEX, return_tensors="pt"
            ).unsqueeze(0).to(model.device)
            outputs = model(
                input_ids,
                images=[video_tensor],
                modalities=["video"],
                return_dict=True,
            )
            final_logits = outputs.logits[0, -1].detach().float().cpu()
            if not torch.isfinite(final_logits).all():
                raise ValueError(f"样本 {sample_index} 的 final logits 含非有限值")

            logits_path = logits_dir / f"{sample_index:04d}.pt"
            torch.save(
                {
                    "sample_index": sample_index,
                    "video": sample["video"],
                    "final_logits": final_logits.half(),
                },
                logits_path,
            )
            scores = {
                str(beta): score_logits(
                    final_logits, args.temperature, beta, DEFAULT_TOKEN_IDS
                )
                for beta in args.betas
            }
            record = {
                "sample_index": sample_index,
                "video": sample["video"],
                "query": query,
                "total_frames": total_frames,
                "average_fps": average_fps,
                "frame_count": len(frame_indices),
                "frame_indices": frame_indices,
                "logits_path": str(logits_path),
                "scores": scores,
                "runtime_sec": round(time.time() - started, 3),
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            print(
                json.dumps(
                    {
                        "sample_index": sample_index,
                        "video": sample["video"],
                        "frame_count": len(frame_indices),
                        "runtime_sec": record["runtime_sec"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--questions-file", required=True)
    parser.add_argument("--video-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--attn-implementation", default="sdpa")
    parser.add_argument(
        "--frame-policy", choices=("fixed", "fps1", "fps1_max"), default="fixed"
    )
    parser.add_argument("--max-frames", type=int, default=32)
    parser.add_argument("--temperature", type=float, default=0.05)
    parser.add_argument("--betas", type=float, nargs="+", default=[1e-6, 0.2])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
