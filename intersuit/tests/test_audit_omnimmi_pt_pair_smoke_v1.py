"""OmniMMI PT 配对 smoke 审计器的 CPU tests。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import torch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_omnimmi_pt_pair_smoke_v1.py"
SPEC = importlib.util.spec_from_file_location("audit_omnimmi_pt_pair_smoke_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def write_pair(root: Path, delta: float = 0.0):
    for name, model, value in (("original", "m4", 1.0), ("candidate", "as", 1.0 + delta)):
        directory = root / name
        (directory / "logits").mkdir(parents=True)
        config = {"created_at": name, "model_path": model, "audio_mode": "behavior_off", "max_frames": 32}
        (directory / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
        rows = []
        for index in range(2):
            logits_path = directory / "logits" / f"{index:04d}.pt"
            torch.save({"final_logits": torch.tensor([value, 2.0])}, logits_path)
            rows.append({"sample_index": index, "video": f"v{index}", "query": "q", "frame_count": 32, "frame_indices": list(range(32)), "prediction": "", "logits_path": str(logits_path)})
        (directory / "scores.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_exact_pair_passes(tmp_path):
    write_pair(tmp_path)
    result = module.audit_pair(tmp_path / "original", tmp_path / "candidate")
    assert result["status"] == "PASS_OMNIMMI_PT_LIMIT2_EXACT_BEHAVIOR_OFF_ROLLBACK"
    assert result["exact_logits_count"] == 2


def test_logit_difference_fails(tmp_path):
    write_pair(tmp_path, delta=0.5)
    result = module.audit_pair(tmp_path / "original", tmp_path / "candidate")
    assert result["status"] == "FAIL_OMNIMMI_PT_LIMIT2_BEHAVIOR_OFF_MISMATCH"
    assert result["exact_logits_count"] == 0


def test_non_model_config_difference_fails(tmp_path):
    write_pair(tmp_path)
    path = tmp_path / "candidate/run_config.json"
    config = json.loads(path.read_text())
    config["max_frames"] = 16
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="运行配置不一致"):
        module.audit_pair(tmp_path / "original", tmp_path / "candidate")
