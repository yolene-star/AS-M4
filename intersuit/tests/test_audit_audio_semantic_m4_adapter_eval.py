from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from intersuit.train.audio_semantic_m4_adapter import summarize_counterfactual_nll


SCRIPT = Path(__file__).parents[1] / "scripts/audit_audio_semantic_m4_adapter_eval.py"
SPEC = importlib.util.spec_from_file_location("audit_adapter_eval", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_adapter_eval_independent_audit_detects_metric_change(tmp_path):
    rows = [
        {"pair_group_id": "g0", "condition_nll": {"video_only": 1.0, "correct": 0.8, "mismatch": 1.2, "silence": 1.1}}
    ]
    rows_path = tmp_path / "rows.jsonl"
    summary_path = tmp_path / "summary.json"
    contract_path = tmp_path / "contract.json"
    manifest_path = tmp_path / "manifest.jsonl"
    rows_path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    contract_path.write_text('{}\n', encoding="utf-8")
    manifest_path.write_text('{}\n', encoding="utf-8")
    summary = {
        "rows_sha256": MODULE.sha256_file(rows_path),
        "contract_sha256": MODULE.sha256_file(contract_path),
        "manifest_sha256": MODULE.sha256_file(manifest_path),
        "adapter_sha256_before": "a",
        "adapter_sha256_after": "a",
        "frozen_runtime_sha256_before": {"x": "b"},
        "frozen_runtime_sha256_after": {"x": "b"},
        "metrics": summarize_counterfactual_nll(rows),
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    passed = MODULE.audit_evaluation(
        summary_path=summary_path, rows_path=rows_path,
        runtime_contract_path=contract_path, manifest_path=manifest_path,
    )
    assert passed["status"] == "PASS_ADAPTER_EVAL_INDEPENDENT_AUDIT"
    summary["metrics"]["group_count"] = 2
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    failed = MODULE.audit_evaluation(
        summary_path=summary_path, rows_path=rows_path,
        runtime_contract_path=contract_path, manifest_path=manifest_path,
    )
    assert failed["status"] == "FAIL_ADAPTER_EVAL_INDEPENDENT_AUDIT"
