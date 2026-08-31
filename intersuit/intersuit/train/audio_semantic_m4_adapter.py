"""CPU contracts for the UnAV-v2 to M4 adapter-only pilot."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any
import torch
import torch.nn.functional as F

CONDITION_ORDER = ("video_only", "correct", "mismatch", "silence")
REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve_repo_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()

def load_pilot_manifest(path: str | Path) -> list[dict[str, Any]]:
    rows=[json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines() if x]
    if not rows or len(rows)%4: raise ValueError('adapter pilot manifest 必须为非空四条件组')
    return rows


def group_pilot_manifest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and collate exactly one four-condition counterfactual group."""
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        group_id = str(row.get("pair_group_id", ""))
        condition = str(row.get("condition", ""))
        if not group_id or condition not in CONDITION_ORDER:
            raise ValueError("adapter pilot 行缺少 pair_group_id 或 condition")
        group = groups.setdefault(group_id, {})
        if condition in group:
            raise ValueError(f"pair_group_id={group_id} 重复 condition={condition}")
        group[condition] = row
    expected = set(CONDITION_ORDER)
    result = []
    for group_id in sorted(groups):
        group = groups[group_id]
        if set(group) != expected:
            raise ValueError(f"pair_group_id={group_id} 必须包含四条件，实际={sorted(group)}")
        reference = group["correct"]
        for condition, row in group.items():
            if row.get("question") != reference.get("question") or row.get("answer") != reference.get("answer"):
                raise ValueError(f"pair_group_id={group_id} 的问题/答案必须四条件一致")
            if condition == "mismatch" and row.get("answer") != reference.get("answer"):
                raise ValueError("mismatch answer 不得进入监督标签")
        result.append({"pair_group_id": group_id, "conditions": {c: group[c] for c in CONDITION_ORDER},
                       "question": reference["question"], "answer": reference["answer"]})
    return result


def build_m4_forward_kwargs(group: dict[str, Any], waveforms: dict[str, torch.Tensor | None],
                            input_ids: torch.Tensor, labels: torch.Tensor, images=None,
                            modalities=None) -> dict[str, Any]:
    """Create one condition's real M4 forward kwargs with shared labels."""
    condition = str(group.get("condition", "correct"))
    if condition not in CONDITION_ORDER:
        raise ValueError(f"unknown adapter pilot condition={condition}")
    waveform = waveforms.get(condition)
    kwargs = {"input_ids": input_ids, "labels": labels, "images": images,
              "modalities": modalities or ["video"]}
    if condition != "video_only":
        if waveform is None:
            raise ValueError(f"condition={condition} 必须提供 waveform")
        kwargs["scene_audios"] = waveform
    else:
        kwargs["scene_audios"] = None
    return kwargs

def materialize_waveform(row: dict[str, Any], loader) -> torch.Tensor | None:
    condition=row['condition']
    if condition=='video_only': return None
    waveform=torch.as_tensor(loader(row['audio_source_path'])).float()
    if condition=='silence': return torch.zeros_like(waveform)
    return waveform

def adapter_pilot_loss(correct_logits: torch.Tensor, mismatch_logits: torch.Tensor,
                       target_ids: torch.Tensor, visual_delta: torch.Tensor,
                       *, margin: float=0.2, visual_weight: float=0.1):
    if correct_logits.shape!=mismatch_logits.shape or correct_logits.ndim!=3: raise ValueError('logits shape mismatch')
    if target_ids.shape!=correct_logits.shape[:2]: raise ValueError('target shape mismatch')
    correct_nll=F.cross_entropy(correct_logits.flatten(0,1),target_ids.flatten())
    mismatch_nll=F.cross_entropy(mismatch_logits.flatten(0,1),target_ids.flatten())
    ranking=F.relu(float(margin)+correct_nll-mismatch_nll)
    suppression=visual_delta.float().square().mean()
    total=correct_nll+ranking+float(visual_weight)*suppression
    if not torch.isfinite(total): raise RuntimeError('adapter pilot loss 非有限')
    return total, {'correct_nll':correct_nll.detach(),'mismatch_nll':mismatch_nll.detach(),'ranking':ranking.detach(),'visual_suppression':suppression.detach()}


