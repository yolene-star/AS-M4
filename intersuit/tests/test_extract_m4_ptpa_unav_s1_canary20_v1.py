"""UnAV S1 canary20选择性解压测试。"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/extract_m4_ptpa_unav_s1_canary20_v1.py"
SPEC = importlib.util.spec_from_file_location("extract_m4_ptpa_unav_s1_canary20_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def split_archive(tmp_path: Path) -> tuple[list[Path], dict[str, bytes]]:
    contents = {"video_a": b"aaa", "video_b": b"bbbb"}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for video_id, content in contents.items():
            member = tarfile.TarInfo(f"root/{video_id}.mp4")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    payload = buffer.getvalue()
    cut = len(payload) // 2
    paths = [tmp_path / "part_00", tmp_path / "part_01"]
    paths[0].write_bytes(payload[:cut])
    paths[1].write_bytes(payload[cut:])
    return paths, contents


def selection(contents: dict[str, bytes]) -> list[dict]:
    return [
        {
            "plan": {"physical_media_id": video_id, "selection_rank": index + 1},
            "member": {
                "member_name": f"root/{video_id}.mp4",
                "media_size_bytes": len(content),
            },
        }
        for index, (video_id, content) in enumerate(contents.items())
    ]


def test_extract_selected_writes_only_allowlisted_members(tmp_path):
    paths, contents = split_archive(tmp_path)
    media_root = tmp_path / "media"

    results = module.extract_selected(paths, selection(contents), media_root)

    assert set(results) == set(contents)
    assert {path.stem for path in media_root.glob("*.mp4")} == set(contents)
    for video_id, content in contents.items():
        assert (media_root / f"{video_id}.mp4").read_bytes() == content


def test_build_selection_rejects_test_rows(monkeypatch, tmp_path):
    plan = [
        {
            "physical_media_id": f"video_{index}",
            "official_split": "test" if index == 19 else ("train" if index < 10 else "validation"),
            "selection_rank": index + 1,
            "historical_overlap": False,
            "feature_extraction_allowed": False,
            "effect_access_allowed": False,
        }
        for index in range(20)
    ]
    monkeypatch.setattr(module, "read_jsonl", lambda path: plan)

    with pytest.raises(ValueError):
        module.build_selection(tmp_path / "plan", tmp_path / "ledger", tmp_path / "index")
