import importlib.util
import json
import sys
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test_unav_beats_standalone_v1.py"
SPEC = importlib.util.spec_from_file_location("test_unav_beats_standalone_v1_script", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_split_windows_preserves_tail():
    windows, timestamps, valid = MODULE.split_windows(torch.arange(20_000, dtype=torch.float32))
    assert windows.shape == (2, 16_000)
    assert timestamps.tolist() == [[0.0, 1.0], [1.0, 1.25]]
    assert valid.tolist() == [16_000, 4_000]


def test_contract_fails_closed(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"status": "LOCKED", "permissions": {}}), encoding="utf-8")
    with pytest.raises(PermissionError):
        MODULE.validate_contract(contract, manifest)
