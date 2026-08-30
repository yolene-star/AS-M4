"""P3 冻结特征预计算与独立资产审计。

该模块只负责把 P2 已锁定条件转换为 Gate-only 可消费的 v2 特征包。
实际编码器必须由调用方显式注入，并提供来源 SHA；这里不创建随机投影，避免
测试替身或未锁定权重被误当成正式训练资产。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import torch
import torchaudio

from intersuit.model.streaming_av.confidence_gate import compute_audio_signal_features
from intersuit.streaming.audio_stream import split_audio_windows, stack_audio_windows
from intersuit.train.audio_confidence import (
    FEATURE_PACK_SCHEMA,
    FrozenFeaturePack,
    load_frozen_feature_pack,
    save_frozen_feature_pack,
)


FEATURE_MANIFEST_SCHEMA = "as_m4_audio_confidence_feature_manifest_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FrozenEncoderBundle:
    """三个冻结编码器及其可审计来源。

    audio_encoder 接收 ``[T,S]`` 的 16 kHz（或 audio_sample_rate 指定值）窗口；
    video_encoder 接收 P2 row 和 ``[T,2]`` 时间戳；question_encoder 接收原始问题。
    三者必须输出同一 hidden_size，才能与正式 FirstStage Gate 合同一致。
    """

    audio_encoder: Callable[[torch.Tensor, torch.Tensor], torch.Tensor]
    video_encoder: Callable[[Mapping[str, Any], torch.Tensor], torch.Tensor]
    question_encoder: Callable[[str], torch.Tensor]
    hidden_size: int
    audio_sample_rate: int
    source_fingerprints: Mapping[str, str]

    def validate(self) -> "FrozenEncoderBundle":
        if self.hidden_size <= 0 or self.audio_sample_rate <= 0:
            raise ValueError("冻结编码器 hidden_size/sample_rate 必须为正数")
        required = {"audio", "video", "question"}
        if set(self.source_fingerprints) != required:
            raise ValueError(f"冻结编码器来源必须精确包含 {sorted(required)}")
        for name, value in self.source_fingerprints.items():
            normalized = str(value).lower()
            if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
                raise ValueError(f"{name} 编码器来源不是合法 SHA-256")
        return self


def _full_windows(
    waveform: torch.Tensor,
    source_sample_rate: int,
    target_sample_rate: int,
    window_sec: float,
    hop_sec: float,
) -> tuple[torch.Tensor, torch.Tensor]:
    mono = waveform.float()
    if mono.ndim == 2:
        mono = mono.mean(dim=0)
    elif mono.ndim != 1:
        raise ValueError("P2 waveform 必须为 [S] 或 [C,S]")
    if source_sample_rate != target_sample_rate:
        mono = torchaudio.functional.resample(mono, source_sample_rate, target_sample_rate)
    window_samples = int(round(float(window_sec) * target_sample_rate))
    windows = split_audio_windows(
        mono, target_sample_rate, window_sec=float(window_sec), hop_sec=float(hop_sec)
    )
    # P2 target_stream_window_count 按完整窗口定义；不把尾部 padding 当成新监督窗口。
    windows = [window for window in windows if window.valid_sample_count == window_samples]
    samples, timestamps = stack_audio_windows(windows)
    if samples.numel() == 0:
        raise ValueError("波形不足以形成一个完整 P3 窗口")
    return samples, timestamps


def _rank_tensor(value: Any, steps: int) -> tuple[torch.Tensor, torch.Tensor]:
    applicable = value is not None
    rank = float(value) if applicable else 0.0
    return torch.full((1, steps), rank), torch.full((1, steps), applicable, dtype=torch.bool)


def build_feature_pack_for_row(
    row: Mapping[str, Any],
    waveform: torch.Tensor,
    encoders: FrozenEncoderBundle,
) -> tuple[FrozenFeaturePack, dict[str, Any]]:
    """把一个首阶段 P2 条件构建为单样本、同时间轴的 v2 特征包。"""

    encoders.validate()
    if row.get("first_stage_training") is not True or str(row.get("condition", "")).startswith("shift_"):
        raise ValueError("P3 首轮禁止 shifted 或未授权条件")
    samples, timestamps = _full_windows(
        waveform,
        int(row["target_audio_sample_rate_hz"]),
        encoders.audio_sample_rate,
        float(row["window_sec"]),
        float(row["hop_sec"]),
    )
    expected_steps = int(row["target_stream_window_count"])
    if samples.shape[0] != expected_steps:
        raise ValueError(
            f"P2 窗口数不匹配：expected={expected_steps}, actual={samples.shape[0]}"
        )
    with torch.inference_mode():
        audio = torch.as_tensor(encoders.audio_encoder(samples, timestamps)).float()
        video = torch.as_tensor(encoders.video_encoder(row, timestamps)).float()
        question = torch.as_tensor(encoders.question_encoder(str(row["question"]))).float()
    expected_sequence_shape = (expected_steps, encoders.hidden_size)
    if tuple(audio.shape) != expected_sequence_shape or tuple(video.shape) != expected_sequence_shape:
        raise ValueError(
            f"冻结 audio/video 特征必须为 {expected_sequence_shape}，"
            f"实际 audio={tuple(audio.shape)}, video={tuple(video.shape)}"
        )
    if tuple(question.shape) != (encoders.hidden_size,):
        raise ValueError(f"冻结 question 特征必须为 {(encoders.hidden_size,)}")
    quality_rank, quality_mask = _rank_tensor(row.get("quality_rank"), expected_steps)
    relevance_rank, relevance_mask = _rank_tensor(row.get("relevance_rank"), expected_steps)
    sync_rank, _ = _rank_tensor(row.get("sync_rank"), expected_steps)
    signal = compute_audio_signal_features(samples.unsqueeze(0))
    pack = FrozenFeaturePack(
        audio=audio.unsqueeze(0),
        video=video.unsqueeze(0),
        question=question.unsqueeze(0),
        valid_mask=torch.ones(1, expected_steps, dtype=torch.bool),
        router_target=torch.tensor([float(row["router_target"])]),
        quality_rank=quality_rank,
        quality_mask=quality_mask,
        relevance_rank=relevance_rank,
        relevance_mask=relevance_mask,
        sync_rank=sync_rank,
        signal_features={name: value for name, value in signal._asdict().items()},
    ).validate()
    metadata = {
        "record_id": str(row["record_id"]),
        "pair_group_id": str(row["pair_group_id"]),
        "physical_video_id": str(row["physical_video_id"]),
        "question_id": str(row["question_id"]),
        "condition": str(row["condition"]),
        "modality": str(row["modality"]),
        "shifted": False,
        "timestamps": timestamps.tolist(),
        "hidden_size": encoders.hidden_size,
        "audio_sample_rate_hz": encoders.audio_sample_rate,
        "encoder_source_sha256": dict(encoders.source_fingerprints),
    }
    return pack, metadata


def precompute_feature_manifest(
    rows: Sequence[Mapping[str, Any]],
    materialize_waveform: Callable[[Mapping[str, Any]], torch.Tensor],
    encoders: FrozenEncoderBundle,
    output_root: Path,
    *,
    p2_manifest_sha256: str,
    limit: int | None = None,
) -> Path:
    """生成特征包和机器 manifest；正式调用方负责提供 SHA 锁定的 P2 rows。"""

    output_root = Path(output_root)
    pack_root = output_root / "packs"
    pack_root.mkdir(parents=True, exist_ok=True)
    selected = list(rows[:limit] if limit is not None else rows)
    if not selected:
        raise ValueError("没有可预计算的 P2 条件")
    records = []
    for row in selected:
        pack, metadata = build_feature_pack_for_row(row, materialize_waveform(row), encoders)
        relative_path = Path("packs") / f"{row['record_id']}.pt"
        destination = output_root / relative_path
        save_frozen_feature_pack(destination, pack, metadata)
        records.append(
            {
                "record_id": str(row["record_id"]),
                "feature_path": relative_path.as_posix(),
                "feature_sha256": sha256_file(destination),
                "condition": str(row["condition"]),
                "modality": str(row["modality"]),
                "pair_group_id": str(row["pair_group_id"]),
            }
        )
    manifest = {
        "schema": FEATURE_MANIFEST_SCHEMA,
        "feature_pack_schema": FEATURE_PACK_SCHEMA,
        "p2_manifest_sha256": str(p2_manifest_sha256),
        "record_count": len(records),
        "hidden_size": encoders.hidden_size,
        "audio_sample_rate_hz": encoders.audio_sample_rate,
        "encoder_source_sha256": dict(encoders.source_fingerprints),
        "records": records,
    }
    manifest_path = output_root / "feature_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def audit_feature_manifest(manifest_path: Path) -> dict[str, Any]:
    """独立重读所有包并核对 SHA、shape、监督 mask 和来源一致性。"""

    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != FEATURE_MANIFEST_SCHEMA:
        raise ValueError("P3 feature manifest schema 不匹配")
    records = manifest.get("records") or []
    if len(records) != int(manifest.get("record_count", -1)) or not records:
        raise ValueError("P3 feature manifest 记录数不一致或为空")
    seen: set[str] = set()
    quality_supervised = relevance_supervised = 0
    for record in records:
        record_id = str(record["record_id"])
        if record_id in seen:
            raise ValueError(f"重复 record_id：{record_id}")
        seen.add(record_id)
        path = (manifest_path.parent / record["feature_path"]).resolve()
        if manifest_path.parent.resolve() not in path.parents:
            raise ValueError("特征路径越出 manifest 根目录")
        if sha256_file(path) != record["feature_sha256"]:
            raise ValueError(f"特征包 SHA 不匹配：{record_id}")
        pack = load_frozen_feature_pack(path)
        if pack.audio.shape[-1] != int(manifest["hidden_size"]):
            raise ValueError(f"特征 hidden_size 不一致：{record_id}")
        payload = torch.load(path, map_location="cpu", weights_only=False)
        metadata = payload.get("metadata") or {}
        if metadata.get("record_id") != record_id or metadata.get("shifted") is not False:
            raise ValueError(f"特征 metadata 不一致：{record_id}")
        if metadata.get("encoder_source_sha256") != manifest["encoder_source_sha256"]:
            raise ValueError(f"编码器来源漂移：{record_id}")
        quality_supervised += int(pack.quality_mask.sum().item())
        relevance_supervised += int(pack.relevance_mask.sum().item())
    return {
        "status": "PASS_P3_FROZEN_FEATURE_AUDIT",
        "record_count": len(records),
        "quality_supervised_windows": quality_supervised,
        "relevance_supervised_windows": relevance_supervised,
        "manifest_sha256": sha256_file(manifest_path),
    }
