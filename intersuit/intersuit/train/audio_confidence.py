"""P3 Gate-only 训练合同：冻结特征、监督损失与 checkpoint。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

import torch
import torch.nn.functional as F
from torch.utils.data import Dataset, Sampler

from intersuit.model.streaming_av.confidence_gate import AudioSignalFeatures


FEATURE_PACK_SCHEMA = "as_m4_audio_confidence_frozen_features_v2"


class GateFeatureCollator:
    """将等长冻结特征样本堆叠为 Gate batch，并拒绝二阶段 shift。"""

    def __call__(self, samples: list[Mapping[str, Any]]) -> dict[str, Any]:
        if not samples:
            raise ValueError("Gate batch 不能为空")
        if any(bool(sample.get("metadata", {}).get("shifted", False)) for sample in samples):
            raise ValueError("P3 首轮 collator 不接受 shifted 条件")
        schemas = {sample.get("schema") for sample in samples if "schema" in sample}
        if schemas and schemas != {FEATURE_PACK_SCHEMA}:
            raise ValueError(f"Gate batch schema 不一致：{sorted(str(value) for value in schemas)}")
        keys = (
            "audio", "video", "question", "valid_mask", "router_target",
            "quality_rank", "quality_mask", "relevance_rank", "relevance_mask",
            "sync_rank",
        )
        # 每个磁盘特征包自身带 batch 维；collator 沿 batch 维拼接，不能再额外 stack。
        result = {key: torch.cat([torch.as_tensor(sample[key]) for sample in samples], dim=0) for key in keys}
        if "signal_features" in samples[0]:
            signal = samples[0]["signal_features"]
            result["signal_features"] = {
                key: torch.cat(
                    [torch.as_tensor(sample["signal_features"][key]) for sample in samples], dim=0
                )
                for key in signal
            }
        if all("metadata" in sample for sample in samples):
            result["pair_group_id"] = [
                str(sample["metadata"]["pair_group_id"])
                for sample in samples
                for _ in range(int(torch.as_tensor(sample["audio"]).shape[0]))
            ]
        return result


@dataclass(frozen=True)
class FrozenFeaturePack:
    """已经由冻结编码器生成、可重复加载的 Gate 输入。"""

    audio: torch.Tensor
    video: torch.Tensor
    question: torch.Tensor
    valid_mask: torch.Tensor
    router_target: torch.Tensor
    quality_rank: torch.Tensor
    quality_mask: torch.Tensor
    relevance_rank: torch.Tensor
    relevance_mask: torch.Tensor
    sync_rank: torch.Tensor
    signal_features: Mapping[str, torch.Tensor] | None = None

    def validate(self) -> "FrozenFeaturePack":
        if self.audio.ndim != 3 or self.video.shape != self.audio.shape:
            raise ValueError("audio/video 必须为相同的 [B,T,H] 特征")
        batch, steps, _ = self.audio.shape
        if self.question.ndim not in (2, 3) or self.question.shape[0] != batch:
            raise ValueError("question 必须以 batch 维开头")
        for name, value in (
            ("valid_mask", self.valid_mask),
            ("quality_rank", self.quality_rank),
            ("quality_mask", self.quality_mask),
            ("relevance_rank", self.relevance_rank),
            ("relevance_mask", self.relevance_mask),
            ("sync_rank", self.sync_rank),
        ):
            if value.shape != (batch, steps):
                raise ValueError(f"{name} 必须为 {(batch, steps)}，实际为 {tuple(value.shape)}")
        if self.router_target.shape not in {(batch,), (batch, steps)}:
            raise ValueError("router_target 必须为 [B] 或 [B,T]")
        for name, value in (
            ("audio", self.audio), ("video", self.video), ("question", self.question),
            ("quality_rank", self.quality_rank), ("relevance_rank", self.relevance_rank),
            ("sync_rank", self.sync_rank),
        ):
            if not torch.isfinite(value).all():
                raise ValueError(f"{name} 包含 NaN/Inf")
        if self.signal_features is not None:
            for name, value in self.signal_features.items():
                if value.shape != (batch, steps):
                    raise ValueError(f"signal_features.{name} 必须为 {(batch, steps)}")
                if not torch.isfinite(value).all():
                    raise ValueError(f"signal_features.{name} 包含 NaN/Inf")
        return self

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": FEATURE_PACK_SCHEMA,
            "audio": self.audio.detach().cpu(),
            "video": self.video.detach().cpu(),
            "question": self.question.detach().cpu(),
            "valid_mask": self.valid_mask.detach().cpu().bool(),
            "router_target": self.router_target.detach().cpu().float(),
            "quality_rank": self.quality_rank.detach().cpu().float(),
            "quality_mask": self.quality_mask.detach().cpu().bool(),
            "relevance_rank": self.relevance_rank.detach().cpu().float(),
            "relevance_mask": self.relevance_mask.detach().cpu().bool(),
            "sync_rank": self.sync_rank.detach().cpu().float(),
            "signal_features": {
                key: value.detach().cpu() for key, value in (self.signal_features or {}).items()
            },
        }


def save_frozen_feature_pack(path: str | Path, pack: FrozenFeaturePack, metadata: Mapping[str, Any] | None = None) -> None:
    payload = pack.as_dict()
    payload["metadata"] = dict(metadata or {})
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, destination)


def load_frozen_feature_pack(path: str | Path, *, map_location: str | torch.device = "cpu") -> FrozenFeaturePack:
    payload = torch.load(Path(path), map_location=map_location, weights_only=False)
    if payload.get("schema") != FEATURE_PACK_SCHEMA:
        raise ValueError(f"特征包 schema 不匹配：{payload.get('schema')!r}")
    signal = payload.get("signal_features") or {}
    return FrozenFeaturePack(
        audio=payload["audio"], video=payload["video"], question=payload["question"],
        valid_mask=payload["valid_mask"], router_target=payload["router_target"],
        quality_rank=payload["quality_rank"], quality_mask=payload["quality_mask"],
        relevance_rank=payload["relevance_rank"], relevance_mask=payload["relevance_mask"],
        sync_rank=payload["sync_rank"], signal_features=signal,
    ).validate()


def _pairwise_rank_loss(
    score: torch.Tensor,
    rank: torch.Tensor,
    mask: torch.Tensor,
    pair_group_id: list[str] | tuple[str, ...] | None = None,
) -> torch.Tensor:
    original_shape = score.shape
    score = score.float().reshape(-1)
    rank = rank.float().reshape(-1)
    valid = mask.bool().reshape(-1)
    if valid.sum() < 2:
        return score.sum() * 0.0
    score, rank = score[valid], rank[valid]
    delta_rank = rank[:, None] - rank[None, :]
    pairs = delta_rank > 0
    if pair_group_id is not None:
        if len(original_shape) != 2 or len(pair_group_id) != original_shape[0]:
            raise ValueError("pair_group_id 必须与 rank 的 batch 维一致")
        expanded_groups = [
            group for group in pair_group_id for _ in range(int(original_shape[1]))
        ]
        valid_groups = [group for group, keep in zip(expanded_groups, valid.tolist()) if keep]
        same_group = torch.tensor(
            [[left == right for right in valid_groups] for left in valid_groups],
            dtype=torch.bool,
            device=score.device,
        )
        pairs = pairs & same_group
    if not pairs.any():
        return score.sum() * 0.0
    # Larger rank means the condition should receive the larger score.
    delta_score = score[:, None] - score[None, :]
    return F.relu(0.1 - delta_score[pairs]).mean()


def compute_gate_losses(output: Any, batch: Mapping[str, torch.Tensor], *,
                        lambda_rank: float = 1.0, lambda_quality: float = 1.0,
                        lambda_relevance: float = 1.0, lambda_sparse: float = 0.05,
                        lambda_sync: float = 0.0) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
    """计算首阶段 Gate loss；sync 权重非零时直接拒绝，防止误启用二阶段。"""
    if lambda_sync != 0.0:
        raise ValueError("P3 首阶段必须保持 lambda_sync=0")
    mask = batch["valid_mask"].to(device=output.gate.device, dtype=torch.bool)
    router_target = batch["router_target"].to(output.gate.device, dtype=torch.float32)
    if router_target.ndim == 2:
        router_target = router_target[:, 0]
    router = output.task_audio_need[:, 0]
    router_loss = F.binary_cross_entropy(router.clamp(1e-5, 1 - 1e-5), router_target)
    quality_rank = batch["quality_rank"].to(output.gate.device)
    relevance_rank = batch["relevance_rank"].to(output.gate.device)
    quality_mask = mask & batch["quality_mask"].to(output.gate.device, dtype=torch.bool)
    relevance_mask = mask & batch["relevance_mask"].to(output.gate.device, dtype=torch.bool)
    pair_group_id = batch.get("pair_group_id")
    quality_loss = _pairwise_rank_loss(
        output.quality, quality_rank, quality_mask, pair_group_id
    )
    relevance_loss = _pairwise_rank_loss(
        output.relevance, relevance_rank, relevance_mask, pair_group_id
    )
    # Visual-only rows (router target 0) should suppress audio influence.
    sparse_mask = (router_target <= 0).view(-1, 1) & mask
    sparse_loss = output.gate[sparse_mask].mean() if sparse_mask.any() else output.gate.sum() * 0.0
    total = router_loss + lambda_rank * (
        lambda_quality * quality_loss + lambda_relevance * relevance_loss
    ) + lambda_sparse * sparse_loss
    metrics = {"loss": total.detach(), "router_bce": router_loss.detach(), "quality_rank": quality_loss.detach(),
               "relevance_rank": relevance_loss.detach(), "visual_suppression": sparse_loss.detach(),
               "sync_loss": total.detach() * 0.0}
    return total, metrics


def save_gate_only_checkpoint(path: str | Path, model: torch.nn.Module, optimizer: torch.optim.Optimizer,
                              *, step: int, metadata: Mapping[str, Any] | None = None) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"schema": "as_m4_audio_confidence_gate_checkpoint_v1", "step": int(step),
                "model": model.state_dict(), "optimizer": optimizer.state_dict(),
                "metadata": dict(metadata or {})}, destination)


def load_gate_only_checkpoint(path: str | Path, model: torch.nn.Module, optimizer: torch.optim.Optimizer | None = None,
                              *, map_location: str | torch.device = "cpu") -> int:
    payload = torch.load(Path(path), map_location=map_location, weights_only=False)
    if payload.get("schema") != "as_m4_audio_confidence_gate_checkpoint_v1":
        raise ValueError("Gate-only checkpoint schema 不匹配")
    model.load_state_dict(payload["model"], strict=True)
    if optimizer is not None:
        optimizer.load_state_dict(payload["optimizer"])
    return int(payload["step"])


class FrozenFeatureManifestDataset(Dataset):
    """惰性读取已经独立审计的 P3 feature manifest。"""

    def __init__(self, manifest_path: str | Path) -> None:
        self.manifest_path = Path(manifest_path)
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != "as_m4_audio_confidence_feature_manifest_v1":
            raise ValueError("P3 feature manifest schema 不匹配")
        self.records = list(manifest.get("records") or [])
        if not self.records:
            raise ValueError("P3 feature manifest 为空")

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> dict[str, Any]:
        record = self.records[index]
        path = (self.manifest_path.parent / record["feature_path"]).resolve()
        if self.manifest_path.parent.resolve() not in path.parents:
            raise ValueError("P3 特征路径越出 manifest 根目录")
        payload = torch.load(path, map_location="cpu", weights_only=False)
        if payload.get("schema") != FEATURE_PACK_SCHEMA:
            raise ValueError("P3 feature pack schema 不匹配")
        return payload


class PairGroupBatchSampler(Sampler[list[int]]):
    """每个 batch 只包含一个 pair_group，防止跨问题构造排序 pair。"""

    def __init__(self, records: list[Mapping[str, Any]], *, shuffle: bool, seed: int) -> None:
        groups: dict[str, list[int]] = {}
        for index, record in enumerate(records):
            groups.setdefault(str(record["pair_group_id"]), []).append(index)
        if not groups:
            raise ValueError("没有可用 pair_group")
        self.groups = [tuple(indices) for _, indices in sorted(groups.items())]
        self.shuffle = bool(shuffle)
        self.seed = int(seed)
        self.epoch = 0

    def __iter__(self):
        order = torch.arange(len(self.groups))
        if self.shuffle:
            generator = torch.Generator().manual_seed(self.seed + self.epoch)
            order = order[torch.randperm(len(order), generator=generator)]
        self.epoch += 1
        for index in order.tolist():
            yield list(self.groups[index])

    def __len__(self) -> int:
        return len(self.groups)


def gate_only_train_step(
    model: torch.nn.Module,
    batch: Mapping[str, Any],
    optimizer: torch.optim.Optimizer,
    *,
    device: torch.device | str,
) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
    """执行一个 Gate-only optimizer step，并严格检查 loss/gradient/output。"""

    tensor_batch = {
        key: value.to(device) if torch.is_tensor(value) else value
        for key, value in batch.items()
    }
    signal_values = tensor_batch.get("signal_features")
    signal = (
        AudioSignalFeatures(
            **{key: value.to(device) for key, value in signal_values.items()}
        )
        if signal_values
        else None
    )
    optimizer.zero_grad(set_to_none=True)
    output = model(
        tensor_batch["audio"], tensor_batch["video"], tensor_batch["question"],
        signal_features=signal, valid_mask=tensor_batch["valid_mask"],
    )
    loss, metrics = compute_gate_losses(output, tensor_batch)
    if not torch.isfinite(loss):
        raise RuntimeError("Gate-only loss 出现 NaN/Inf")
    loss.backward()
    for name, parameter in model.named_parameters():
        if parameter.grad is not None and not torch.isfinite(parameter.grad).all():
            raise RuntimeError(f"Gate-only gradient 出现 NaN/Inf：{name}")
    optimizer.step()
    if not torch.isfinite(output.gate).all() or output.gate.min() < 0 or output.gate.max() > 1:
        raise RuntimeError("Gate 输出非有限或越界")
    return loss.detach(), metrics
