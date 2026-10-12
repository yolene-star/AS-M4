"""CPU fusion 模式比较测试。"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_cpu_comparison_preserves_identity_and_bypass(tmp_path):
    import subprocess, sys
    out = tmp_path / "result.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/compare_m4_ptpa_unav_v4_fusion_modes_cpu_v1.py"), "--output", str(out)], check=True)
    data = json.loads(out.read_text())
    assert data["status"] == "PASS_CPU_FUSION_MODE_COMPARISON"
    assert data["modes"]["identity_fusion"]["relative_mean_difference"] >= 0.5
    assert data["modes"]["bypass_old_fusion"]["mean_cosine"] <= 0.95
    assert data["modes"]["lightweight_norm_preserving"]["relative_mean_difference"] >= 0.5
    assert data["modes"]["old_fusion_reference"]["mean_cosine"] > 0.99
    assert all(data["gate0_exact_zero"].values())
