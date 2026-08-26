"""UnAV-100官方多卷归档断点下载器测试。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/download_m4_ptpa_unav_archive_v1.py"
SPEC = importlib.util.spec_from_file_location("download_m4_ptpa_unav_archive_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_real_contract_authorizes_exactly_13_archive_parts():
    contract = json.loads(
        (ROOT / "configs/m4_ptpa_unav_s1_reconstruction_v1.json").read_text()
    )
    files = module.validate_contract(contract)
    assert len(files) == 13
    assert sum(row["size"] for row in files) == 65_775_604_313
    assert contract["permissions"]["archive_extraction_allowed"] is False
    assert contract["permissions"]["feature_extraction_allowed"] is False
    assert contract["permissions"]["test_effect_access_allowed"] is False


def test_download_part_uses_resume_and_atomic_verified_commit(monkeypatch, tmp_path):
    content = b"archive"
    item = {
        "name": "part_00",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    calls = []

    def fake_run(command, check=False):
        calls.append(command)
        target = Path(command[command.index("--output") + 1])
        target.write_bytes(content)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.download_part(item, "revision", tmp_path, Path("/bin/true"), 2)

    assert result["status"] == "downloaded_verified"
    assert (tmp_path / "part_00").read_bytes() == content
    assert not (tmp_path / "part_00.partial").exists()
    assert "--continue-at" in calls[0]
    assert calls[0][calls[0].index("--continue-at") + 1] == "-"
    assert "--retry-connrefused" in calls[0]
    assert "--retry-all-errors" not in calls[0]

    reused = module.download_part(item, "revision", tmp_path, Path("/bin/true"), 2)
    assert reused["status"] == "reused_verified"
    assert len(calls) == 1


def test_download_part_with_retries_resumes_after_connection_close(monkeypatch, tmp_path):
    outcomes = [
        {"name": "part_01", "status": "download_failed", "partial_bytes": 123},
        {"name": "part_01", "status": "downloaded_verified", "size": 456},
    ]
    sleeps = []

    monkeypatch.setattr(module, "download_part", lambda *args, **kwargs: outcomes.pop(0))
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    result = module.download_part_with_retries(
        {"name": "part_01"}, "revision", tmp_path, Path("/bin/true"),
        retries=2, part_attempts=3, retry_delay=7,
    )

    assert result["status"] == "downloaded_verified"
    assert result["part_attempt"] == 2
    assert sleeps == [7]