def counterfactual_ranking_coefficients(correct_nll: float, mismatch_nll: float, *, margin: float) -> dict[str, float | bool]:
    """Return the exact hinge subgradient coefficients for memory-safe separate forwards."""
    values = torch.tensor([correct_nll, mismatch_nll, margin], dtype=torch.float64)
    if not torch.isfinite(values).all() or float(margin) < 0:
        raise ValueError("counterfactual ranking NLL/margin 必须 finite 且 margin 非负")
    hinge = max(0.0, float(margin) + float(correct_nll) - float(mismatch_nll))
    active = hinge > 0.0
    return {
        "active": active,
        "hinge": hinge,
        "correct_coefficient": 2.0 if active else 1.0,
        "mismatch_coefficient": -1.0 if active else 0.0,
    }

def build_teacher_forced_labels(tokenizer, answer: str, device='cpu') -> torch.Tensor:
    """Build answer-only labels; prompt/image/audio positions are ignored."""
    encoded = tokenizer(answer, return_tensors='pt', add_special_tokens=False)
    ids = encoded.input_ids.to(device)
    if ids.ndim != 2 or ids.shape[1] == 0:
        raise ValueError('answer tokenization produced no labels')
    return ids


def build_m4_teacher_forced_example(tokenizer, question: str, answer: str, device='cpu') -> tuple[torch.Tensor, torch.Tensor]:
    """Build the qwen_1_5 video prompt and answer-only causal-LM labels."""
    from intersuit.constants import DEFAULT_IMAGE_TOKEN, IMAGE_TOKEN_INDEX
    from intersuit.conversation import conv_templates
    from intersuit.mm_utils import tokenizer_image_token

    clean_question = str(question).replace(DEFAULT_IMAGE_TOKEN, "").strip()
    if not clean_question or not str(answer).strip():
        raise ValueError("adapter pilot question/answer 不能为空")
    conversation = conv_templates["qwen_1_5"].copy()
    conversation.append_message(conversation.roles[0], f"{DEFAULT_IMAGE_TOKEN}\n{clean_question}")
    conversation.append_message(conversation.roles[1], None)
    prompt_ids = tokenizer_image_token(
        conversation.get_prompt(), tokenizer, IMAGE_TOKEN_INDEX, return_tensors="pt"
    ).unsqueeze(0).to(device)
    answer_ids = build_teacher_forced_labels(tokenizer, str(answer), device=device)
    eos_token_id = getattr(tokenizer, "eos_token_id", None)
    if eos_token_id is not None and int(answer_ids[0, -1]) != int(eos_token_id):
        answer_ids = torch.cat(
            [answer_ids, torch.tensor([[int(eos_token_id)]], device=answer_ids.device, dtype=answer_ids.dtype)],
            dim=1,
        )
    return build_teacher_forced_batch(prompt_ids, answer_ids)


def build_teacher_forced_batch(prompt_ids: torch.Tensor, answer_ids: torch.Tensor,
                               *, ignore_index: int = -100) -> tuple[torch.Tensor, torch.Tensor]:
    """Append answer tokens and mask prompt positions for causal-LM teacher forcing."""
    if prompt_ids.ndim != 2 or answer_ids.ndim != 2 or prompt_ids.shape[0] != answer_ids.shape[0]:
        raise ValueError("prompt_ids/answer_ids 必须是相同 batch 的二维张量")
    if prompt_ids.shape[1] == 0 or answer_ids.shape[1] == 0:
        raise ValueError("prompt 和 answer 均不能为空")
    input_ids = torch.cat([prompt_ids, answer_ids], dim=1)
    labels = torch.full_like(input_ids, int(ignore_index))
    labels[:, prompt_ids.shape[1]:] = answer_ids
    return input_ids, labels


