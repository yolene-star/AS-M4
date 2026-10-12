"""UnAV分卷tar无解压成员审计测试。"""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_m4_ptpa_unav_archive_members_v1.py"
SPEC = importlib.util.spec_from_file_location("audit_m4_ptpa_unav_archive_members_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def make_archive(tmp_path: Path, unsafe: bool = False) -> list[Path]:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        directory = tarfile.TarInfo(str(module.EXPECTED_PREFIX))
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        for video_id in ("video_a", "video_b"):
            content = video_id.encode()
            name = f"{module.EXPECTED_PREFIX}/{video_id}.mp4"
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        if unsafe:
            member = tarfile.TarInfo("../escape.mp4")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    content = buffer.getvalue()
    cuts = (len(content) // 3, 2 * len(content) // 3)
    parts = [content[: cuts[0]], content[cuts[0] : cuts[1]], content[cuts[1] :]]
    paths = []
    for index, part in enumerate(parts):
        path = tmp_path / f"part_{index:02d}"
        path.write_bytes(part)
        paths.append(path)
    return paths


def ledger() -> dict:
    return {
        "video_a": {
            "official_split": "train",
            "eligible": True,
            "exclusion_reasons": [],
            "test_effect_locked": False,
        },
        "video_b": {
            "official_split": "test",
            "eligible": True,
            "exclusion_reasons": [],
            "test_effect_locked": True,
        },
    }


def test_scan_archive_maps_members_without_extraction(tmp_path):
    rows, summary = module.scan_archive(make_archive(tmp_path), ledger())

    assert summary["status"] == "PASS"
    assert summary["regular_mp4_count"] == 2
    assert summary["archive_split_counts"] == {"test": 1, "train": 1}
    assert summary["metadata_missing_from_archive_count"] == 0
    assert rows[1]["test_effect_locked"] is True


def test_scan_archive_rejects_path_traversal(tmp_path):
    _, summary = module.scan_archive(make_archive(tmp_path, unsafe=True), ledger())

    assert summary["status"] == "FAIL"
    assert summary["unsafe_member_count"] == 1
