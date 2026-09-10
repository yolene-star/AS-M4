#!/usr/bin/env python
"""独立评估 P3 Gate-only canary 的 Router/相关性排序与 Gate 分布。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from intersuit.model.streaming_av.confidence_gate import FirstStageAudioConfidenceGate
from intersuit.train.audio_confidence import (
    FrozenFeatureManifestDataset,
    GateFeatureCollator,
    load_gate_only_checkpoint,
    PairGroupBatchSampler,
)
from intersuit.train.audio_confidence_precompute import audit_feature_manifest, sha256_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    return parser.parse_args()


@torch.inference_mode()
def main() -> None:
    args = parse_args()
    audit = audit_feature_manifest(args.feature_manifest)
    manifest = json.loads(args.feature_manifest.read_text(encoding="utf-8"))
    model = FirstStageAudioConfidenceGate(hidden_size=int(manifest["hidden_size"]), projection_dim=128)
    router_parameters = list(model.question_router.parameters())
    router_ids = {id(parameter) for parameter in router_parameters}
    base_parameters = [parameter for parameter in model.parameters() if id(parameter) not in router_ids]
    optimizer = torch.optim.AdamW(
        [{"params": base_parameters, "lr": 1e-4}, {"params": router_parameters, "lr": 5e-2}]
    )
    step = load_gate_only_checkpoint(args.checkpoint, model, optimizer, map_location="cpu")
    model.to(args.device).eval()
    dataset = FrozenFeatureManifestDataset(args.feature_manifest)
    sampler = PairGroupBatchSampler(dataset.records, shuffle=False, seed=0)
    base_collator = GateFeatureCollator()
    def collate_with_metadata(samples):
        result = base_collator(samples)
        result["record_id"] = [str(sample["metadata"]["record_id"]) for sample in samples]
        result["condition"] = [str(sample["metadata"]["condition"]) for sample in samples]
        result["modality"] = [str(sample["metadata"]["modality"]) for sample in samples]
        return result
    loader = DataLoader(dataset, batch_sampler=sampler, collate_fn=collate_with_metadata, num_workers=0)
    rows = []
    for batch in loader:
        tensor_batch = {key: value.to(args.device) if torch.is_tensor(value) else value for key, value in batch.items()}
        signal_values = tensor_batch.get("signal_features")
        signal = None
        if signal_values:
            from intersuit.model.streaming_av.confidence_gate import AudioSignalFeatures
            signal = AudioSignalFeatures(**{key: value.to(args.device) for key, value in signal_values.items()})
        output = model(tensor_batch["audio"], tensor_batch["video"], tensor_batch["question"], signal_features=signal, valid_mask=tensor_batch["valid_mask"])
        gates = output.gate.detach().cpu()
        relevance = output.relevance.detach().cpu()
        router = output.task_audio_need.detach().cpu()
        for index, record in enumerate(batch["record_id"]):
            valid = batch["valid_mask"][index].bool()
            rows.append({
                "record_id": str(record),
                "pair_group_id": str(batch["pair_group_id"][index]),
                "condition": str(batch["condition"][index]),
                "modality": str(batch["modality"][index]),
                "router_target": float(batch["router_target"][index].reshape(-1)[0]),
                "router": float(router[index].reshape(-1)[0]),
                "relevance": float(relevance[index][valid].mean()),
                "gate_mean": float(gates[index][valid].mean()),
                "gate_std": float(gates[index][valid].std(unbiased=False)),
            })
    correct = [r for r in rows if r["condition"] == "clean_correct" and r["modality"] in {"audio", "audio_visual"}]
    mismatch = [r for r in rows if r["condition"] == "mismatch" and r["modality"] in {"audio", "audio_visual"}]
    mismatch_by_group = {(r["pair_group_id"], r["modality"]): r for r in mismatch}
    pairs = [(r, mismatch_by_group[(r["pair_group_id"], r["modality"])])
             for r in correct if (r["pair_group_id"], r["modality"]) in mismatch_by_group]
    ranking_accuracy = sum(a["relevance"] > b["relevance"] for a, b in pairs) / len(pairs) if pairs else 0.0
    positives = [r for r in rows if r["router_target"] >= 0.5]
    negatives = [r for r in rows if r["router_target"] < 0.5]
    pos_acc = sum(r["router"] >= 0.5 for r in positives) / len(positives) if positives else 0.0
    neg_acc = sum(r["router"] < 0.5 for r in negatives) / len(negatives) if negatives else 0.0
    gate_values = [r["gate_mean"] for r in rows]
    result = {
        "status": "PASS_P3_GATE_ONLY_CANARY20_METRICS_AUDIT",
        "checkpoint_step": step,
        "feature_manifest_sha256": sha256_file(args.feature_manifest),
        "checkpoint_sha256": sha256_file(args.checkpoint),
        "record_count": len(rows),
        "correct_mismatch_pairs": len(pairs),
        "correct_mismatch_ranking_accuracy": ranking_accuracy,
        "router_balanced_accuracy": 0.5 * (pos_acc + neg_acc),
        "router_positive_accuracy": pos_acc,
        "router_negative_accuracy": neg_acc,
        "gate_min": min(gate_values),
        "gate_max": max(gate_values),
        "gate_mean": sum(gate_values) / len(gate_values),
        "gate_nonconstant": len({round(value, 8) for value in gate_values}) > 1,
        "feature_audit": audit,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