def m4_teacher_forced_forward(model, *, input_ids: torch.Tensor, labels: torch.Tensor,
                              scene_audios: torch.Tensor | None = None, images=None,
                              modalities=None, **kwargs):
    """Call the real M4 causal-LM path and fail closed on missing/invalid loss."""
    if input_ids.ndim != 2 or labels.shape != input_ids.shape:
        raise ValueError("M4 teacher forcing 要求 labels 与 input_ids 形状完全一致")
    call_kwargs = dict(input_ids=input_ids, labels=labels, images=images,
                       modalities=modalities or ["video"], scene_audios=scene_audios)
    call_kwargs.update(kwargs)
    outputs = model(**call_kwargs)
    loss = getattr(outputs, "loss", None)
    if loss is None and isinstance(outputs, (tuple, list)) and outputs:
        loss = outputs[0]
    if loss is None or not torch.is_tensor(loss) or not torch.isfinite(loss):
        raise RuntimeError("M4 teacher-forcing loss 缺失或非有限")
    return outputs


def configure_adapter_only_model(model) -> tuple[str, ...]:
    """Freeze the loaded M4 and expose only the new scene-audio adapter."""
    from .trainable_parts import enable_scene_audio_m4_adapter_only
    if not hasattr(model, "get_scene_audio_encoder") or model.get_scene_audio_encoder() is None:
        raise RuntimeError("M4 模型未初始化 scene_audio_encoder")
    encoder = model.get_scene_audio_encoder()
    if isinstance(encoder, list):
        encoder = encoder[0]
    model_body = model.get_model() if hasattr(model, "get_model") else model
    model_dtype = next(model_body.embed_tokens.parameters()).dtype if hasattr(model_body, "embed_tokens") else next(model.parameters()).dtype
    encoder.m4_adapter.float()
    encoder.m4_output_dtype = model_dtype
    names = enable_scene_audio_m4_adapter_only(model)
    if any(not p.requires_grad for n, p in model.named_parameters() if n in names):
        raise RuntimeError("m4_adapter 解冻失败")
    return names


def load_adapter_only_model(contract: dict[str, Any], *, device: str = "cpu", loader=None):
    """Load M4 with explicit beats_v4/UnAV settings; caller owns GPU authorization."""
    checkpoint = str(_resolve_repo_path(contract["base_model"]["checkpoint"]))
    semantic_checkpoint = str(_resolve_repo_path(contract["audio_source"]["checkpoint"]))
    if loader is None:
        from intersuit.model.builder import load_pretrained_model
        from intersuit.mm_utils import get_model_name_from_path
        loader = load_pretrained_model
        model_name = get_model_name_from_path(checkpoint)
        initialization_seed = int(contract.get("optimization", {}).get("initialization_seed", 20260831))
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(initialization_seed)
            tokenizer, model, image_processor, context_len = loader(
                checkpoint, None, model_name, device_map=device, multimodal=True,
                attn_implementation=str(contract["base_model"].get("attn_implementation", "sdpa")),
                overwrite_config={
                    "enable_scene_audio": True,
                    "scene_audio_encoder_type": "beats_v4",
                    "scene_audio_hidden_size": int(contract["base_model"]["m4_hidden_size"]),
                    "scene_audio_v4_projector_path": semantic_checkpoint,
                    "scene_audio_v4_projector_sha256": str(contract["audio_source"]["sha256"]),
                    "scene_audio_window_mode": "fixed",
                    "as_m4_fusion_position": str(contract["base_model"].get("fusion_position", "llm_input_audio_tokens")),
                    "as_m4_audio_token_gate": float(contract["base_model"].get("audio_token_gate", 1.0)),
                    "force_audio_gate": None,
                },
            )
    else:
        tokenizer, model, image_processor, context_len = loader(contract, device)
    names = configure_adapter_only_model(model)
    return tokenizer, model, image_processor, context_len, names


