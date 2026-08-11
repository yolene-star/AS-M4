from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_m4_ptpa_selector_v3_p3_outputs.sh"
LAUNCHER = ROOT / "scripts" / "run_m4_ptpa_selector_v3_p3_canary.sh"


def test_p3_checker_skips_conda_blank_lines_and_launcher_delegates_to_it():
    checker = CHECKER.read_text(encoding="utf-8")
    launcher = LAUNCHER.read_text(encoding="utf-8")

    assert '[[ -z "$condition" ]] && continue' in checker
    assert "condition_count" in checker
    assert CHECKER.name in launcher


def test_p3_shell_scripts_pass_bash_syntax_check():
    for script in (CHECKER, LAUNCHER):
        subprocess.run(["bash", "-n", str(script)], check=True)
