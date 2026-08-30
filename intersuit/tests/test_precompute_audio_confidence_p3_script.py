from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/precompute_audio_confidence_p3_features.py"
CONTRACT = ROOT / "configs/audio_confidence_gate_p3_contract_v1.json"


def test_precompute_script_is_fail_closed_before_model_load():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--contract", str(CONTRACT), "--limit", "1"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    contract = json.loads(CONTRACT.read_text())
    assert contract["authorization"]["feature_precompute"] is False
    assert completed.returncode != 0
    assert "尚未授权 feature_precompute" in completed.stderr
