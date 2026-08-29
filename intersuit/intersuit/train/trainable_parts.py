"""精确训练范围与 optimizer 审计工具。"""

from __future__ import annotations

from typing import Iterable


SCENE_AUDIO_SELECTOR_ONLY_ALIAS = "scene_audio_selector"
AUDIO_CONFIDENCE_GATE_ONLY_ALIAS = "audio_confidence_gate"
SCENE_AUDIO_SELECTOR_PARAMETER_SUFFIXES = (
    "streaming_av_module.question_conditioned_window_selector.audio_projection.weight",
    "streaming_av_module.question_conditioned_window_selector.question_projection.weight",
)


def scene_audio_selector_parameter_names(model) -> tuple[str, ...]:
    names = tuple(
        name
        for name, _ in model.named_parameters()
        if any(name.endswith(suffix) for suffix in SCENE_AUDIO_SELECTOR_PARAMETER_SUFFIXES)
    )
    if len(names) != len(SCENE_AUDIO_SELECTOR_PARAMETER_SUFFIXES):
        raise RuntimeError(
            "selector-only 参数解析失败："
            f"expected={len(SCENE_AUDIO_SELECTOR_PARAMETER_SUFFIXES)}, actual={len(names)}, names={names}"
        )
    for suffix in SCENE_AUDIO_SELECTOR_PARAMETER_SUFFIXES:
        if sum(name.endswith(suffix) for name in names) != 1:
            raise RuntimeError(f"selector-only 参数缺失或重复: {suffix}")
    return tuple(sorted(names))


def enable_scene_audio_selector_only(model) -> tuple[str, ...]:
    """只解冻 question-conditioned selector 的两个投影权重。"""

    allowed = scene_audio_selector_parameter_names(model)
    allowed_set = set(allowed)
    for name, parameter in model.named_parameters():
        parameter.requires_grad_(name in allowed_set)
    assert_exact_trainable_parameters(model, allowed)
    return allowed


def audio_confidence_gate_parameter_names(model) -> tuple[str, ...]:
    fragment = "streaming_av_module.first_stage_confidence_gate."
    names = tuple(sorted(name for name, _ in model.named_parameters() if fragment in name))
    required_fragments = (
        ".question_router.",
        ".audio_projection.",
        ".video_projection.",
        ".question_projection.",
        ".quality_mlp.",
        ".relevance_mlp.",
    )
    if not names or any(not any(piece in name for name in names) for piece in required_fragments):
        raise RuntimeError(
            "Gate-only 参数解析失败："
            f"required={required_fragments}, actual_count={len(names)}, names={names}"
        )
    return names


def enable_audio_confidence_gate_only(model) -> tuple[str, ...]:
    """只解冻第一阶段 QuestionRouter/Quality/Relevance Gate 与低维投影。"""

    allowed = audio_confidence_gate_parameter_names(model)
    allowed_set = set(allowed)
    for name, parameter in model.named_parameters():
        parameter.requires_grad_(name in allowed_set)
    assert_exact_trainable_parameters(model, allowed)
    return allowed


def assert_exact_trainable_parameters(model, expected_names: Iterable[str]) -> tuple[str, ...]:
    expected = tuple(sorted(expected_names))
    actual = tuple(sorted(name for name, parameter in model.named_parameters() if parameter.requires_grad))
    if actual != expected:
        unexpected = sorted(set(actual) - set(expected))
        missing = sorted(set(expected) - set(actual))
        raise RuntimeError(
            "trainable allowlist 审计失败："
            f"unexpected={unexpected}, missing={missing}"
        )
    return actual


def optimizer_parameter_names(optimizer, model) -> tuple[str, ...]:
    id_to_name = {id(parameter): name for name, parameter in model.named_parameters()}
    names: list[str] = []
    unknown = 0
    for group in optimizer.param_groups:
        for parameter in group["params"]:
            name = id_to_name.get(id(parameter))
            if name is None:
                unknown += 1
            else:
                names.append(name)
    if unknown:
        raise RuntimeError(f"optimizer 含 {unknown} 个不属于当前模型的参数")
    if len(names) != len(set(names)):
        raise RuntimeError("optimizer 参数组存在重复参数")
    return tuple(sorted(names))


def assert_selector_only_optimizer(optimizer, model) -> tuple[str, ...]:
    expected = scene_audio_selector_parameter_names(model)
    actual = optimizer_parameter_names(optimizer, model)
    if actual != expected:
        raise RuntimeError(
            "selector-only optimizer 审计失败："
            f"unexpected={sorted(set(actual) - set(expected))}, "
            f"missing={sorted(set(expected) - set(actual))}"
        )
    return actual


def assert_audio_confidence_gate_only_optimizer(optimizer, model) -> tuple[str, ...]:
    expected = audio_confidence_gate_parameter_names(model)
    actual = optimizer_parameter_names(optimizer, model)
    if actual != expected:
        raise RuntimeError(
            "Gate-only optimizer 审计失败："
            f"unexpected={sorted(set(actual) - set(expected))}, "
            f"missing={sorted(set(expected) - set(actual))}"
        )
    return actual
