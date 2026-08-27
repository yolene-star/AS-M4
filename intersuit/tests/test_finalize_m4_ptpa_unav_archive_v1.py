"""UnAV官方归档组合终态清单测试。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/finalize_m4_ptpa_unav_archive_v1.py"
SPEC = importlib.util.spec_from_file_location("finalize_m4_ptpa_unav_archive_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_build_manifest_combines_curl_and_xet_provenance(monkeypatch, tmp_path):
    contents = [f"part-{index}".encode() for index in range(13)]
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    files = []
    for index, content in enumerate(contents):
        name = f"part_{index:02d}"
        (archive_root / name).write_bytes(content)
        files.append({"name": name, "size": len(content), "sha256": digest(content)})
    contract = {
        "status": "full_archive_download_authorized_effect_locked",
        "official_source": {"hugging_face": "owner/dataset"},
        "media_distribution": {
            "archive_revision": "revision",
            "archive_total_bytes": sum(map(len, contents)),
            "archive_files": files,
        },
        "permissions": {
            "full_archive_download_allowed_by_explicit_user_authorization": True,
            "archive_extraction_allowed": False,
            "feature_extraction_allowed": False,
            "effect_access_allowed": False,
            "test_media_download_allowed": False,
            "test_feature_extraction_allowed": False,
            "test_effect_access_allowed": False,
            "training_allowed": False,
            "redistribution_allowed": False,
        },
    }
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract))
    curl_path = tmp_path / "curl.json"
    curl_path.write_text(json.dumps({"revision": "revision", "completed_verified": 9}))
    xet_path = tmp_path / "xet.tsv"
    xet_path.write_text(
        "part\tstatus\tsize\tsha256\tupdated_at\n"
        + "".join(
            f"{index:02d}\tverified\t{len(contents[index])}\t{digest(contents[index])}\tnow\n"
            for index in range(9, 13)
        )
        + f"all\tPASS\t{sum(map(len, contents))}\t-\tnow\n"
    )
    monkeypatch.setattr(module, "validate_contract", lambda value: value["media_distribution"]["archive_files"])

    manifest = module.build_manifest(contract_path, archive_root, curl_path, xet_path)

    assert manifest["status"] == module.PASS_STATUS
    assert manifest["completed_verified"] == 13
    assert manifest["archive_total_bytes"] == sum(map(len, contents))
    assert manifest["results"][8]["finalizer_source"] == "curl"
    assert manifest["results"][9]["finalizer_source"] == "hf_xet_handoff"
    assert manifest["permissions"]["archive_extraction_allowed"] is False


def test_default_xet_status_is_in_archive_root():
    assert module.DEFAULT_XET_STATUS.parent == module.DEFAULT_ARCHIVE_ROOT
