from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import torch


SCRIPT = Path(__file__).parents[1] / "scripts/audit_audio_semantic_m4_adapter_canary.py"
SPEC = importlib.util.spec_from_file_location("audit_adapter_canary", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_adapter_canary_checkpoint_audit_passes_and_rejects_dtype(tmp_path):
    contract = tmp_path / "contract.json"
    manifest = tmp_path / "manifest.jsonl"
    checkpoint_path = tmp_path / "checkpoint.pt"
    summary_path = tmp_path / "summary.json"
    contract.write_text('{"x":1}\n', encoding="utf-8")
    manifest.write_text('{"x":1}\n', encoding="utf-8")
    optimizer = torch.optim.AdamW(
        [torch.nn.Parameter(torch.zeros(3584)), torch.nn.Parameter(torch.zeros(3584, 768))]
    )
    for parameter in optimizer.param_groups[0]["params"]:
        parameter.grad = torch.ones_like(parameter)
    optimizer.step()
    payload = {
        "step": 2,
        "adapter_state_dict": {
            "bias": torch.zeros(3584),
            "weight": torch.zeros(3584, 768),
        },
        "optimizer_state_dict": optimizer.state_dict(),
        "metadata": {
            "contract_sha256": MODULE.sha256_file(contract),
            "manifest_sha256": MODULE.sha256_file(manifest),
            "trainable_names": [
                "model.scene_audio_encoder.m4_adapter.bias",
                "model.scene_audio_encoder.m4_adapter.weight",
            ],
        },
    }
    torch.save(payload, checkpoint_path)
    summary = {
        "steps": 2,
        "checkpoint_sha256": MODULE.sha256_file(checkpoint_path),
        "contract_sha256": MODULE.sha256_file(contract),
        "manifest_sha256": MODULE.sha256_file(manifest),
        "frozen_runtime_sha256_before": {"x": "a"},
        "frozen_runtime_sha256_after": {"x": "a"},
        "adapter_sha256_after_training": "b",
        "adapter_sha256_after_restore": "b",
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    result = MODULE.audit_canary(
        checkpoint_path=checkpoint_path,
        summary_path=summary_path,
        runtime_contract_path=contract,
        manifest_path=manifest,
    )
    assert result["status"] == "PASS_ADAPTER_CANARY_CHECKPOINT_AUDIT"
    payload["adapter_state_dict"]["weight"] = payload["adapter_state_dict"]["weight"].bfloat16()
    torch.save(payload, checkpoint_path)
    summary["checkpoint_sha256"] = MODULE.sha256_file(checkpoint_path)
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    failed = MODULE.audit_canary(
        checkpoint_path=checkpoint_path,
        summary_path=summary_path,
        runtime_contract_path=contract,
        manifest_path=manifest,
    )
    assert failed["status"] == "FAIL_ADAPTER_CANARY_CHECKPOINT_AUDIT"
    assert any("dtype" in error for error in failed["errors"])
