"""Valor32k P2 虚拟音频条件加载器。"""

from __future__ import annotations

import hashlib
import json
import math
from collections import OrderedDict
from pathlib import Path
import subprocess
from typing import Any

import torch
from torch.utils.data import Dataset


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_under_root(root: Path, relative_path: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root_resolved / relative_path).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError(f"媒体路径越出根目录：{relative_path}")
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def decode_audio_ffmpeg(
    path: Path,
    sample_rate_hz: int,
    channels: int,
    duration_sec: float,
) -> torch.Tensor:
    if sample_rate_hz <= 0 or channels <= 0 or not math.isfinite(duration_sec) or duration_sec <= 0:
        raise ValueError("目标音频参数无效")
    target_samples = int(round(sample_rate_hz * duration_sec))
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(path),
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate_hz),
        "-t",
        f"{duration_sec:.9f}",
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "pipe:1",
    ]
    completed = subprocess.run(command, check=False, capture_output=True)
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg 音频解码失败：{path}: {error}")
    values = torch.frombuffer(bytearray(completed.stdout), dtype=torch.float32).clone()
    if values.numel() % channels:
        raise RuntimeError(f"ffmpeg 输出不能整除声道数：{path}")
    waveform = values.view(-1, channels).transpose(0, 1).contiguous()
    if waveform.shape[1] < target_samples:
        waveform = torch.nn.functional.pad(waveform, (0, target_samples - waveform.shape[1]))
    elif waveform.shape[1] > target_samples:
        waveform = waveform[:, :target_samples]
    if not torch.isfinite(waveform).all():
        raise RuntimeError(f"解码产生 NaN/Inf：{path}")
    return waveform


def _center_and_normalize(noise: torch.Tensor) -> torch.Tensor:
    centered = noise - noise.mean(dim=-1, keepdim=True)
    rms = centered.square().mean().sqrt()
    if not torch.isfinite(rms) or rms <= 1e-8:
        raise ValueError("噪声源能量过低或无效")
    return centered / rms


def deterministic_noise(
    noise_type: str,
    shape: tuple[int, int],
    seed: int,
    background: torch.Tensor | None = None,
) -> torch.Tensor:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(int(seed) % (2**63 - 1))
    if noise_type == "white":
        noise = torch.randn(shape, generator=generator)
    elif noise_type == "pink":
        white = torch.randn(shape, generator=generator)
        spectrum = torch.fft.rfft(white, dim=-1)
        frequencies = torch.arange(spectrum.shape[-1], dtype=torch.float32)
        scale = torch.ones_like(frequencies)
        scale[1:] = frequencies[1:].rsqrt()
        noise = torch.fft.irfft(spectrum * scale, n=shape[-1], dim=-1)
    elif noise_type == "same_dataset_background":
        if background is None or tuple(background.shape) != shape:
            raise ValueError("same_dataset_background 需要目标形状一致的背景音频")
        noise = background.clone()
    else:
        raise ValueError(f"不支持的噪声类型：{noise_type}")
    return _center_and_normalize(noise)


def add_noise_at_snr(waveform: torch.Tensor, noise: torch.Tensor, snr_db: float) -> torch.Tensor:
    signal_rms = waveform.square().mean().sqrt()
    if not torch.isfinite(signal_rms):
        raise ValueError("原音频能量无效")
    if signal_rms <= 1e-8:
        return waveform.clone()
    noise_unit = _center_and_normalize(noise)
    scale = signal_rms / (10.0 ** (float(snr_db) / 20.0))
    output = waveform + noise_unit * scale
    if not torch.isfinite(output).all():
        raise RuntimeError("加噪后出现 NaN/Inf")
    return output


