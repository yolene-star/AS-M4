#!/usr/bin/env python3
"""复核 UnAV-100 官方13卷归档并生成组合下载终态清单。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/m4_ptpa_unav_s1_reconstruction_v1.json"
DEFAULT_ARCHIVE_ROOT = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/official_archive"
DEFAULT_CURL_STATUS = DEFAULT_ARCHIVE_ROOT / "archive_download_status.json"
DEFAULT_XET_STATUS = DEFAULT_ARCHIVE_ROOT / "xet_handoff_part09_12_status.tsv"
DEFAULT_OUTPUT = DEFAULT_ARCHIVE_ROOT / "archive_completion_manifest_v1.json"
PASS_STATUS = "PASS_ARCHIVE_COMPLETE_VERIFIED_EFFECT_LOCKED"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_contract(contract: dict[str, Any]) -> list[dict[str, Any]]:
    if contract.get("status") not in {
        "full_archive_download_authorized_effect_locked",
        "archive_complete_verified_effect_locked",
        "archive_complete_verified_structure_audited_effect_locked",
        "canary20_selective_extraction_authorized_effect_locked",
    }:
        raise PermissionError("UnAV完整归档合同状态未授权终态复核")
    permissions = contract["permissions"]
    authorization_recorded = (
        permissions.get("full_archive_download_allowed_by_explicit_user_authorization") is True
        or contract.get("archive_completion", {}).get(
            "explicit_user_download_authorization_consumed"
        ) is True
    )
    if not authorization_recorded:
        raise PermissionError("UnAV完整归档用户授权缺失")
    for key in (
        "archive_extraction_allowed", "feature_extraction_allowed",
        "effect_access_allowed", "test_media_download_allowed",
        "test_feature_extraction_allowed", "test_effect_access_allowed",
        "training_allowed", "redistribution_allowed",
    ):
        if permissions.get(key) is not False:
            raise PermissionError(f"UnAV终态复核禁止项漂移：{key}")
    files = list(contract["media_distribution"]["archive_files"])
    if len(files) != 13:
        raise ValueError("UnAV归档卷数必须为13")
    if sum(int(item["size"]) for item in files) != 65_775_604_313:
        raise ValueError("UnAV归档总字节数漂移")
    return files


def read_xet_status(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Xet handoff状态不存在：{path}")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    verified = {
        int(row["part"]): row
        for row in rows
        if row.get("part", "").isdigit() and row.get("status") == "verified"
    }
    all_pass = any(row.get("part") == "all" and row.get("status") == "PASS" for row in rows)
    return {"rows": rows, "verified": verified, "all_pass": all_pass}


def build_manifest(
    contract_path: Path,
    archive_root: Path,
    curl_status_path: Path,
    xet_status_path: Path,
) -> dict[str, Any]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    files = validate_contract(contract)
    curl_status = json.loads(curl_status_path.read_text(encoding="utf-8"))
    xet_status = read_xet_status(xet_status_path)
    revision = contract["media_distribution"]["archive_revision"]
    if curl_status.get("revision") != revision:
        raise ValueError("curl状态revision与冻结合同不一致")
    if not xet_status["all_pass"]:
        raise ValueError("Xet handoff未记录all PASS")

    results = []
    total_bytes = 0
    for index, item in enumerate(files):
        path = archive_root / item["name"]
        if not path.is_file():
            raise FileNotFoundError(f"UnAV归档卷缺失：{path}")
        observed_size = path.stat().st_size
        observed_sha = sha256(path)
        if observed_size != int(item["size"]) or observed_sha != item["sha256"]:
            raise ValueError(f"UnAV归档卷大小或SHA漂移：{item['name']}")
        if index >= 9:
            xet_row = xet_status["verified"].get(index)
            if (
                xet_row is None
                or int(xet_row["size"]) != observed_size
                or xet_row["sha256"] != observed_sha
            ):
                raise ValueError(f"Xet handoff终态与归档不一致：part_{index:02d}")
        results.append(
            {
                "index": index,
                "name": item["name"],
                "size": observed_size,
                "sha256": observed_sha,
                "verified": True,
                "finalizer_source": "curl" if index < 9 else "hf_xet_handoff",
            }
        )
        total_bytes += observed_size

    if total_bytes != contract["media_distribution"]["archive_total_bytes"]:
        raise ValueError("UnAV组合终态总字节数漂移")
    return {
        "artifact_kind": "m4_ptpa_unav_official_archive_completion_manifest",
        "format_version": 1,
        "status": PASS_STATUS,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": contract["official_source"]["hugging_face"],
        "revision": revision,
        "archive_parts": len(results),
        "archive_total_bytes": total_bytes,
        "completed_verified": len(results),
        "results": results,
        "provenance": {
            "contract_path": str(contract_path),
            "contract_sha256": sha256(contract_path),
            "curl_intermediate_status_path": str(curl_status_path),
            "curl_intermediate_status_sha256": sha256(curl_status_path),
            "curl_intermediate_completed_verified": curl_status.get("completed_verified"),
            "xet_handoff_status_path": str(xet_status_path),
            "xet_handoff_status_sha256": sha256(xet_status_path),
            "xet_verified_parts": sorted(xet_status["verified"]),
        },
        "permissions": {
            "archive_member_inventory_allowed": True,
            "archive_extraction_allowed": False,
            "feature_extraction_allowed": False,
            "effect_access_allowed": False,
            "test_media_access_allowed": False,
            "test_effect_access_allowed": False,
            "training_allowed": False,
            "redistribution_allowed": False,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--curl-status", type=Path, default=DEFAULT_CURL_STATUS)
    parser.add_argument("--xet-status", type=Path, default=DEFAULT_XET_STATUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    arguments = parse_args()
    manifest = build_manifest(
        arguments.contract,
        arguments.archive_root,
        arguments.curl_status,
        arguments.xet_status,
    )
    write_json_atomic(arguments.output, manifest)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "completed_verified": manifest["completed_verified"],
                "archive_total_bytes": manifest["archive_total_bytes"],
                "output": str(arguments.output),
            },
            ensure_ascii=False,
        )
    )
