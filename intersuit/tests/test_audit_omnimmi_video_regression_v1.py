"""OmniMMI 视频回归合同审计器的 CPU tests。"""

from __future__ import annotations

import importlib.util
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_omnimmi_video_regression_v1.py"
SPEC = importlib.util.spec_from_file_location("audit_omnimmi_video_regression_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def real_contract():
    return module.read_json(ROOT / "harness/configs/omnimmi_video_regression_v1.json")


def test_real_contract_passes_and_reuses_base_shards():
    result = module.audit_contract(real_contract())
    assert result["status"] == "PASS_OMNIMMI_VIDEO_REGRESSION_LIMIT2_SMOKE_FULL_CLOSED"
    assert result["base_shards_reused_exactly"] is True
    assert result["manual_modality_labels_required"] is False


def test_rejects_manual_modality_label_requirement():
    contract = deepcopy(real_contract())
    contract["manual_modality_labels_required"] = True
    with pytest.raises(ValueError, match="不得要求人工模态标签"):
        module.audit_contract(contract)


def test_rejects_changed_noninferiority_margin():
    contract = deepcopy(real_contract())
    contract["gates"]["standard_audio_noninferiority_margin"]["pt_official_accuracy_delta_min"] = -0.05
    with pytest.raises(ValueError, match="非劣容差"):
        module.audit_contract(contract)


def test_rejects_audio_improvement_claim():
    contract = deepcopy(real_contract())
    contract["gates"]["audio_improvement_claim_allowed"] = True
    with pytest.raises(ValueError, match="音频提升宣称"):
        module.audit_contract(contract)
