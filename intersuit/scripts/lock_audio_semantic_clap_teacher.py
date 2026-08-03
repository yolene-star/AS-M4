#!/usr/bin/env python
"""锁定官方 CLAP 教师资产、环境和候选选择账本。"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch
import transformers
from transformers import ClapModel, ClapProcessor


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
REVISION = "8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a"
MODEL_ID = "laion/clap-htsat-unfused"
SNAPSHOT = (
    INTERSUIT_ROOT
    / "assets/hf_cache/hub/models--laion--clap-htsat-unfused/snapshots"
    / REVISION
)
OUTPUT_ROOT = (
    INTERSUIT_ROOT / "harness/artifacts/audio_semantic_teacher_t0"
)

REMOTE_FILES = {
    ".gitattributes": {
        "size": 1477,
        "git_blob": "c7d9f3332a950355d5a77d85000f05e6f45435ea",
    },
    "README.md": {
        "size": 4454,
        "git_blob": "b866f5bee496710d19b98792fc0f68481b181f2a",
    },
    "config.json": {
        "size": 5390,
        "git_blob": "70624c940dc3d35b36bc9de57ee5a958c86e032a",
    },
    "merges.txt": {
        "size": 456356,
        "git_blob": "6636bda4a1fd7a63653dffb22683b8162c8de956",
    },
    "preprocessor_config.json": {
        "size": 541,
        "git_blob": "e8636819eeb1b19f1e5b6f572eca1a101144062e",
    },
    "pytorch_model.bin": {
        "size": 614525833,
        "lfs_sha256": (
            "1cd3c601bc4afe0fa87be3de4c13dd2cfadd249fac1e29acf74a9b296c3219bb"
        ),
    },
    "special_tokens_map.json": {
        "size": 280,
        "git_blob": "d5698132694f4f1bcff08fa7d937b1701812598e",
    },
    "tokenizer.json": {
        "size": 2108746,
        "git_blob": "99f518e1ee65361b4d772c6f805508dbf30cfd8b",
    },
    "tokenizer_config.json": {
        "size": 384,
        "git_blob": "058e2e071e2a76af9dc9a10940053477ce8329d3",
    },
    "vocab.json": {
        "size": 798293,
        "git_blob": "4ebe4bb3f3114daf2e4cc349f24873a1175a35d7",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPO_ROOT))


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def validate_snapshot() -> dict[str, dict[str, Any]]:
    actual_names = {path.name for path in SNAPSHOT.iterdir() if path.is_file()}
    if actual_names != set(REMOTE_FILES):
        raise ValueError(
            f"snapshot 文件集合不一致：{sorted(actual_names)}"
        )
    result = {}
    for name, expected in REMOTE_FILES.items():
        path = SNAPSHOT / name
        size = path.stat().st_size
        if size != expected["size"]:
            raise ValueError(f"{name} 大小不一致：{size}")
        item = {"size": size, "sha256": sha256(path)}
        if "git_blob" in expected:
            actual_blob = git_blob_sha1(path)
            if actual_blob != expected["git_blob"]:
                raise ValueError(f"{name} Git blob SHA1 不一致")
            item["git_blob_sha1"] = actual_blob
        else:
            if item["sha256"] != expected["lfs_sha256"]:
                raise ValueError(f"{name} LFS SHA256 不一致")
            item["lfs_sha256_verified"] = True
        result[name] = item
    return result


def transformers_code_lock() -> dict[str, str]:
    package_root = Path(transformers.__file__).resolve().parent
    paths = [
        package_root / "models/clap/configuration_clap.py",
        package_root / "models/clap/feature_extraction_clap.py",
        package_root / "models/clap/modeling_clap.py",
        package_root / "models/clap/processing_clap.py",
    ]
    result = {}
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        result[str(path)] = sha256(path)
    return result


def update_ledger(asset_lock_sha: str) -> dict[str, Any]:
    path = OUTPUT_ROOT / "candidate_selection_ledger.json"
    if path.exists():
        ledger = read_json(path)
    else:
        ledger = {"schema_version": 1, "attempts": []}
    attempt_id = f"{MODEL_ID}@{REVISION}"
    existing = [
        item for item in ledger["attempts"] if item["attempt_id"] == attempt_id
    ]
    if existing:
        if len(existing) != 1:
            raise ValueError("candidate ledger 出现重复 attempt_id")
        if existing[0]["teacher_asset_lock_sha256"] != asset_lock_sha:
            raise ValueError("candidate ledger 已有记录与当前资产锁冲突")
        return ledger
    ledger["attempts"].append(
        {
            "attempt_id": attempt_id,
            "model_id": MODEL_ID,
            "official_source": f"https://huggingface.co/{MODEL_ID}",
            "preferred_download_entry": (
                f"https://hf-mirror.com/{MODEL_ID}/resolve/{REVISION}/"
            ),
            "revision": REVISION,
            "license": "apache-2.0",
            "status": "selected_for_t0_qualification",
            "teacher_asset_lock_sha256": asset_lock_sha,
            "selection_data_accessed": [],
            "validation_accessed": False,
            "test_locked_accessed": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    write_json(path, ledger)
    return ledger


def run() -> dict[str, Any]:
    files = validate_snapshot()
    processor = ClapProcessor.from_pretrained(
        SNAPSHOT, local_files_only=True
    )
    model = ClapModel.from_pretrained(
        SNAPSHOT, local_files_only=True, trust_remote_code=False
    ).eval()
    model.requires_grad_(False)
    trainable = sum(
        parameter.numel()
        for parameter in model.parameters()
        if parameter.requires_grad
    )
    if trainable != 0:
        raise ValueError("教师冻结失败")

    code_files = transformers_code_lock()
    code_digest = hashlib.sha256()
    for path, digest in sorted(code_files.items()):
        code_digest.update(f"{path}\0{digest}\n".encode())

    asset_lock = {
        "schema_version": 1,
        "status": "PASS",
        "teacher": {
            "model_id": MODEL_ID,
            "official_source": f"https://huggingface.co/{MODEL_ID}",
            "preferred_download_endpoint": "https://hf-mirror.com",
            "revision": REVISION,
            "license": "apache-2.0",
            "private": False,
            "gated": False,
            "architecture": type(model).__name__,
            "trust_remote_code": False,
            "snapshot_path": str(SNAPSHOT),
            "files": files,
        },
        "model_contract": {
            "parameter_count": sum(
                parameter.numel() for parameter in model.parameters()
            ),
            "trainable_parameter_count": trainable,
            "projection_dim": model.config.projection_dim,
            "audio_hidden_size": model.config.audio_config.hidden_size,
            "text_hidden_size": model.config.text_config.hidden_size,
        },
        "snapshot_preprocessor": {
            "sampling_rate": processor.feature_extractor.sampling_rate,
            "max_length_s": processor.feature_extractor.max_length_s,
            "padding": processor.feature_extractor.padding,
            "truncation": processor.feature_extractor.truncation,
            "preprocessor_config_sha256": files[
                "preprocessor_config.json"
            ]["sha256"],
        },
        "runtime": {
            "conda_env": "M4",
            "python": sys.version,
            "platform": platform.platform(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "git_head": git_head(),
            "hf_home": str(INTERSUIT_ROOT / "assets/hf_cache"),
            "hf_hub_cache": str(
                INTERSUIT_ROOT / "assets/hf_cache/hub"
            ),
        },
        "transformers_clap_code": {
            "files": code_files,
            "combined_sha256": code_digest.hexdigest(),
        },
        "formal_validation_accessed": False,
        "test_locked_accessed": False,
    }
    asset_path = OUTPUT_ROOT / "teacher_asset_lock.json"
    write_json(asset_path, asset_lock)
    asset_sha = sha256(asset_path)
    ledger = update_ledger(asset_sha)
    environment = {
        "conda_env": "M4",
        "python_executable": sys.executable,
        "python_version": sys.version,
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "hf_home": str(INTERSUIT_ROOT / "assets/hf_cache"),
        "hf_hub_cache": str(INTERSUIT_ROOT / "assets/hf_cache/hub"),
        "snapshot": str(SNAPSHOT),
        "offline_load_pass": True,
    }
    write_json(OUTPUT_ROOT / "environment.json", environment)
    return {
        "status": "PASS",
        "teacher_asset_lock": relative(asset_path),
        "teacher_asset_lock_sha256": asset_sha,
        "candidate_attempt_count": len(ledger["attempts"]),
        "trainable_parameter_count": trainable,
    }


def main() -> int:
    try:
        result = run()
    except Exception as exc:
        print(f"CLAP 教师资产锁定失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
