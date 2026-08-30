#!/usr/bin/env python
"""消费已审计冻结特征，执行 P3 Gate-only 2-step canary。

当前机器合同的 two_step_training 仍为 false，因此脚本默认 fail closed。20/150-step
需要各自的指标门禁实现与单独授权，不由本入口提前开放。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from intersuit.model.streaming_av.confidence_gate import FirstStageAudioConfidenceGate
from intersuit.train.audio_confidence import (
    FrozenFeatureManifestDataset,
    GateFeatureCollator,
    PairGroupBatchSampler,
    gate_only_train_step,
    save_gate_only_checkpoint,
)
from intersuit.train.audio_confidence_precompute import audit_feature_manifest, sha256_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/audio_confidence_gate_p3_contract_v1.json"
DEFAULT_FEATURE_MANIFEST = (
    INTERSUIT_ROOT
    / "datasets/VALOR32K_AVQA_V2_AUDIT/p3/frozen_features_v1/feature_manifest.json"
)
DEFAULT_OUTPUT = INTERSUIT_ROOT / "checkpoints/AS-M4-AudioConfidenceGate-P3-canary2-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--feature-manifest", type=Path, default=DEFAULT_FEATURE_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--steps", type=int, choices=(2,), default=2)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=20260830)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract["authorization"].get("two_step_training") is not True:
        raise PermissionError("P3 contract 尚未授权 two_step_training；停止，不加载特征或 GPU")
    if args.device != "cuda:0":
        raise ValueError("P3 2-step 正式入口只接受逻辑 cuda:0（物理 GPU2）")
    import os
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "2":
        raise RuntimeError("P3 2-step 必须显式设置 CUDA_VISIBLE_DEVICES=2")
    audit = audit_feature_manifest(args.feature_manifest)
    if audit["status"] != "PASS_P3_FROZEN_FEATURE_AUDIT":
        raise RuntimeError("冻结特征独立审计未通过")
    manifest = json.loads(args.feature_manifest.read_text(encoding="utf-8"))
    hidden_size = int(manifest["hidden_size"])
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    model = FirstStageAudioConfidenceGate(hidden_size=hidden_size, projection_dim=128).to(args.device)
    expected_tensors = contract["trainable_scope"][
        "expected_parameter_tensor_count_hidden3584_projection128"
    ]
    expected_parameters = contract["trainable_scope"][
        "expected_parameter_count_hidden3584_projection128"
    ]
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    if hidden_size != 3584 or len(trainable) != expected_tensors or sum(p.numel() for p in trainable) != expected_parameters:
        raise RuntimeError("Gate 参数白名单数量与 P3 合同不一致")
    dataset = FrozenFeatureManifestDataset(args.feature_manifest)
    sampler = PairGroupBatchSampler(dataset.records, shuffle=True, seed=args.seed)
    loader = DataLoader(
        dataset, batch_sampler=sampler, collate_fn=GateFeatureCollator(), num_workers=0
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    start = datetime.now(timezone.utc).astimezone().isoformat()
    run_config = {
        "start_time": start,
        "steps": args.steps,
        "seed": args.seed,
        "device": args.device,
        "physical_gpu": 2,
        "learning_rate": args.learning_rate,
        "feature_manifest_sha256": sha256_file(args.feature_manifest),
        "feature_audit": audit,
        "contract_sha256": sha256_file(args.contract),
    }
    (args.output_dir / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    iterator = iter(loader)
    history = []
    for step in range(1, args.steps + 1):
        try:
            batch = next(iterator)
        except StopIteration:
            iterator = iter(loader)
            batch = next(iterator)
        loss, metrics = gate_only_train_step(model, batch, optimizer, device=args.device)
        history.append({
            "step": step,
            "loss": float(loss.cpu()),
            **{name: float(value.cpu()) for name, value in metrics.items()},
        })
    checkpoint = args.output_dir / "gate-only-checkpoint.pt"
    save_gate_only_checkpoint(
        checkpoint, model, optimizer, step=args.steps,
        metadata={"feature_manifest_sha256": run_config["feature_manifest_sha256"]},
    )
    report = {
        "status": "PASS_P3_GATE_ONLY_CANARY2",
        "history": history,
        "checkpoint": checkpoint.name,
        "checkpoint_sha256": sha256_file(checkpoint),
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
