#!/usr/bin/env python
"""采集 OmniMMI PT 的连续分数，供阈值与特殊 token 离线审计。"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
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
AUDIO_MODES = ("behavior_off", "gate0", "correct")


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


def decode_video_audio(video_path: Path, sample_rate: int) -> torch.Tensor | None:
    """用 ffmpeg 将视频首个音轨解码为单声道 float32；无音轨时返回 None。"""

    if sample_rate <= 0:
        raise ValueError("audio_sample_rate 必须为正整数")
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(video_path),
        "-map",
        "0:a:0?",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    completed = subprocess.run(command, capture_output=True, check=False)
    if not completed.stdout:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        no_audio_markers = (
            "does not contain any stream",
            "matches no streams",
            "stream map",
        )
        if completed.returncode != 0 and not any(
            marker in stderr.lower() for marker in no_audio_markers
        ):
            raise RuntimeError(
                f"ffmpeg 音频解码失败：video={video_path}, error={stderr}"
            )
        return None
    waveform = torch.from_numpy(np.frombuffer(completed.stdout, dtype=np.float32).copy())
    if waveform.numel() == 0:
        return None
    if not torch.isfinite(waveform).all():
        raise ValueError(f"视频音频包含非有限值：{video_path}")
    return waveform


def window_waveform(
    waveform: torch.Tensor,
    sample_rate: int,
    window_sec: float,
    hop_sec: float,
) -> tuple[torch.Tensor, torch.Tensor]:
    """按训练/评测约定生成定长音频窗及 [start,end] 时间戳。"""

    if waveform.ndim != 1 or waveform.numel() == 0:
        raise ValueError("waveform 必须是非空一维 tensor")
    if sample_rate <= 0 or window_sec <= 0 or hop_sec <= 0:
        raise ValueError("采样率、window_sec 和 hop_sec 必须为正数")
    window_samples = max(1, round(sample_rate * window_sec))
    hop_samples = max(1, round(sample_rate * hop_sec))
    starts = list(range(0, max(waveform.numel() - window_samples + 1, 1), hop_samples))
    final_start = max(0, waveform.numel() - window_samples)
    if not starts or starts[-1] != final_start:
        starts.append(final_start)
    windows = []
    timestamps = []
    for start in starts:
        window = waveform[start : start + window_samples]
        if window.numel() < window_samples:
            window = torch.nn.functional.pad(window, (0, window_samples - window.numel()))
        windows.append(window)
        timestamps.append(
            [start / sample_rate, (start + window_samples) / sample_rate]
        )
    return torch.stack(windows), torch.tensor(timestamps, dtype=torch.float32)


def configure_audio_mode(model: Any, audio_mode: str, inference_gate: float) -> None:
    if audio_mode not in AUDIO_MODES:
        raise ValueError(f"未知 audio_mode: {audio_mode}")
    if not math.isfinite(inference_gate) or not 0.0 <= inference_gate <= 1.0:
        raise ValueError("audio_inference_gate 必须位于 [0,1]")
    model.config.enable_scene_audio = audio_mode != "behavior_off"
    model.config.force_audio_gate = 0.0 if audio_mode == "gate0" else None
    model.config.as_m4_inference_simple_audio_gate = inference_gate


def configure_beats_chunk_size(model: Any, chunk_size: int) -> None:
    if chunk_size < 0:
        raise ValueError("audio_encode_chunk_size 必须为非负整数")
    get_model = getattr(model, "get_model", None)
    inner = get_model() if callable(get_model) else model
    get_encoder = getattr(inner, "get_scene_audio_encoder", None)
    encoder = get_encoder() if callable(get_encoder) else None
    if encoder is not None and hasattr(encoder, "encode_chunk_size"):
        encoder.encode_chunk_size = int(chunk_size)
    model.config.scene_audio_beats_encode_chunk_size = int(chunk_size)


def diagnostic_scalar(diagnostics: Any, key: str) -> float | None:
    if not isinstance(diagnostics, list):
        return None
    values = []
    for item in diagnostics:
        if isinstance(item, dict) and item.get(key) is not None:
            value = item[key]
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                values.append(float(value))
    return sum(values) / len(values) if values else None


def streaming_av_diagnostics(model: Any) -> Any:
    diagnostics = getattr(model, "_last_streaming_av_diagnostics", None)
    if diagnostics is not None:
        return diagnostics
    get_model = getattr(model, "get_model", None)
    if callable(get_model):
        return getattr(get_model(), "_last_streaming_av_diagnostics", None)
    return None


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
    model_path = str(Path(args.model_path).resolve())
    load_cwd = Path(__file__).resolve().parents[1]
    previous_cwd = Path.cwd()
    try:
        # 官方 M4 config 的 vision tower 使用相对于 intersuit/ 的路径。
        # 与正式 harness 保持一致，只在模型加载期间切换工作目录。
        os.chdir(load_cwd)
        tokenizer, model, image_processor, _ = load_pretrained_model(
            model_path,
            None,
            "llava_qwen",
            device_map=args.device,
            attn_implementation=args.attn_implementation,
        )
    finally:
        os.chdir(previous_cwd)
    model = model.to(args.device).eval()
    configure_audio_mode(model, args.audio_mode, args.audio_inference_gate)
    configure_beats_chunk_size(model, args.audio_encode_chunk_size)

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
            "audio_mode": args.audio_mode,
            "audio_sample_rate": args.audio_sample_rate,
            "audio_window_sec": args.audio_window_sec,
            "audio_hop_sec": args.audio_hop_sec,
            "audio_inference_gate": args.audio_inference_gate,
            "audio_encode_chunk_size": args.audio_encode_chunk_size,
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
            frame_timestamps = torch.tensor(
                [index / average_fps for index in frame_indices],
                dtype=torch.float32,
                device=model.device,
            ).unsqueeze(0)
            frames = reader.get_batch(frame_indices).asnumpy()
            video_tensor = image_processor.preprocess(frames, return_tensors="pt")[
                "pixel_values"
            ].half().to(model.device)

            query = build_pt_query(sample)
            prompt = build_prompt(query)
            input_ids = tokenizer_image_token(
                prompt, tokenizer, IMAGE_TOKEN_INDEX, return_tensors="pt"
            ).unsqueeze(0).to(model.device)
            scene_audios = None
            scene_audio_timestamps = None
            audio_window_count = 0
            audio_available = False
            if args.audio_mode != "behavior_off":
                waveform = decode_video_audio(video_path, args.audio_sample_rate)
                if waveform is not None:
                    audio_windows, audio_timestamps = window_waveform(
                        waveform,
                        args.audio_sample_rate,
                        args.audio_window_sec,
                        args.audio_hop_sec,
                    )
                    model_dtype = next(model.parameters()).dtype
                    scene_audios = audio_windows.to(
                        device=model.device,
                        dtype=model_dtype,
                    ).unsqueeze(0)
                    scene_audio_timestamps = audio_timestamps.to(
                        device=model.device,
                    ).unsqueeze(0)
                    audio_window_count = int(audio_windows.shape[0])
                    audio_available = True
            outputs = model(
                input_ids,
                images=[video_tensor],
                modalities=["video"],
                scene_audios=scene_audios,
                scene_audio_timestamps=scene_audio_timestamps,
                frame_timestamps=frame_timestamps,
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
            selected_score = scores[str(args.betas[0])]["tokens"][
                str(args.decision_token_id)
            ]
            diagnostics = streaming_av_diagnostics(model)
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
                "prediction": "" if selected_score["silent"] else "GEN",
                "decision_token_id": args.decision_token_id,
                "audio_mode": args.audio_mode,
                "audio_available": audio_available,
                "audio_window_count": audio_window_count,
                "audio_gate": diagnostic_scalar(diagnostics, "gate_mean"),
                "audio_norm": diagnostic_scalar(diagnostics, "audio_norm"),
                "delta_to_video_ratio": diagnostic_scalar(
                    diagnostics, "delta_to_video_ratio"
                ),
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
                        "audio_mode": args.audio_mode,
                        "audio_available": audio_available,
                        "audio_window_count": audio_window_count,
                        "audio_gate": record["audio_gate"],
                        "delta_to_video_ratio": record["delta_to_video_ratio"],
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
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--betas", type=float, nargs="+", default=[0.2])
    parser.add_argument("--decision-token-id", type=int, default=151644)
    parser.add_argument("--audio-mode", choices=AUDIO_MODES, default="behavior_off")
    parser.add_argument("--audio-sample-rate", type=int, default=16000)
    parser.add_argument("--audio-window-sec", type=float, default=1.0)
    parser.add_argument("--audio-hop-sec", type=float, default=0.5)
    parser.add_argument("--audio-inference-gate", type=float, default=0.005)
    parser.add_argument("--audio-encode-chunk-size", type=int, default=64)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