def build_adapter_only_optimizer(model, *, learning_rate: float, weight_decay: float = 0.0):
    """Create and independently audit the exact two-tensor adapter optimizer."""
    from .trainable_parts import assert_scene_audio_m4_adapter_only_optimizer

    parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    if len(parameters) != 2:
        raise RuntimeError(f"adapter-only optimizer 要求 2 个可训练 tensor，实际={len(parameters)}")
    optimizer = torch.optim.AdamW(parameters, lr=float(learning_rate), weight_decay=float(weight_decay))
    names = assert_scene_audio_m4_adapter_only_optimizer(optimizer, model)
    return optimizer, names


def _module_state_sha256_excluding(module: torch.nn.Module, excluded_prefixes: tuple[str, ...] = ()) -> str:
    digest = hashlib.sha256()
    included = 0
    for name, value in sorted(module.state_dict().items()):
        if any(name == prefix or name.startswith(prefix + ".") for prefix in excluded_prefixes):
            continue
        tensor = value.detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(str(tuple(tensor.shape)).encode("ascii"))
        digest.update(tensor.view(torch.uint8).numpy().tobytes())
        included += 1
    if included == 0:
        raise RuntimeError("冻结模块 SHA 审计没有可哈希 state")
    return digest.hexdigest()


def adapter_frozen_runtime_sha256(model) -> dict[str, str]:
    """Hash frozen audio/runtime state while deliberately excluding the trainable adapter."""
    body = model.get_model() if hasattr(model, "get_model") else model
    encoder = getattr(body, "scene_audio_encoder", None)
    runtime = getattr(body, "streaming_av_module", None)
    if isinstance(encoder, list):
        encoder = encoder[0] if encoder else None
    if isinstance(runtime, list):
        runtime = runtime[0] if runtime else None
    if encoder is None:
        raise RuntimeError("冻结 SHA 审计缺少 scene_audio_encoder")
    result = {
        "scene_audio_encoder_without_m4_adapter": _module_state_sha256_excluding(
            encoder, ("m4_adapter",)
        )
    }
    result["streaming_av_module"] = (
        _module_state_sha256_excluding(runtime) if runtime is not None else "__absent__"
    )
    return result


def assert_finite_adapter_gradients(model, trainable_names: tuple[str, ...]) -> dict[str, float]:
    expected = set(trainable_names)
    norms: dict[str, float] = {}
    for name, parameter in model.named_parameters():
        if name in expected:
            if parameter.grad is None or not torch.isfinite(parameter.grad).all():
                raise RuntimeError(f"adapter gradient 缺失或非有限: {name}")
            norms[name] = float(parameter.grad.detach().float().norm().item())
        elif parameter.grad is not None:
            raise RuntimeError(f"冻结参数意外产生 gradient: {name}")
    if set(norms) != expected:
        raise RuntimeError(f"adapter gradient 白名单不完整: expected={sorted(expected)}, actual={sorted(norms)}")
    return norms


def assert_finite_adapter_parameters(model, trainable_names: tuple[str, ...]) -> dict[str, float]:
    expected = set(trainable_names)
    norms: dict[str, float] = {}
    for name, parameter in model.named_parameters():
        if name not in expected:
            continue
        if not torch.isfinite(parameter).all():
            raise RuntimeError(f"optimizer step 后 adapter 参数非有限: {name}")
        norms[name] = float(parameter.detach().float().norm().item())
    if set(norms) != expected:
        raise RuntimeError("optimizer step 后 adapter 参数白名单不完整")
    return norms


def adapter_state_sha256(model) -> str:
    encoder = model.get_scene_audio_encoder()
    if isinstance(encoder, list):
        encoder = encoder[0]
    return _module_state_sha256_excluding(encoder.m4_adapter)


