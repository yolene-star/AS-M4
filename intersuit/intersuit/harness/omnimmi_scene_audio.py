"""OmniMMI 场景音频解码与逐帧窗口工具。"""

from __future__ import annotations

from pathlib import Path
import subprocess

import numpy as np
import torch
import torch.nn.functional as F

from intersuit.model.streaming_av.confidence_gate import AudioSignalFeatures


def decode_video_audio(video_path: str | Path, sample_rate: int = 16000) -> torch.Tensor | None:
    """解码视频首个音轨为单声道 float32；无音轨时返回 ``None``。"""

    path = Path(video_path)
    if sample_rate <= 0:
        raise ValueError("sample_rate 必须为正整数")
    completed = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
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
        ],
        capture_output=True,
        check=False,
    )
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
            raise RuntimeError(f"ffmpeg 音频解码失败：video={path}, error={stderr}")
        return None
    waveform = torch.from_numpy(np.frombuffer(completed.stdout, dtype=np.float32).copy())
    if waveform.numel() == 0:
        return None
    if not torch.isfinite(waveform).all():
        raise ValueError(f"视频音频包含非有限值：{path}")
    return waveform


def frame_audio_windows(
    waveform: torch.Tensor,
    frame_indices: list[int] | range,
    frame_fps: float,
    sample_rate: int = 16000,
    window_sec: float = 1.0,
) -> tuple[torch.Tensor, torch.Tensor]:
    """为每个流式视频帧提取同起点的 1 秒音频窗并补零。"""

    if waveform.ndim != 1 or waveform.numel() == 0:
        raise ValueError("waveform 必须是非空一维 tensor")
    if frame_fps <= 0 or sample_rate <= 0 or window_sec <= 0:
        raise ValueError("frame_fps、sample_rate 和 window_sec 必须为正数")
    indices = [int(index) for index in frame_indices]
    if not indices or any(index < 0 for index in indices):
        raise ValueError("frame_indices 必须包含非负帧编号")
    window_samples = max(1, round(sample_rate * window_sec))
    windows = []
    timestamps = []
    for index in indices:
        start_sec = index / frame_fps
        start_sample = round(start_sec * sample_rate)
        window = waveform[start_sample : start_sample + window_samples]
        if window.numel() < window_samples:
            window = F.pad(window, (0, window_samples - window.numel()))
        windows.append(window)
        timestamps.append([start_sec, start_sec + window_sec])
    return torch.stack(windows), torch.tensor(timestamps, dtype=torch.float32)


def project_audio_signal_features(
    signal_features: AudioSignalFeatures,
    source_weights: torch.Tensor,
) -> AudioSignalFeatures:
    """将原始逐窗信号统计投影到 selector 的定长输出槽。"""

    if source_weights.ndim != 3:
        raise ValueError("source_weights 必须是 [B,K,T]")
    batch, _, source_steps = source_weights.shape
    for name, value in signal_features._asdict().items():
        if value.shape != (batch, source_steps):
            raise ValueError(
                f"{name} 形状 {tuple(value.shape)} 与 source_weights "
                f"来源维度 {(batch, source_steps)} 不匹配"
            )
    weights = source_weights.to(
        device=signal_features.rms.device,
        dtype=signal_features.rms.dtype,
    )
    return AudioSignalFeatures(
        **{
            name: torch.einsum("bkt,bt->bk", weights, value)
            for name, value in signal_features._asdict().items()
        }
    )
