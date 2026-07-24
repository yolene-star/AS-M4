"""D1 AVE validation-only 媒体解码预检的 CPU 单元测试。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "preflight_ave_official_d1_validation_media.py"
SPEC = importlib.util.spec_from_file_location(
    "preflight_ave_official_d1_validation_media", MODULE_PATH
)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def write_manifest(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_load_validation_rows_rejects_test_or_non_validation(tmp_path):
    path = tmp_path / "rows.jsonl"
    base = {
        "youtube_id": "sample",
        "official_split": "test",
        "test_sealed": True,
        "status": "present",
    }
    write_manifest(path, [base])

    with pytest.raises(ValueError, match="非 validation"):
        module.load_validation_rows(path, expected_count=1)
    base["official_split"] = "validation"
    write_manifest(path, [base])
    with pytest.raises(ValueError, match="test/sealed"):
        module.load_validation_rows(path, expected_count=1)


def test_load_validation_rows_requires_unique_expected_count_and_present(tmp_path):
    path = tmp_path / "rows.jsonl"
    row = {
        "youtube_id": "sample",
        "official_split": "validation",
        "test_sealed": False,
        "status": "present",
    }
    write_manifest(path, [row])
    assert module.load_validation_rows(path, expected_count=1) == [row]
    with pytest.raises(ValueError, match="行数不符"):
        module.load_validation_rows(path, expected_count=2)
    write_manifest(path, [row, row])
    with pytest.raises(ValueError, match="重复物理视频"):
        module.load_validation_rows(path, expected_count=2)


def test_audit_row_validates_sha_streams_and_full_decode(tmp_path, monkeypatch):
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"media")
    row = {
        "youtube_id": "sample",
        "media_path": str(media),
        "media_size_bytes": media.stat().st_size,
        "media_sha256": module.file_sha256(media),
    }
    monkeypatch.setattr(
        module,
        "probe_media",
        lambda path: {
            "duration_sec": 10.0,
            "video_stream_count": 1,
            "audio_stream_count": 1,
        },
    )
    calls = []

    def fake_decode(path, kind):
        calls.append(kind)
        return 300 if kind == "video" else 431

    monkeypatch.setattr(module, "decode_stream", fake_decode)
    record = module.audit_row(row)

    assert record["status"] == "valid"
    assert record["full_video_decode_ok"] is True
    assert record["full_audio_decode_ok"] is True
    assert record["decoded_video_frame_count"] == 300
    assert record["decoded_audio_frame_count"] == 431
    assert record["test_accessed"] is False
    assert calls == ["video", "audio"]


def test_audit_row_reports_sha_change_without_decoding(tmp_path, monkeypatch):
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"changed")
    row = {
        "youtube_id": "sample",
        "media_path": str(media),
        "media_size_bytes": media.stat().st_size,
        "media_sha256": "0" * 64,
    }

    def forbidden_probe(*args, **kwargs):
        raise AssertionError("SHA 变化后不应继续调用 PyAV")

    monkeypatch.setattr(module, "probe_media", forbidden_probe)
    record = module.audit_row(row)

    assert record["status"] == "invalid"
    assert record["failure_reasons"] == ["media_sha256_changed"]
