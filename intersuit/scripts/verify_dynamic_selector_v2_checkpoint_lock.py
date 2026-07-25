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
    if payload.get("oracle_eligible") is not False:
        raise ValueError("selectivity 门禁前 oracle_eligible 必须为 false")
    if payload.get("blocked_pending_audio_path_selectivity_gate") is not True:
        raise ValueError("必须显式记录 audio path selectivity blocker")

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

    beats = payload["beats"]
    require_hash(project_root / beats["path"], beats["sha256"])
    contract = payload["runtime_contract"]
    if (contract["window_sec"], contract["hop_sec"], contract["dynamic_window_top_k"]) != (1.0, 1.0, 8):
        raise ValueError("V2 runtime contract 必须固定为 1s/1s/K=8")
    return {
        "status": "pass",
        "checkpoint": checkpoint["path"],
        "oracle_eligible": payload["oracle_eligible"],
        "shards_verified": not skip_shards,
    }


def main() -> None:
    args = parse_args()
    payload = json.loads(args.lock.read_text(encoding="utf-8"))
    print(json.dumps(validate_lock(payload, project_root=PROJECT_ROOT, skip_shards=args.skip_shards), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