def save_adapter_only_checkpoint(path: str | Path, model, optimizer, *, step: int, metadata: dict[str, Any]) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoder = model.get_scene_audio_encoder()
    if isinstance(encoder, list):
        encoder = encoder[0]
    payload = {
        "step": int(step),
        "adapter_state_dict": {name: value.detach().cpu() for name, value in encoder.m4_adapter.state_dict().items()},
        "optimizer_state_dict": optimizer.state_dict(),
        "metadata": dict(metadata),
    }
    torch.save(payload, path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_adapter_only_checkpoint(path: str | Path, model, optimizer=None) -> dict[str, Any]:
    try:
        payload = torch.load(Path(path), map_location="cpu", weights_only=True)
    except TypeError:
        payload = torch.load(Path(path), map_location="cpu")
    required = {"step", "adapter_state_dict", "optimizer_state_dict", "metadata"}
    if not isinstance(payload, dict) or not required <= set(payload):
        raise ValueError("adapter-only checkpoint schema 不完整")
    encoder = model.get_scene_audio_encoder()
    if isinstance(encoder, list):
        encoder = encoder[0]
    encoder.m4_adapter.load_state_dict(payload["adapter_state_dict"], strict=True)
    if optimizer is not None:
        optimizer.load_state_dict(payload["optimizer_state_dict"])
    return payload


def prepare_real_condition_batch(
    row: dict[str, Any], tokenizer, model, image_processor, *, device: str,
    video_loader=None, audio_loader=None, video_cache: dict[str, torch.Tensor] | None = None,
) -> dict[str, Any]:
    """Materialize one manifest condition for the real M4 causal-LM path."""
    if video_loader is None or audio_loader is None:
        from intersuit.harness.runners.run_predictions_from_plan import (
            _load_scene_audio_tensor, _load_video_tensor,
        )
        video_loader = video_loader or _load_video_tensor
        audio_loader = audio_loader or _load_scene_audio_tensor
    condition = str(row.get("condition"))
    if condition not in CONDITION_ORDER:
        raise ValueError(f"unknown adapter pilot condition={condition}")
    input_ids, labels = build_m4_teacher_forced_example(
        tokenizer, str(row["question"]), str(row["answer"]), device=device
    )
    video_path = str(row["video_path"])
    cache = video_cache if video_cache is not None else {}
    images = cache.get(video_path)
    if images is None:
        images = video_loader(video_path, image_processor, model, device)
        cache[video_path] = images
    scene_audios = None
    scene_audio_timestamps = None
    if condition != "video_only":
        qa = {
            "scene_audio_path": row.get("audio_source_path"),
            "scene_audio_sample_rate": 16000,
            "scene_audio_window_sec": 1.0,
            "scene_audio_hop_sec": 0.5,
        }
        debug_condition = "silence" if condition == "silence" else "original"
        loaded, timestamps, _metadata = audio_loader(qa, debug_condition=debug_condition)
        if loaded is None or timestamps is None:
            raise RuntimeError(f"condition={condition} 音频物化失败")
        dtype = next(model.parameters()).dtype
        scene_audios = loaded.to(device=device, dtype=dtype).unsqueeze(0)
        scene_audio_timestamps = timestamps.to(device=device, dtype=torch.float32).unsqueeze(0)
    return {
        "input_ids": input_ids,
        "labels": labels,
        "attention_mask": torch.ones_like(input_ids, device=input_ids.device),
        "images": [images],
        "modalities": ["video"],
        "scene_audios": scene_audios,
        "scene_audio_timestamps": scene_audio_timestamps,
        "use_cache": False,
    }

def adapter_only_forward(model, adapter, audio_v2: torch.Tensor, answer_labels: torch.Tensor,
                         *, video_context: torch.Tensor | None = None):
    """Minimal contract hook: model must expose frozen LM logits from adapted audio."""
    if audio_v2.ndim != 3 or audio_v2.shape[-1] != 768:
        raise ValueError('audio_v2 must be [B,T,768]')
    adapted = adapter(audio_v2)
    logits = model(adapted, video_context=video_context)
    if logits.ndim != 3 or logits.shape[:2] != answer_labels.shape:
        raise ValueError('adapter pilot model logits/labels shape mismatch')
    return logits
