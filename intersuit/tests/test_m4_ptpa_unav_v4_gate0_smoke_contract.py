"""UnAV v4 到 M4 的只读 gate0 smoke 合同测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "harness/configs/m4_ptpa_unav_v4_gate0_smoke_v1.json"


def _load() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_gate0_contract_locks_non_test_sample_and_artifacts():
    contract = _load()
    dataset = contract["dataset"]
    lock = contract["v4_lock"]

    assert dataset["sample_limit"] == 1
    assert dataset["locked_first_sample_id"] == "ave_stage2_QSgqjJ2iglM_audio"
    assert dataset["test_accessed"] is False
    assert _sha256(Path(dataset["manifest"])) == dataset["manifest_sha256"]
    assert _sha256(Path(lock["semantic_projector_path"])) == lock["semantic_projector_sha256"]
    assert _sha256(Path(lock["beats_checkpoint_path"])) == lock["beats_checkpoint_sha256"]
    assert lock["m4_adapter_source"] == "existing_audio_projector"


def test_gate0_contract_is_read_only_and_has_no_correct_condition():
    contract = _load()
    permissions = contract["permissions"]
    experiments = contract["experiments"]

    assert contract["experiment_ids"] == ["V4_BEHAVIOR_OFF", "V4_GATE0"]
    assert [item["id"] for item in experiments] == contract["experiment_ids"]
    assert permissions["correct_nonzero_gate_allowed"] is False
    assert permissions["training_allowed"] is False
    assert permissions["beats_unfreeze_allowed"] is False
    assert permissions["test_access_allowed"] is False
    assert all(item["force_audio_gate"] == "0" for item in experiments)


def test_gate0_experiment_requires_locked_v4_and_existing_adapter():
    gate0 = _load()["experiments"][1]
    env = gate0["env"]

    assert env["AS_M4_SCENE_AUDIO_ENCODER_TYPE"] == "beats_v4"
    assert env["AS_M4_SCENE_AUDIO_V4_ADAPTER_SOURCE"] == "existing_audio_projector"
    assert len(env["AS_M4_SCENE_AUDIO_BEATS_CHECKPOINT_SHA256"]) == 64
    assert len(env["AS_M4_SCENE_AUDIO_V4_PROJECTOR_SHA256"]) == 64
