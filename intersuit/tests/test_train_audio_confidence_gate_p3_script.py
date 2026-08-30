from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/train_audio_confidence_gate_p3.py"
CONTRACT = ROOT / "configs/audio_confidence_gate_p3_contract_v1.json"


def test_gate_train_script_is_fail_closed_before_feature_or_gpu_access():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--contract", str(CONTRACT), "--steps", "2"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    contract = json.loads(CONTRACT.read_text())
    assert contract["authorization"]["two_step_training"] is False
    assert completed.returncode != 0
    assert "尚未授权 two_step_training" in completed.stderr
