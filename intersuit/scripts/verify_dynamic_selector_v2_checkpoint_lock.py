#!/usr/bin/env python3
"""验证 Dynamic Selector V2 候选 checkpoint 锁文件，禁止资产静默漂移。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCK = PROJECT_ROOT / "intersuit/configs/dynamic_selector_v2_checkpoint_lock.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--skip-shards", action="store_true", help="仅做快速 schema/config/index 检查")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_hash(path: Path, expected: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"锁定资产不存在：{path}")
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"锁定资产 SHA 漂移：{path}\nexpected={expected}\nactual={actual}")


def validate_lock(payload: dict, *, project_root: Path, skip_shards: bool = False) -> dict:
    if payload.get("checkpoint_locked") is not True:
        raise ValueError("checkpoint_locked 必须为 true")
    gate_status = payload.get("audio_path_selectivity_gate_status")
    if gate_status not in {"pending", "failed", "passed"}:
        raise ValueError("必须显式记录 audio path selectivity gate 状态")
    oracle_eligible = payload.get("oracle_eligible")
    utility_allowed = payload.get("v2_oracle_utility_allowed")
    pilot_allowed = payload.get("v2_oracle_pilot_allowed")
    confirm_allowed = payload.get("v2_oracle_confirm_allowed")
    if gate_status == "failed":
        if (
            oracle_eligible != "diagnostic_only"
            or utility_allowed != "pilot_only"
            or pilot_allowed is not True
            or confirm_allowed is not False
        ):
            raise ValueError("failed gate 只允许 diagnostic_only/pilot_only，且必须禁止 confirm")
    elif gate_status == "pending":
        if any(value is not False for value in (oracle_eligible, utility_allowed, pilot_allowed, confirm_allowed)):
            raise ValueError("pending gate 必须禁止全部 V2 Oracle 权限")
    elif not (
        oracle_eligible is True
        and utility_allowed is True
        and pilot_allowed is True
        and confirm_allowed is True
    ):
        raise ValueError("passed gate 必须显式授权完整 V2 Oracle 权限")
    if payload.get("learned_selector_training_allowed") is not False:
        raise ValueError("本 lock 不得授权 learned selector training")

    checkpoint = payload["checkpoint"]
    checkpoint_dir = project_root / checkpoint["path"]
    require_hash(checkpoint_dir / "config.json", checkpoint["config_sha256"])
    index_path = checkpoint_dir / "model.safetensors.index.json"
    require_hash(index_path, checkpoint["model_index_sha256"])
    index = json.loads(index_path.read_text(encoding="utf-8"))
    keys = tuple(index.get("weight_map", {}))
    missing_prefixes = [
        prefix for prefix in payload["required_weight_prefixes"] if not any(key.startswith(prefix) for key in keys)
    ]
    if missing_prefixes:
        raise ValueError(f"checkpoint 缺少 V2 所需权重前缀：{missing_prefixes}")
    if not skip_shards:
        for filename, expected in checkpoint["model_shards"].items():
            require_hash(checkpoint_dir / filename, expected)

    evidence = payload.get("selectivity_failure_evidence")
    if gate_status == "failed":
        if not isinstance(evidence, dict):
            raise ValueError("failed gate 必须附带冻结失败证据")
        artifact_root = project_root / evidence["artifact_root"]
        require_hash(artifact_root / "matrix_plan.jsonl", evidence["matrix_plan_sha256"])
        require_hash(artifact_root / "selectivity_summary.json", evidence["summary_sha256"])
        matrix_rows = [
            json.loads(line)
            for line in (artifact_root / "matrix_plan.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not matrix_rows or any(Path(row["model_path"]).resolve() != checkpoint_dir.resolve() for row in matrix_rows):
            raise ValueError("selectivity evidence 使用的 checkpoint 与 lock 不一致")
        if any(row["manifest_sha256"] != evidence["manifest_sha256"] for row in matrix_rows):
            raise ValueError("selectivity evidence manifest SHA 不一致")
        summary = json.loads((artifact_root / "selectivity_summary.json").read_text(encoding="utf-8"))
        if summary.get("status") != "FAIL":
            raise ValueError("冻结证据不是 FAIL")
        for condition, expected_correct in (
            ("video_only", evidence["video_only"]),
            ("correct", evidence["correct"]),
            ("mismatched", evidence["mismatched"]),
        ):
            item = summary["condition_summaries"][condition]
            if item["count"] != evidence["count_per_condition"] or item["correct"] != expected_correct:
                raise ValueError(f"selectivity {condition} 汇总漂移")
        prediction_root = artifact_root / "combined_predictions"
        for condition, expected_hash in evidence["prediction_sha256"].items():
            require_hash(prediction_root / f"dev760_{condition}_predictions.jsonl", expected_hash)

    beats = payload["beats"]
    require_hash(project_root / beats["path"], beats["sha256"])
    contract = payload["runtime_contract"]
    if (contract["window_sec"], contract["hop_sec"], contract["dynamic_window_top_k"]) != (1.0, 1.0, 8):
        raise ValueError("V2 runtime contract 必须固定为 1s/1s/K=8")
    return {
        "status": "pass",
        "checkpoint": checkpoint["path"],
        "oracle_eligible": payload["oracle_eligible"],
        "audio_path_selectivity_gate_status": gate_status,
        "v2_oracle_utility_allowed": payload["v2_oracle_utility_allowed"],
        "v2_oracle_pilot_allowed": payload["v2_oracle_pilot_allowed"],
        "v2_oracle_confirm_allowed": payload["v2_oracle_confirm_allowed"],
        "shards_verified": not skip_shards,
    }


def main() -> None:
    args = parse_args()
    payload = json.loads(args.lock.read_text(encoding="utf-8"))
    print(json.dumps(validate_lock(payload, project_root=PROJECT_ROOT, skip_shards=args.skip_shards), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
