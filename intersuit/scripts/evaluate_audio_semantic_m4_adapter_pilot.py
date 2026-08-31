#!/usr/bin/env python
"""评估 adapter-only pilot 的 30 组四条件 teacher-forced NLL。"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "configs/audio_semantic_m4_adapter_only_pilot_v1.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_evaluation(
    contract: dict,
    contract_path: Path,
    manifest_path: Path,
    *,
    device: str,
    output_dir: Path,
    checkpoint_path: Path | None = None,
):
    import torch
    from intersuit.train.audio_semantic_m4_adapter import (
        adapter_frozen_runtime_sha256,
        adapter_state_sha256,
        group_pilot_manifest,
        load_adapter_only_checkpoint,
        load_adapter_only_model,
        load_pilot_manifest,
        prepare_real_condition_batch,
        summarize_counterfactual_nll,
        m4_teacher_forced_forward,
    )

    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"adapter 评估输出目录非空，拒绝覆盖: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    groups = group_pilot_manifest(load_pilot_manifest(manifest_path))
    expected_groups = int(contract["data"]["sample_limit_pilot"])
    if len(groups) != expected_groups:
        raise ValueError(f"评估组数错误: expected={expected_groups}, actual={len(groups)}")
    tokenizer, model, image_processor, _context_len, trainable_names = load_adapter_only_model(
        contract, device=device
    )
    checkpoint_sha = None
    checkpoint_metadata = None
    if checkpoint_path is not None:
        checkpoint_sha = sha256_file(checkpoint_path)
        checkpoint_lock = contract.get("evaluation", {}).get("post_checkpoint_lock", {})
        if checkpoint_sha != checkpoint_lock.get("sha256"):
            raise ValueError(
                f"评估 checkpoint SHA 与合同不一致: expected={checkpoint_lock.get('sha256')}, actual={checkpoint_sha}"
            )
        payload = load_adapter_only_checkpoint(checkpoint_path, model)
        checkpoint_metadata = payload["metadata"]
        if int(payload.get("step", -1)) != int(checkpoint_lock.get("step", -2)):
            raise ValueError("评估 checkpoint step 与合同不一致")
        if checkpoint_metadata.get("manifest_sha256") != sha256_file(manifest_path):
            raise ValueError("评估 checkpoint 的 manifest SHA 不匹配")
    model.eval()
    frozen_before = adapter_frozen_runtime_sha256(model)
    adapter_before = adapter_state_sha256(model)
    started_at = time.perf_counter()
    if device.startswith("cuda"):
        torch.cuda.reset_peak_memory_stats(torch.device(device))
    rows = []
    with torch.inference_mode():
        for index, group in enumerate(groups, start=1):
            video_cache = {}
            losses = {}
            for condition in ("video_only", "correct", "mismatch", "silence"):
                batch = prepare_real_condition_batch(
                    group["conditions"][condition],
                    tokenizer,
                    model,
                    image_processor,
                    device=device,
                    video_cache=video_cache,
                )
                output = m4_teacher_forced_forward(model, **batch)
                losses[condition] = float(output.loss.detach().float().item())
            row = {"pair_group_id": group["pair_group_id"], "condition_nll": losses}
            rows.append(row)
            print(json.dumps({"adapter_eval_progress": index, "total": len(groups), **row}, ensure_ascii=False), flush=True)
            video_cache.clear()
    metrics = summarize_counterfactual_nll(rows)
    frozen_after = adapter_frozen_runtime_sha256(model)
    adapter_after = adapter_state_sha256(model)
    if frozen_after != frozen_before or adapter_after != adapter_before:
        raise RuntimeError("adapter 评估发生权重变化")
    rows_path = output_dir / "rows.jsonl"
    rows_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    summary = {
        "status": "PASS_ADAPTER_BASELINE_EVAL" if checkpoint_path is None else "PASS_ADAPTER_CHECKPOINT_EVAL",
        "evaluation_kind": "initialization" if checkpoint_path is None else "checkpoint",
        "contract_sha256": sha256_file(contract_path),
        "manifest_sha256": sha256_file(manifest_path),
        "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
        "checkpoint_sha256": checkpoint_sha,
        "checkpoint_metadata": checkpoint_metadata,
        "trainable_names": list(trainable_names),
        "adapter_sha256_before": adapter_before,
        "adapter_sha256_after": adapter_after,
        "frozen_runtime_sha256_before": frozen_before,
        "frozen_runtime_sha256_after": frozen_after,
        "rows_sha256": sha256_file(rows_path),
        "runtime_seconds": time.perf_counter() - started_at,
        "cuda_peak_memory_allocated_bytes": (
            int(torch.cuda.max_memory_allocated(torch.device(device))) if device.startswith("cuda") else 0
        ),
        "metrics": metrics,
        "visual_only_question_control": "NOT_EVALUATED",
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    authorization_key = "gpu_checkpoint_eval" if args.checkpoint else "gpu_baseline_eval"
    if contract["authorization"].get(authorization_key) is not True:
        raise PermissionError(f"adapter pilot 尚未授权 {authorization_key}；停止，不加载 M4/GPU")
    if args.device != "cuda:0":
        raise ValueError("adapter 评估只允许逻辑 cuda:0；物理卡由 CUDA_VISIBLE_DEVICES 显式映射")
    result = run_evaluation(
        contract,
        args.contract.resolve(),
        args.manifest.resolve(),
        device=args.device,
        output_dir=args.output_dir.resolve(),
        checkpoint_path=args.checkpoint.resolve() if args.checkpoint else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
