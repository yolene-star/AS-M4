"""统一的场景音频窗口选择合同与确定性策略注册表。

该模块刻意不依赖 M4 runner 或具体 benchmark。外部选窗必须先生成
``SelectionResult``，模型只消费选择结果和明确的 selection stage，避免
runner 与模型内部各选一次。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import random
from typing import Any, Callable, Mapping

import torch


EXTERNAL_PRE_ENCODER_STAGE = "external_pre_encoder"
INTERNAL_MODEL_STAGE = "internal_model"
DIAGNOSTIC_ORACLE_POLICIES = frozenset({"oracle_topk"})


@dataclass(frozen=True)
class SelectionRequest:
    """一条样本的完整窗口集合及某个 policy 所需的只读证据。"""

    sample_id: str
    source_indices: torch.Tensor
    timestamps: torch.Tensor
    valid_mask: torch.Tensor
    top_k: int | None = None
    raw_windows: torch.Tensor | None = None
    cached_features: torch.Tensor | None = None
    scores: Mapping[str, torch.Tensor] = field(default_factory=dict)
    target_intervals: torch.Tensor | None = None
    explicit_selected_source_indices: tuple[int, ...] | None = None
    seed: int | None = None
    diagnostic: bool = False
    training: bool = False
    production: bool = False

    def __post_init__(self) -> None:
        source_indices = torch.as_tensor(self.source_indices, dtype=torch.long).detach().cpu()
        timestamps = torch.as_tensor(self.timestamps, dtype=torch.float32).detach().cpu()
        valid_mask = torch.as_tensor(self.valid_mask, dtype=torch.bool).detach().cpu()
        if source_indices.ndim != 1:
            raise ValueError("source_indices 必须是一维 tensor")
        if timestamps.shape != (source_indices.numel(), 2):
            raise ValueError("timestamps 必须为 [N, 2]，并与 source_indices 等长")
        if valid_mask.shape != source_indices.shape:
            raise ValueError("valid_mask 必须与 source_indices 等形")
        if source_indices.unique().numel() != source_indices.numel():
            raise ValueError("source_indices 必须唯一")
        if not torch.isfinite(timestamps).all() or torch.any(timestamps[:, 1] < timestamps[:, 0]):
            raise ValueError("timestamps 必须有限且 end >= start")
        if self.top_k is not None and int(self.top_k) <= 0:
            raise ValueError("top_k 必须为正整数")
        if not bool(valid_mask.any()):
            raise ValueError("至少需要一个有效窗口")
        for field_name, tensor in (
            ("raw_windows", self.raw_windows),
            ("cached_features", self.cached_features),
        ):
            if tensor is not None and (tensor.ndim == 0 or int(tensor.shape[0]) != source_indices.numel()):
                raise ValueError(f"{field_name} 的第一维必须等于窗口数")
        normalized_scores: dict[str, torch.Tensor] = {}
        for score_name, score in self.scores.items():
            score_tensor = torch.as_tensor(score, dtype=torch.float32).detach().cpu()
            if score_tensor.shape != source_indices.shape:
                raise ValueError(f"{score_name} scores 必须与 source_indices 等形")
            if not torch.isfinite(score_tensor[valid_mask]).all():
                raise ValueError(f"{score_name} scores 的有效位置含 NaN/Inf")
            normalized_scores[str(score_name)] = score_tensor
        if self.target_intervals is not None:
            intervals = torch.as_tensor(self.target_intervals, dtype=torch.float32).detach().cpu()
            if intervals.ndim != 2 or intervals.shape[1] != 2 or intervals.numel() == 0:
                raise ValueError("target_intervals 必须是非空 [M, 2] tensor")
            if not torch.isfinite(intervals).all() or torch.any(intervals[:, 1] < intervals[:, 0]):
                raise ValueError("target_intervals 必须有限且 end >= start")
        object.__setattr__(self, "source_indices", source_indices)
        object.__setattr__(self, "timestamps", timestamps)
        object.__setattr__(self, "valid_mask", valid_mask)
        object.__setattr__(self, "scores", normalized_scores)
        if self.target_intervals is not None:
            object.__setattr__(self, "target_intervals", intervals)


@dataclass(frozen=True)
class SelectionResult:
    """所有 policy 的统一输出；selected_positions 用于安全索引原始 tensor。"""

    policy_name: str
    selected_positions: torch.Tensor
    selected_source_indices: torch.Tensor
    selection_scores: torch.Tensor
    selected_timestamps: torch.Tensor
    selection_stage: str
    full_window_count: int
    valid_window_count: int
    policy_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        positions = torch.as_tensor(self.selected_positions, dtype=torch.long)
        source_indices = torch.as_tensor(self.selected_source_indices, dtype=torch.long)
        scores = torch.as_tensor(self.selection_scores, dtype=torch.float32)
        timestamps = torch.as_tensor(self.selected_timestamps, dtype=torch.float32)
        count = positions.numel()
        if positions.ndim != 1 or count == 0:
            raise ValueError("selected_positions 必须是一维非空 tensor")
        if source_indices.shape != positions.shape or scores.shape != positions.shape:
            raise ValueError("selected source indices/scores 必须与 selected_positions 等形")
        if timestamps.shape != (count, 2):
            raise ValueError("selected_timestamps 必须为 [K, 2]")
        if positions.unique().numel() != count or source_indices.unique().numel() != count:
            raise ValueError("选择结果不得包含重复窗口")
        if positions.min().item() < 0 or positions.max().item() >= int(self.full_window_count):
            raise ValueError("selected_positions 越界")
        if self.selection_stage not in {EXTERNAL_PRE_ENCODER_STAGE, INTERNAL_MODEL_STAGE}:
            raise ValueError(f"不支持的 selection_stage={self.selection_stage!r}")
        if count > int(self.valid_window_count):
            raise ValueError("选择数量不能超过有效窗口数量")
        if not torch.isfinite(scores).all() or not torch.isfinite(timestamps).all():
            raise ValueError("选择结果含 NaN/Inf")
        if count > 1 and torch.any(timestamps[1:, 0] < timestamps[:-1, 0]):
            raise ValueError("选择结果必须按时间顺序输出")
        object.__setattr__(self, "selected_positions", positions)
        object.__setattr__(self, "selected_source_indices", source_indices)
        object.__setattr__(self, "selection_scores", scores)
        object.__setattr__(self, "selected_timestamps", timestamps)

    @property
    def selected_window_count(self) -> int:
        return int(self.selected_positions.numel())

    def to_trace(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "selection_stage": self.selection_stage,
            "full_window_count": int(self.full_window_count),
            "valid_window_count": int(self.valid_window_count),
            "selected_window_count": self.selected_window_count,
            "selected_positions": self.selected_positions.tolist(),
            "selected_source_indices": self.selected_source_indices.tolist(),
            "selection_scores": self.selection_scores.tolist(),
            "selected_timestamps": self.selected_timestamps.tolist(),
            "policy_metadata": dict(self.policy_metadata),
        }


Policy = Callable[[SelectionRequest], SelectionResult]


class SelectionPolicyRegistry:
    """显式 policy 注册表；重复注册和未知策略都 fail-closed。"""

    def __init__(self) -> None:
        self._policies: dict[str, Policy] = {}

    def register(self, name: str, policy: Policy) -> None:
        normalized = str(name).strip().lower()
        if not normalized or normalized in self._policies:
            raise ValueError(f"重复或空 selection policy: {normalized!r}")
        self._policies[normalized] = policy

    def select(self, name: str, request: SelectionRequest) -> SelectionResult:
        normalized = str(name).strip().lower()
        if normalized not in self._policies:
            raise ValueError(f"未知 selection policy={normalized!r}；可用值={sorted(self._policies)}")
        result = self._policies[normalized](request)
        if result.policy_name != normalized:
            raise ValueError("policy 返回的 policy_name 与 registry key 不一致")
        return result

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._policies))


def _valid_positions(request: SelectionRequest) -> list[int]:
    return torch.nonzero(request.valid_mask, as_tuple=False).flatten().tolist()


def _effective_k(request: SelectionRequest, valid_count: int) -> int:
    return valid_count if request.top_k is None else min(int(request.top_k), valid_count)


def _build_result(
    policy_name: str,
    request: SelectionRequest,
    positions: list[int],
    scores: torch.Tensor,
    metadata: Mapping[str, Any] | None = None,
) -> SelectionResult:
    positions = sorted(positions, key=lambda index: (float(request.timestamps[index, 0]), int(request.source_indices[index])))
    position_tensor = torch.tensor(positions, dtype=torch.long)
    return SelectionResult(
        policy_name=policy_name,
        selected_positions=position_tensor,
        selected_source_indices=request.source_indices.index_select(0, position_tensor),
        selection_scores=scores.index_select(0, position_tensor).float(),
        selected_timestamps=request.timestamps.index_select(0, position_tensor).float(),
        selection_stage=EXTERNAL_PRE_ENCODER_STAGE,
        full_window_count=int(request.source_indices.numel()),
        valid_window_count=int(request.valid_mask.sum().item()),
        policy_metadata=dict(metadata or {}),
    )


def _rank_topk(request: SelectionRequest, scores: torch.Tensor) -> list[int]:
    valid = _valid_positions(request)
    k = _effective_k(request, len(valid))
    ranked = sorted(
        valid,
        key=lambda index: (
            -float(scores[index]),
            float(request.timestamps[index, 0]),
            int(request.source_indices[index]),
        ),
    )
    return ranked[:k]


def _fixed_all(request: SelectionRequest) -> SelectionResult:
    positions = _valid_positions(request)
    scores = torch.ones_like(request.source_indices, dtype=torch.float32)
    return _build_result("fixed_all", request, positions, scores, {"all_windows": True})


def _external_indices(request: SelectionRequest) -> SelectionResult:
    requested = request.explicit_selected_source_indices
    if not requested:
        raise ValueError("external_indices 需要 explicit_selected_source_indices")
    if tuple(sorted(set(requested))) != tuple(requested):
        raise ValueError("external selected source indices 必须唯一且递增")
    source_to_position = {int(source): index for index, source in enumerate(request.source_indices.tolist())}
    missing = [source for source in requested if int(source) not in source_to_position]
    if missing:
        raise ValueError(f"external selected source indices 不存在: {missing}")
    positions = [source_to_position[int(source)] for source in requested]
    if not all(bool(request.valid_mask[position]) for position in positions):
        raise ValueError("external selection 包含无效窗口")
    scores = torch.zeros_like(request.source_indices, dtype=torch.float32)
    scores[positions] = 1.0
    return _build_result("external_indices", request, positions, scores, {"provided_indices": True})


def _uniform_topk(request: SelectionRequest) -> SelectionResult:
    valid = _valid_positions(request)
    k = _effective_k(request, len(valid))
    if k == len(valid):
        positions = valid
    elif k == 1:
        positions = [valid[len(valid) // 2]]
    else:
        offsets = [round(index * (len(valid) - 1) / (k - 1)) for index in range(k)]
        positions = [valid[offset] for offset in offsets]
    scores = torch.zeros_like(request.source_indices, dtype=torch.float32)
    scores[positions] = 1.0
    return _build_result("uniform_topk", request, positions, scores)


def _score_policy(policy_name: str, score_name: str) -> Policy:
    def select(request: SelectionRequest) -> SelectionResult:
        if score_name not in request.scores:
            raise ValueError(f"{policy_name} 缺少 {score_name} scores")
        scores = torch.as_tensor(request.scores[score_name], dtype=torch.float32)
        return _build_result(policy_name, request, _rank_topk(request, scores), scores, {"score_name": score_name})

    return select


def _random_topk(request: SelectionRequest) -> SelectionResult:
    if request.seed is None:
        raise ValueError("random_topk 必须显式提供 seed")
    seed_material = f"{request.sample_id}\0{int(request.seed)}".encode("utf-8")
    derived_seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
    rng = random.Random(derived_seed)
    valid = _valid_positions(request)
    k = _effective_k(request, len(valid))
    positions = rng.sample(valid, k)
    scores = torch.zeros_like(request.source_indices, dtype=torch.float32)
    for rank, position in enumerate(positions):
        scores[position] = float(k - rank)
    return _build_result(
        "random_topk",
        request,
        positions,
        scores,
        {"seed": int(request.seed), "derived_seed_sha256": hashlib.sha256(seed_material).hexdigest()},
    )


def _oracle_topk(request: SelectionRequest) -> SelectionResult:
    if request.training or request.production or not request.diagnostic:
        raise PermissionError("oracle_topk 仅允许 diagnostic inference，禁止 train/production")
    if request.target_intervals is None:
        raise ValueError("oracle_topk 缺少 target_intervals")
    intervals = torch.as_tensor(request.target_intervals, dtype=torch.float32)
    scores = torch.zeros_like(request.source_indices, dtype=torch.float32)
    for index, (start, end) in enumerate(request.timestamps.tolist()):
        best = 0.0
        for target_start, target_end in intervals.tolist():
            intersection = max(0.0, min(end, target_end) - max(start, target_start))
            union = max(end, target_end) - min(start, target_start)
            best = max(best, intersection / union if union > 0 else float(start == target_start))
        scores[index] = best
    return _build_result(
        "oracle_topk",
        request,
        _rank_topk(request, scores),
        scores,
        {"uses_ground_truth": True, "target_interval_count": int(intervals.shape[0])},
    )


def build_default_selection_policy_registry() -> SelectionPolicyRegistry:
    registry = SelectionPolicyRegistry()
    registry.register("fixed_all", _fixed_all)
    registry.register("external_indices", _external_indices)
    registry.register("uniform_topk", _uniform_topk)
    registry.register("energy_topk", _score_policy("energy_topk", "energy"))
    registry.register("eventness_topk", _score_policy("eventness_topk", "eventness"))
    registry.register("temporal_highconf_topk", _score_policy("temporal_highconf_topk", "temporal"))
    registry.register("random_topk", _random_topk)
    registry.register("oracle_topk", _oracle_topk)
    return registry


DEFAULT_SELECTION_POLICY_REGISTRY = build_default_selection_policy_registry()


def selection_results_from_model_output(policy_name: str, output) -> tuple[SelectionResult, ...]:
    """将现有模型内部 selector 输出归一为同一审计合同。"""

    required = (
        "selection_mask",
        "selected_source_indices",
        "selection_scores",
        "selected_timestamps",
        "source_weights",
    )
    missing = [name for name in required if not hasattr(output, name)]
    if missing:
        raise ValueError(f"internal selector output 缺少字段: {missing}")
    batch_size = int(output.selection_mask.shape[0])
    full_window_count = int(output.source_weights.shape[-1])
    results: list[SelectionResult] = []
    for batch_index in range(batch_size):
        mask = output.selection_mask[batch_index].detach().bool().cpu()
        if not bool(mask.any()):
            raise ValueError("internal selector output 不得为空")
        weights = output.source_weights[batch_index, mask].detach().float().cpu()
        positions = weights.argmax(dim=-1)
        results.append(
            SelectionResult(
                policy_name=str(policy_name),
                selected_positions=positions,
                selected_source_indices=output.selected_source_indices[batch_index, mask].detach().cpu(),
                selection_scores=output.selection_scores[batch_index, mask].detach().float().cpu(),
                selected_timestamps=output.selected_timestamps[batch_index, mask].detach().float().cpu(),
                selection_stage=INTERNAL_MODEL_STAGE,
                full_window_count=full_window_count,
                valid_window_count=full_window_count,
                policy_metadata={"adapter": "model_selector_output"},
            )
        )
    return tuple(results)
