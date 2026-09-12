#!/usr/bin/env python3
"""审计 OmniMMI/M4 四条件合同的 CPU 阶段状态。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = ROOT / "intersuit/configs/omnimmi_m4_four_condition_contract_v1.json"
DEFAULT_OUTPUT = ROOT / "intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/contract_audit.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit(contract_path: Path) -> dict:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    expected_conditions = {"correct_audio", "mismatch_audio", "video_only", "silence"}
    checks = {
        "mainline_is_unav_omnimmi": "UnAV-100" in contract.get("mainline", "")
        and "OmniMMI/M4" in contract.get("mainline", ""),
        "tasks_are_pt_pa": set(contract.get("tasks", [])) == {"pt", "pa"},
        "four_conditions_exact": set(contract.get("conditions", [])) == expected_conditions,
        "prediction_blind_review": contract.get("review", {}).get("prediction_blind") is True,
        "formal_labels_not_frozen": contract.get("review", {}).get("formal_labels_frozen") is False,
        "gpu_and_training_closed": all(
            contract.get("authorization", {}).get(key) is False
            for key in ("gpu_smoke", "gpu_canary", "full_evaluation", "training_150step", "formal_training", "new_model_structure")
        ),
    }
    manifest_path = ROOT / contract["review"]["candidate_manifest"]
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    checks.update({
        "candidate_manifest_nonempty": bool(rows),
        "all_rows_pending_review": all(row.get("review_status") == "pending_human_review" for row in rows),
        "no_formal_label_leak": all(row.get("final_modality") is None for row in rows),
        "review_ids_unique": len({row["review_id"] for row in rows}) == len(rows),
    })
    counts = Counter((row["task"], row["auto_candidate"]) for row in rows)
    status = "PENDING_HUMAN_MODALITY_REVIEW" if all(checks.values()) else "FAIL_OMNIMMI_CONTRACT_PREFLIGHT"
    return {
        "status": status,
        "checks": checks,
        "contract_sha256": sha256(contract_path),
        "candidate_manifest_sha256": sha256(manifest_path),
        "candidate_rows": len(rows),
        "task_candidate_counts": {f"{task}:{label}": value for (task, label), value in sorted(counts.items())},
        "prediction_files_accessed": False,
        "gpu_authorized": False,
        "next_gate": "human_review_then_final_manifest_freeze",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = audit(args.contract.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"].startswith("FAIL"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
