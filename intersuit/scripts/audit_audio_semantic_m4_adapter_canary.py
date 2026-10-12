#!/usr/bin/env python
"""独立审计 M4 音频语义 adapter-only canary checkpoint 与运行锁。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import torch


EXPECTED_ADAPTER_KEYS = ("bias", "weight")
EXPECTED_TRAINABLE_SUFFIXES = (
    "scene_audio_encoder.m4_adapter.bias",
    "scene_audio_encoder.m4_adapter.weight",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_checkpoint(path: Path) -> dict[str, Any]:
    try:
        payload = torch.load(path, map_location="cpu", weights_only=True)
    except TypeError:
        payload = torch.load(path, map_location="cpu")
    if not isinstance(payload, dict):
        raise ValueError("adapter checkpoint 必须是 dict")
    return payload


def audit_canary(
    *, checkpoint_path: Path, summary_path: Path, runtime_contract_path: Path, manifest_path: Path
) -> dict[str, Any]:
    errors: list[str] = []
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    checkpoint = load_checkpoint(checkpoint_path)
    required = {"step", "adapter_state_dict", "optimizer_state_dict", "metadata"}
    if not required <= set(checkpoint):
        errors.append(f"checkpoint schema 缺失: {sorted(required - set(checkpoint))}")
        return {"status": "FAIL_ADAPTER_CANARY_CHECKPOINT_AUDIT", "errors": errors}

    checkpoint_sha = sha256_file(checkpoint_path)
    contract_sha = sha256_file(runtime_contract_path)
    manifest_sha = sha256_file(manifest_path)
    metadata = checkpoint["metadata"]
    adapter_state = checkpoint["adapter_state_dict"]
    optimizer_state = checkpoint["optimizer_state_dict"]
    if int(checkpoint["step"]) != int(summary.get("steps", -1)):
        errors.append("checkpoint step 与 summary 不一致")
    if checkpoint_sha != summary.get("checkpoint_sha256"):
        errors.append("checkpoint SHA 与 summary 不一致")
    if contract_sha != summary.get("contract_sha256") or contract_sha != metadata.get("contract_sha256"):
        errors.append("运行时合同 SHA 不一致")
    if manifest_sha != summary.get("manifest_sha256") or manifest_sha != metadata.get("manifest_sha256"):
        errors.append("manifest SHA 不一致")
    if tuple(sorted(adapter_state)) != EXPECTED_ADAPTER_KEYS:
        errors.append(f"adapter state keys 错误: {sorted(adapter_state)}")
    expected_shapes = {"weight": (3584, 768), "bias": (3584,)}
    adapter_tensor_audit = {}
    for name, expected_shape in expected_shapes.items():
        tensor = adapter_state.get(name)
        if not torch.is_tensor(tensor):
            errors.append(f"adapter tensor 缺失: {name}")
            continue
        if tuple(tensor.shape) != expected_shape:
            errors.append(f"adapter {name} shape 错误: {tuple(tensor.shape)}")
        if tensor.dtype != torch.float32:
            errors.append(f"adapter {name} dtype 不是 FP32: {tensor.dtype}")
        if not torch.isfinite(tensor).all():
            errors.append(f"adapter {name} 含 NaN/Inf")
        adapter_tensor_audit[name] = {
            "shape": list(tensor.shape),
            "dtype": str(tensor.dtype),
            "norm": float(tensor.float().norm().item()),
        }
    trainable_names = tuple(metadata.get("trainable_names") or ())
    if len(trainable_names) != 2 or any(
        not any(name.endswith(suffix) for name in trainable_names)
        for suffix in EXPECTED_TRAINABLE_SUFFIXES
    ):
        errors.append(f"trainable names 白名单错误: {trainable_names}")
    param_groups = optimizer_state.get("param_groups") if isinstance(optimizer_state, dict) else None
    state = optimizer_state.get("state") if isinstance(optimizer_state, dict) else None
    optimizer_param_ids = [item for group in (param_groups or []) for item in group.get("params", [])]
    if len(optimizer_param_ids) != 2 or len(set(optimizer_param_ids)) != 2:
        errors.append(f"optimizer 参数数量/重复错误: {optimizer_param_ids}")
    if not isinstance(state, dict) or set(state) != set(optimizer_param_ids):
        errors.append("optimizer state 与两个 adapter 参数不一致")
    else:
        for parameter_id, item in state.items():
            for key in ("exp_avg", "exp_avg_sq"):
                tensor = item.get(key)
                if not torch.is_tensor(tensor) or tensor.dtype != torch.float32 or not torch.isfinite(tensor).all():
                    errors.append(f"optimizer state {parameter_id}/{key} 非 finite FP32")
    if summary.get("frozen_runtime_sha256_before") != summary.get("frozen_runtime_sha256_after"):
        errors.append("summary 冻结运行模块 SHA 前后不一致")
    if summary.get("adapter_sha256_after_training") != summary.get("adapter_sha256_after_restore"):
        errors.append("summary adapter 保存/恢复 SHA 不一致")

    return {
        "status": "PASS_ADAPTER_CANARY_CHECKPOINT_AUDIT" if not errors else "FAIL_ADAPTER_CANARY_CHECKPOINT_AUDIT",
        "errors": errors,
        "step": int(checkpoint["step"]),
        "checkpoint_sha256": checkpoint_sha,
        "runtime_contract_sha256": contract_sha,
        "manifest_sha256": manifest_sha,
        "adapter_tensor_audit": adapter_tensor_audit,
        "optimizer_parameter_count": len(optimizer_param_ids),
        "optimizer_state_count": len(state or {}),
        "frozen_runtime_sha_unchanged": summary.get("frozen_runtime_sha256_before") == summary.get("frozen_runtime_sha256_after"),
        "adapter_restore_sha_matches": summary.get("adapter_sha256_after_training") == summary.get("adapter_sha256_after_restore"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--runtime-contract", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_canary(
        checkpoint_path=args.checkpoint.resolve(),
        summary_path=args.summary.resolve(),
        runtime_contract_path=args.runtime_contract.resolve(),
        manifest_path=args.manifest.resolve(),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if not result["status"].startswith("PASS_"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
