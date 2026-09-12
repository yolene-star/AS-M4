from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path("intersuit/scripts/audit_omnimmi_m4_four_condition_contract.py").resolve()
SPEC = spec_from_file_location("audit_omnimmi_m4_four_condition_contract", SCRIPT)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_current_contract_is_safe_and_pending_review() -> None:
    result = MODULE.audit(Path("intersuit/configs/omnimmi_m4_four_condition_contract_v1.json").resolve())
    assert result["status"] == "PENDING_HUMAN_MODALITY_REVIEW"
    assert result["candidate_rows"] == 872
    assert result["checks"]["four_conditions_exact"]
    assert result["checks"]["gpu_and_training_closed"]
    assert result["prediction_files_accessed"] is False
