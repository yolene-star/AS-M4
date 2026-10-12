from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "configs/audio_confidence_p3_feature_smoke_result_lock_v1.json"


def test_feature_smoke_result_passes_only_interface_scope():
    result = json.loads(LOCK.read_text())
    assert result["status"] == "PASS_P3_FROZEN_FEATURE_SMOKE"
    assert result["scope"]["record_count"] == 2
    assert result["scope"]["training_performed"] is False
    assert result["scope"]["formal_test_accessed"] is False
    assert result["result"]["audit_status"] == "PASS_P3_FROZEN_FEATURE_AUDIT"
    assert result["result"]["all_features_finite"] is True
    assert result["result"]["quality_supervised_windows"] == 18
    assert result["result"]["relevance_supervised_windows"] == 9
    assert [record["modality"] for record in result["result"]["records"]] == ["visual", "audio"]
    assert all(record["shape"] == [1, 9, 3584] for record in result["result"]["records"])
    assert all(len(value) == 64 for value in result["result"]["encoder_source_sha256"].values())
    assert result["runtime"]["gpu_memory_before_mib"] == result["runtime"]["gpu_memory_after_mib"]
    assert result["runtime"]["no_gpu_process_left_after_exit"] is True
    assert not any(result["authorization_after_result"].values())


def test_feature_smoke_result_records_known_visibility_limits():
    result = json.loads(LOCK.read_text())
    kernel = result["runtime"]["kernel_check"]
    assert kernel["dmesg_since_supported"] is False
    assert kernel["journal_access_complete"] is False
    assert kernel["new_xid_aer_pcie_hung_task_observed"] is False
    assert result["warnings"]
