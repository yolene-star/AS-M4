"""AVE 官方 D1 媒体冻结器的 CPU 单元测试。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "freeze_ave_official_d1_media.py"
SPEC = importlib.util.spec_from_file_location("freeze_ave_official_d1_media", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_index_media_rejects_duplicate_stems(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "same.mp4").write_bytes(b"a")
    (tmp_path / "b" / "same.avi").write_bytes(b"b")
    (tmp_path / "unique.mp4").write_bytes(b"c")

    media, duplicates = module.index_media(tmp_path)

    assert set(media) == {"unique"}
    assert set(duplicates) == {"same"}


def test_audit_split_marks_missing_zero_byte_and_sealed_test(tmp_path):
    valid = tmp_path / "valid.mp4"
    valid.write_bytes(b"valid")
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    rows = [
        {"youtube_id": "valid"},
        {"youtube_id": "empty"},
        {"youtube_id": "absent"},
        {"youtube_id": "duplicate"},
    ]

    inventory, missing = module.audit_split(
        "test",
        rows,
        {"valid": valid, "empty": empty},
        {"duplicate"},
    )

    assert len(inventory) == 4
    assert {row["youtube_id"] for row in missing} == {"empty", "absent", "duplicate"}
    reasons = {row["youtube_id"]: row["failure_reason"] for row in missing}
    assert reasons == {
        "empty": "zero_byte_media",
        "absent": "missing_media",
        "duplicate": "duplicate_media_paths",
    }
    assert all(row["test_sealed"] for row in inventory)
    assert inventory[0]["media_sha256"] == module.file_sha256(valid)


def test_load_jsonl_requires_unique_physical_ids(tmp_path):
    path = tmp_path / "manifest.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"youtube_id": "same"}),
                json.dumps({"youtube_id": "same"}),
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="重复物理视频"):
        module.load_jsonl(path)
    assert len(module.load_jsonl(path, require_unique_ids=False)) == 2


def test_run_rejects_incomplete_official_archive(tmp_path):
    archive = tmp_path / "AVE_Dataset.zip"
    archive.write_bytes(b"incomplete")

    with pytest.raises(ValueError, match="字节数不符"):
        module.run(
            SimpleNamespace(
                manifest_dir=tmp_path,
                media_root=tmp_path,
                source_archive=archive,
                output_dir=tmp_path / "output",
            )
        )


def test_run_freezes_counts_sha_and_test_seal(tmp_path):
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    manifests = {
        "train_candidate_manifest.jsonl": [{"youtube_id": "train-id"}],
        "validation_candidate_manifest.jsonl": [{"youtube_id": "validation-id"}],
        "test_candidate_manifest.sealed.jsonl": [{"youtube_id": "test-id"}],
    }
    for filename, rows in manifests.items():
        (manifest_dir / filename).write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
    media_root = tmp_path / "media"
    package_root = media_root / "AVE_Dataset"
    package_media = package_root / "AVE"
    package_media.mkdir(parents=True)
    raw_rows = {
        "train": [
            {
                "youtube_id": "train-id",
                "event_label": "train",
                "quality": "good",
                "event_start_sec": 0,
                "event_end_sec": 10,
            }
        ],
        "validation": [
            {
                "youtube_id": "validation-id",
                "event_label": "validation",
                "quality": "good",
                "event_start_sec": 1,
                "event_end_sec": 9,
            }
        ],
        "test": [
            {
                "youtube_id": "test-id",
                "event_label": "test",
                "quality": "good",
                "event_start_sec": 2,
                "event_end_sec": 8,
            }
        ],
    }
    for split, filename in module.RAW_MANIFEST_NAMES.items():
        (manifest_dir / filename).write_text(
            "".join(json.dumps(row) + "\n" for row in raw_rows[split]),
            encoding="utf-8",
        )
    expected_lines = {
        split: [module.annotation_line(row) for row in rows]
        for split, rows in raw_rows.items()
    }
    for split, filename in module.PACKAGE_SPLIT_NAMES.items():
        (package_root / filename).write_text(
            "\n".join(expected_lines[split]) + "\n",
            encoding="utf-8",
        )
    (package_root / "Annotations.txt").write_text(
        "\n".join(
            [
                module.ANNOTATION_HEADER,
                *expected_lines["test"],
                *expected_lines["train"],
                *expected_lines["validation"],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (package_media / "train-id.mp4").write_bytes(b"train")
    (package_media / "validation-id.mp4").write_bytes(b"validation")
    (package_media / "test-id.mp4").write_bytes(b"test")
    archive = tmp_path / "AVE_Dataset.zip"
    archive.write_bytes(b"archive")
    output_dir = tmp_path / "output"

    original_expected_size = module.EXPECTED_ARCHIVE_SIZE_BYTES
    module.EXPECTED_ARCHIVE_SIZE_BYTES = archive.stat().st_size
    try:
        lock = module.run(
            SimpleNamespace(
                manifest_dir=manifest_dir,
                media_root=media_root,
                source_archive=archive,
                output_dir=output_dir,
            )
        )
    finally:
        module.EXPECTED_ARCHIVE_SIZE_BYTES = original_expected_size

    assert lock["counts"]["train"]["present_count"] == 1
    assert lock["counts"]["validation"]["present_count"] == 1
    assert lock["counts"]["test"]["present_count"] == 1
    assert lock["package_verification"]["media_id_exact_match"] is True
    assert lock["test_seal"]["sealed"] is True
    assert lock["test_seal"]["predictions_run"] is False
    test_rows = [
        json.loads(line)
        for line in (output_dir / "test_media_inventory.sealed.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert test_rows[0]["test_sealed"] is True
    assert lock["source_archive"]["sha256"] == module.file_sha256(archive)
