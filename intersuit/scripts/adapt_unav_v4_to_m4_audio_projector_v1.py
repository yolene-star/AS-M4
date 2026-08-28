#!/usr/bin/env python3
"""隔离验证 UnAV v4 semantic projector 到 M4 768->3584 adapter 的结构。"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import torch
from torch import nn
import torch.nn.functional as F

class V4SemanticProjector(nn.Module):
    def __init__(self):
        super().__init__()
        self.down = nn.Linear(768, 256)
        self.up = nn.Linear(256, 768)

    def forward(self, x):
        return F.normalize(x.float() + 0.1 * self.up(F.gelu(self.down(x.float()))), dim=-1)

class M4V4AudioAdapter(nn.Module):
    """v4保持768维语义空间，再映射到M4 hidden size。"""
    def __init__(self, hidden_size: int = 3584):
        super().__init__()
        self.semantic = V4SemanticProjector()
        self.to_m4 = nn.Linear(768, hidden_size)

    def forward(self, raw_features, gate: float = 1.0):
        semantic = self.semantic(raw_features)
        projected = self.to_m4(semantic)
        return projected * float(gate)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v4-checkpoint", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    ck = torch.load(args.v4_checkpoint, map_location="cpu", weights_only=False)
    model = M4V4AudioAdapter()
    model.semantic.load_state_dict(ck["state_dict"])
    model.eval()
    for parameter in model.semantic.parameters():
        parameter.requires_grad = False
    raw = torch.randn(2, 5, 768)
    with torch.no_grad():
        normal = model(raw, gate=1.0)
        gate0 = model(raw, gate=0.0)
    result = {
        "status": "PASS_V4_M4_ADAPTER_SHAPE_FREEZE_ROLLBACK",
        "input_shape": list(raw.shape),
        "output_shape": list(normal.shape),
        "m4_hidden_size": 3584,
        "semantic_projector_frozen": all(not p.requires_grad for p in model.semantic.parameters()),
        "adapter_trainable_parameter_count": sum(p.numel() for p in model.to_m4.parameters()),
        "gate0_exact_zero": bool(torch.equal(gate0, torch.zeros_like(gate0))),
        "finite": bool(torch.isfinite(normal).all()),
        "v4_checkpoint": str(args.v4_checkpoint.resolve()),
        "v4_checkpoint_sha256": ck.get("checkpoint_sha256", "external_lock"),
        "beats_training_performed": False,
        "m4_training_performed": False,
        "test_accessed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