class VirtualAudioMaterializer:
    """按 P2 recipe 即时生成波形，不在磁盘复制派生媒体。"""

    def __init__(
        self,
        media_root: Path,
        *,
        verify_source_sha256: bool = True,
        cache_size: int = 8,
    ) -> None:
        self.media_root = Path(media_root)
        self.verify_source_sha256 = bool(verify_source_sha256)
        self.cache_size = max(0, int(cache_size))
        self._waveform_cache: OrderedDict[tuple[str, int, int, float], torch.Tensor] = OrderedDict()
        self._verified_hashes: dict[Path, str] = {}

    def _verify(self, path: Path, expected_sha256: str | None) -> None:
        if not self.verify_source_sha256:
            return
        if not expected_sha256 or len(expected_sha256) != 64:
            raise ValueError(f"缺少合法源媒体 SHA-256：{path}")
        actual = self._verified_hashes.get(path)
        if actual is None:
            actual = sha256_file(path)
            self._verified_hashes[path] = actual
        if actual != expected_sha256:
            raise ValueError(f"源媒体 SHA-256 不匹配：{path}")

    def _decode(
        self,
        relative_path: str,
        expected_sha256: str | None,
        sample_rate: int,
        channels: int,
        duration: float,
    ) -> torch.Tensor:
        path = resolve_under_root(self.media_root, relative_path)
        self._verify(path, expected_sha256)
        key = (relative_path, sample_rate, channels, duration)
        cached = self._waveform_cache.get(key)
        if cached is not None:
            self._waveform_cache.move_to_end(key)
            return cached.clone()
        waveform = decode_audio_ffmpeg(path, sample_rate, channels, duration)
        if self.cache_size:
            self._waveform_cache[key] = waveform
            self._waveform_cache.move_to_end(key)
            while len(self._waveform_cache) > self.cache_size:
                self._waveform_cache.popitem(last=False)
        return waveform.clone()

    def materialize(self, row: dict[str, Any], *, allow_second_stage: bool = False) -> torch.Tensor:
        if not row.get("first_stage_training", False) and not allow_second_stage:
            raise ValueError("二阶段 shifted 条件未获首轮加载授权")
        sample_rate = int(row["target_audio_sample_rate_hz"])
        channels = int(row["target_audio_channels"])
        duration = float(row["target_audio_duration_sec"])
        waveform = self._decode(
            row["audio_source_relative_media_path"],
            row.get("audio_source_sha256"),
            sample_rate,
            channels,
            duration,
        )
        recipe = row["corruption_recipe"]
        recipe_type = recipe["type"]
        if recipe_type in {"identity", "cross_video_audio"}:
            return waveform
        if recipe_type == "zeros_like_source":
            return torch.zeros_like(waveform)
        if recipe_type == "additive_noise":
            background = None
            if recipe["noise_type"] == "same_dataset_background":
                background = self._decode(
                    recipe["background_relative_media_path"],
                    recipe.get("background_sha256"),
                    sample_rate,
                    channels,
                    duration,
                )
            noise = deterministic_noise(
                recipe["noise_type"],
                tuple(waveform.shape),
                int(row["corruption_seed"]),
                background,
            )
            return add_noise_at_snr(waveform, noise, float(recipe["snr_db"]))
        if recipe_type == "circular_time_shift":
            shift_samples = int(round(float(recipe["shift_sec"]) * sample_rate))
            return torch.roll(waveform, shifts=shift_samples, dims=-1)
        raise ValueError(f"不支持的 P2 recipe：{recipe_type}")


class Valor32kConfidenceP2Dataset(Dataset):
    """首轮 Gate-only 数据视图；默认硬拒绝二阶段条件。"""

    def __init__(
        self,
        manifest_path: Path,
        media_root: Path,
        *,
        expected_manifest_sha256: str,
        verify_source_sha256: bool = True,
    ) -> None:
        manifest_path = Path(manifest_path)
        actual_sha = sha256_file(manifest_path)
        if actual_sha != expected_manifest_sha256:
            raise ValueError(
                f"P2 manifest SHA-256 不匹配：expected={expected_manifest_sha256}, actual={actual_sha}"
            )
        rows = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.rows = [row for row in rows if row.get("first_stage_training") is True]
        if not self.rows or any(row["condition"].startswith("shift_") for row in self.rows):
            raise ValueError("首轮数据为空或混入 shifted 条件")
        self.materializer = VirtualAudioMaterializer(
            media_root, verify_source_sha256=verify_source_sha256
        )

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        row = self.rows[index]
        return {
            "waveform": self.materializer.materialize(row),
            "sample_rate_hz": int(row["target_audio_sample_rate_hz"]),
            "condition": row["condition"],
            "pair_group_id": row["pair_group_id"],
            "router_target": int(row["router_target"]),
            "quality_rank": row["quality_rank"],
            "relevance_rank": row["relevance_rank"],
            "sync_rank": row["sync_rank"],
        }
