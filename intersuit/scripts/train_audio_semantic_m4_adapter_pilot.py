#!/usr/bin/env python
"""运行 UnAV-v2 -> M4 adapter-only pilot；默认合同关闭训练。"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT=ROOT/'configs/audio_semantic_m4_adapter_only_pilot_v1.json'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def condition_loss(model, batch):
    from intersuit.train.audio_semantic_m4_adapter import m4_teacher_forced_forward
    output = m4_teacher_forced_forward(model, **batch)
    return output.loss


def run_forward_smoke(contract, contract_path: Path, manifest_path: Path, *, device: str, output_dir: Path):
    import torch
    from intersuit.train.audio_semantic_m4_adapter import (
        adapter_frozen_runtime_sha256,
        adapter_state_sha256,
        group_pilot_manifest,
        load_adapter_only_model,
        load_pilot_manifest,
        prepare_real_condition_batch,
    )

    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f'adapter smoke 输出目录非空，拒绝覆盖: {output_dir}')
    output_dir.mkdir(parents=True, exist_ok=True)
    groups = group_pilot_manifest(load_pilot_manifest(manifest_path))
    group = groups[0]
    tokenizer, model, image_processor, _context_len, trainable_names = load_adapter_only_model(
        contract, device=device
    )
    model.eval()
    frozen_before = adapter_frozen_runtime_sha256(model)
    adapter_before = adapter_state_sha256(model)
    video_cache = {}

    def make_batch(row):
        return prepare_real_condition_batch(
            row, tokenizer, model, image_processor, device=device, video_cache=video_cache
        )

    losses = {}
    with torch.inference_mode():
        for condition in ('video_only', 'correct', 'mismatch', 'silence'):
            loss = condition_loss(model, make_batch(group['conditions'][condition]))
            losses[condition] = float(loss.detach().float().item())
        previous_gate = getattr(model.config, 'as_m4_audio_token_gate', 1.0)
        try:
            model.config.as_m4_audio_token_gate = 0.0
            gate0_loss = float(
                condition_loss(model, make_batch(group['conditions']['correct'])).detach().float().item()
            )
        finally:
            model.config.as_m4_audio_token_gate = previous_gate
    if any(not torch.isfinite(torch.tensor(value)) for value in (*losses.values(), gate0_loss)):
        raise RuntimeError('adapter forward smoke loss 含 NaN/Inf')
    frozen_after = adapter_frozen_runtime_sha256(model)
    adapter_after = adapter_state_sha256(model)
    gate0_abs_diff = abs(gate0_loss - losses['video_only'])
    if frozen_after != frozen_before or adapter_after != adapter_before:
        raise RuntimeError('只读 forward smoke 发生权重变化')
    if gate0_abs_diff != 0.0:
        raise RuntimeError(f'gate0 未精确回退: abs_loss_diff={gate0_abs_diff}')
    summary = {
        'status': 'PASS_ADAPTER_ONLY_REAL_FORWARD_SMOKE',
        'pair_group_id': group['pair_group_id'],
        'contract_sha256': sha256_file(contract_path),
        'manifest_sha256': sha256_file(manifest_path),
        'trainable_names': list(trainable_names),
        'condition_nll': losses,
        'gate0_correct_audio_nll': gate0_loss,
        'gate0_video_only_abs_diff': gate0_abs_diff,
        'frozen_runtime_sha256_before': frozen_before,
        'frozen_runtime_sha256_after': frozen_after,
        'adapter_sha256_before': adapter_before,
        'adapter_sha256_after': adapter_after,
        'visual_only_question_control': 'NOT_EVALUATED',
    }
    (output_dir / 'summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    return summary


def run_training(contract, contract_path: Path, manifest_path: Path, *, steps: int, device: str, output_dir: Path):
    import torch
    from intersuit.train.audio_semantic_m4_adapter import (
        adapter_frozen_runtime_sha256,
        adapter_state_sha256,
        assert_finite_adapter_parameters,
        assert_finite_adapter_gradients,
        build_adapter_only_optimizer,
        counterfactual_objective_coefficients,
        group_pilot_manifest,
        load_adapter_only_checkpoint,
        load_adapter_only_model,
        load_pilot_manifest,
        prepare_real_condition_batch,
        save_adapter_only_checkpoint,
    )

    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f'adapter pilot 输出目录非空，拒绝覆盖: {output_dir}')
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_pilot_manifest(manifest_path)
    groups = group_pilot_manifest(rows)
    if len(groups) != int(contract['data']['sample_limit_pilot']):
        raise ValueError(f"pilot group 数不匹配: expected={contract['data']['sample_limit_pilot']}, actual={len(groups)}")
    tokenizer, model, image_processor, _context_len, trainable_names = load_adapter_only_model(
        contract, device=device
    )
    optimization = contract['optimization']
    os.environ['M4_CHUNKED_LM_LOSS'] = '1' if bool(optimization.get('chunked_lm_loss', True)) else '0'
    os.environ['M4_CHUNKED_LM_LOSS_TOKENS'] = str(int(optimization.get('chunked_lm_loss_tokens', 256)))
    os.environ['M4_CHUNKED_LM_LOSS_CHECKPOINT'] = (
        '1' if bool(optimization.get('chunked_lm_loss_checkpoint', False)) else '0'
    )
    model.train()
    encoder = model.get_scene_audio_encoder()
    if isinstance(encoder, list):
        encoder = encoder[0]
    encoder.eval()
    encoder.m4_adapter.train()
    model_body = model.get_model() if hasattr(model, 'get_model') else model
    runtime = getattr(model_body, 'streaming_av_module', None)
    if isinstance(runtime, list):
        runtime = runtime[0] if runtime else None
    if runtime is not None:
        runtime.eval()
    vision_tower = model.get_vision_tower() if hasattr(model, 'get_vision_tower') else None
    if vision_tower is not None:
        vision_tower.eval()
    if bool(optimization.get('gradient_checkpointing', False)):
        if not hasattr(model, 'gradient_checkpointing_enable'):
            raise RuntimeError('合同要求 gradient checkpointing，但模型不支持')
        model.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={
                'use_reentrant': bool(optimization.get('gradient_checkpointing_use_reentrant', False))
            }
        )
        model.config.use_cache = False
    optimizer, optimizer_names = build_adapter_only_optimizer(
        model,
        learning_rate=float(optimization['learning_rate']),
        weight_decay=float(optimization['weight_decay']),
    )
    if tuple(trainable_names) != tuple(optimizer_names):
        raise RuntimeError('trainable/optimizer 参数白名单不一致')
    adapter_sha_before_training = adapter_state_sha256(model)
    if int(steps) == 20:
        expected_initial_sha = contract.get('evaluation', {}).get('baseline_lock', {}).get('adapter_initialization_sha256')
        if not expected_initial_sha or adapter_sha_before_training != expected_initial_sha:
            raise RuntimeError(
                f'20-step adapter 初始化 SHA 与 baseline 不一致: expected={expected_initial_sha}, actual={adapter_sha_before_training}'
            )
    frozen_before = adapter_frozen_runtime_sha256(model)
    margin = float(contract['objectives']['margin'])
    video_benefit_margin = float(contract['objectives'].get('video_benefit_margin', 0.0))
    video_benefit_weight = float(contract['objectives'].get('video_benefit_weight', 0.0))
    silence_anchor_weight = float(contract['objectives'].get('silence_anchor_weight', 0.0))
    max_grad_norm = float(optimization['max_grad_norm'])
    metrics = []
    video_cache = {}
    started_at = time.perf_counter()
    if device.startswith('cuda'):
        torch.cuda.reset_peak_memory_stats(torch.device(device))

    def make_batch(row):
        return prepare_real_condition_batch(
            row, tokenizer, model, image_processor, device=device, video_cache=video_cache
        )

    for step in range(1, int(steps) + 1):
        group = groups[(step - 1) % len(groups)]
        conditions = group['conditions']
        with torch.no_grad():
            detached = {
                condition: float(condition_loss(model, make_batch(conditions[condition])).detach().float().item())
                for condition in ('video_only', 'correct', 'mismatch', 'silence')
            }
        objective = counterfactual_objective_coefficients(
            detached,
            mismatch_margin=margin,
            video_benefit_margin=video_benefit_margin,
            video_benefit_weight=video_benefit_weight,
            silence_anchor_weight=silence_anchor_weight,
        )
        optimizer.zero_grad(set_to_none=True)
        correct_loss = condition_loss(model, make_batch(conditions['correct']))
        (float(objective['correct_coefficient']) * correct_loss).backward()
        mismatch_train_loss = None
        if objective['ranking_active']:
            mismatch_train_loss = condition_loss(model, make_batch(conditions['mismatch']))
            (float(objective['mismatch_coefficient']) * mismatch_train_loss).backward()
        silence_train_loss = None
        if float(objective['silence_coefficient']) != 0.0:
            silence_train_loss = condition_loss(model, make_batch(conditions['silence']))
            (float(objective['silence_coefficient']) * silence_train_loss).backward()
        gradient_norms = assert_finite_adapter_gradients(model, tuple(trainable_names))
        total_norm = torch.nn.utils.clip_grad_norm_(
            [parameter for parameter in model.parameters() if parameter.requires_grad], max_grad_norm
        )
        if not torch.isfinite(torch.as_tensor(total_norm)):
            raise RuntimeError('adapter gradient norm 非有限')
        optimizer.step()
        parameter_norms = assert_finite_adapter_parameters(model, tuple(trainable_names))
        step_metrics = {
            'step': step,
            'pair_group_id': group['pair_group_id'],
            'condition_nll_before_step': detached,
            'ranking_active': bool(objective['ranking_active']),
            'ranking_hinge_before_step': float(objective['ranking_hinge']),
            'video_benefit_active': bool(objective['video_benefit_active']),
            'video_benefit_hinge_before_step': float(objective['video_benefit_hinge']),
            'silence_anchor_abs_gap_before_step': float(objective['silence_anchor_abs_gap']),
            'correct_gradient_coefficient': float(objective['correct_coefficient']),
            'mismatch_gradient_coefficient': float(objective['mismatch_coefficient']),
            'silence_gradient_coefficient': float(objective['silence_coefficient']),
            'gradient_norms': gradient_norms,
            'total_gradient_norm_before_clip': float(torch.as_tensor(total_norm).detach().cpu().item()),
            'mismatch_gradient_forward_used': mismatch_train_loss is not None,
            'silence_gradient_forward_used': silence_train_loss is not None,
            'adapter_parameter_norms_after_step': parameter_norms,
        }
        metrics.append(step_metrics)
        print(json.dumps({'adapter_pilot_step': step_metrics}, ensure_ascii=False), flush=True)

    frozen_after = adapter_frozen_runtime_sha256(model)
    if frozen_after != frozen_before:
        raise RuntimeError(f'冻结运行模块 SHA 发生变化: before={frozen_before}, after={frozen_after}')
    adapter_sha_before_save = adapter_state_sha256(model)
    checkpoint_path = output_dir / f'checkpoint-step-{steps}.pt'
    checkpoint_sha = save_adapter_only_checkpoint(
        checkpoint_path,
        model,
        optimizer,
        step=steps,
        metadata={
            'contract_sha256': sha256_file(contract_path),
            'manifest_sha256': sha256_file(manifest_path),
            'trainable_names': list(trainable_names),
            'frozen_runtime_sha256': frozen_after,
        },
    )
    with torch.no_grad():
        for parameter in encoder.m4_adapter.parameters():
            parameter.zero_()
    load_adapter_only_checkpoint(checkpoint_path, model, optimizer)
    adapter_sha_after_restore = adapter_state_sha256(model)
    if adapter_sha_after_restore != adapter_sha_before_save:
        raise RuntimeError('adapter checkpoint 恢复 SHA 不一致')
    summary = {
        'status': f'PASS_ADAPTER_ONLY_CANARY{steps}',
        'steps': int(steps),
        'contract_sha256': sha256_file(contract_path),
        'manifest_sha256': sha256_file(manifest_path),
        'trainable_names': list(trainable_names),
        'optimizer_names': list(optimizer_names),
        'frozen_runtime_sha256_before': frozen_before,
        'frozen_runtime_sha256_after': frozen_after,
        'adapter_sha256_after_training': adapter_sha_before_save,
        'adapter_sha256_before_training': adapter_sha_before_training,
        'adapter_sha256_after_restore': adapter_sha_after_restore,
        'checkpoint_path': str(checkpoint_path),
        'checkpoint_sha256': checkpoint_sha,
        'visual_only_question_control': 'NOT_EVALUATED',
        'runtime_seconds': time.perf_counter() - started_at,
        'cuda_peak_memory_allocated_bytes': (
            int(torch.cuda.max_memory_allocated(torch.device(device))) if device.startswith('cuda') else 0
        ),
        'metrics': metrics,
    }
    (output_dir / 'summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    return summary

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--contract',type=Path,default=DEFAULT_CONTRACT)
    ap.add_argument('--manifest',type=Path,required=True)
    ap.add_argument('--steps',type=int,choices=(2,20),default=2)
    ap.add_argument('--smoke-only',action='store_true')
    ap.add_argument('--device',default='cuda:0')
    ap.add_argument('--output-dir',type=Path)
    args=ap.parse_args()
    contract=json.loads(args.contract.read_text(encoding='utf-8'))
    key='gpu_forward_smoke' if args.smoke_only else ('gpu_2step' if args.steps==2 else 'gpu_20step')
    if contract['authorization'].get(key) is not True:
        raise PermissionError(f'adapter pilot 尚未授权 {key}；停止，不加载 M4/GPU')
    if args.device != 'cuda:0':
        raise ValueError('adapter pilot 合同仅允许逻辑 cuda:0；物理 GPU 必须由 CUDA_VISIBLE_DEVICES 显式映射')
    suffix = 'forward_smoke' if args.smoke_only else f'canary{args.steps}'
    output_dir = args.output_dir or (ROOT.parent / contract['optimization']['checkpoint_dir']).parent / suffix
    if args.smoke_only:
        summary = run_forward_smoke(
            contract, args.contract.resolve(), args.manifest.resolve(),
            device=args.device, output_dir=output_dir.resolve()
        )
    else:
        summary = run_training(
            contract, args.contract.resolve(), args.manifest.resolve(),
            steps=args.steps, device=args.device, output_dir=output_dir.resolve()
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
if __name__=='__main__': main()
