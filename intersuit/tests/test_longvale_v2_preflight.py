import io
import json
from pathlib import Path
import zipfile

import pytest
import torch

from intersuit.harness.longvale_v2_preflight import (
    MIN_SPARSE_EVENTS,
    SPLIT_SIZES,
    ZipMember,
    build_mismatch_mapping,
    extract_zip_member,
    freeze_video_splits,
    parse_training_annotations,
    parse_zip_central_directory,
    validate_split,
)


def test_parse_zip_central_directory_from_tail():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("video_train_7240/video-a.mp4", b"video-a")
        archive.writestr("video_train_7240/video-b.mp4", b"video-b")
    payload = buffer.getvalue()
    tail = payload[-256:]
    members = parse_zip_central_directory(tail, total_size=len(payload))
    assert [item.name for item in members] == [
        "video_train_7240/video-a.mp4",
        "video_train_7240/video-b.mp4",
    ]
    assert members[0].uncompressed_size == len(b"video-a")


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_extract_zip_member_validates_size_and_crc(tmp_path: Path, compression: int):
    buffer = io.BytesIO()
    content = b"abc123" * 1000
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        archive.writestr("video.mp4", content)
    payload = buffer.getvalue()
    central = parse_zip_central_directory(payload, total_size=len(payload))[0]
    name_length, extra_length = __import__("struct").unpack_from("<2H", payload, 26)
    data_start = 30 + name_length + extra_length
    compressed = tmp_path / "member.part"
    compressed.write_bytes(payload[data_start : data_start + central.compressed_size])
    output = tmp_path / "video.mp4"
    extract_zip_member(compressed, output, central)
    assert output.read_bytes() == content


def test_parse_zip_central_directory_rejects_short_tail():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("video.mp4", b"x")
    payload = buffer.getvalue()
    with pytest.raises(ValueError, match="更大的尾部 range"):
        parse_zip_central_directory(payload[-30:], total_size=len(payload))


def test_freeze_splits_are_stable_disjoint_and_sized():
    annotations = {
        f"video-{index:04d}": {
            "duration_sec": 100.0,
            "events": [{"is_sparse": True}] * 10,
        }
        for index in range(sum(SPLIT_SIZES.values()) + 20)
    }
    first = freeze_video_splits(annotations, namespace="fixture")
    second = freeze_video_splits(annotations, namespace="fixture")
    assert first == second
    assert {name: len(ids) for name, ids in first.items()} == SPLIT_SIZES
    flat = [video_id for values in first.values() for video_id in values]
    assert len(flat) == len(set(flat))


def test_mismatch_mapping_is_stable_one_to_one_derangement():
    ids = [f"video-{index}" for index in range(10)]
    first = build_mismatch_mapping(ids, seed=20260720)
    assert first == build_mismatch_mapping(reversed(ids), seed=20260720)
    assert set(first) == set(first.values()) == set(ids)
    assert all(source != target for source, target in first.items())


def test_parse_annotations_and_validate_sparse_threshold(tmp_path: Path):
    annotation = {
        "video-a": {
            "duration": "100.0",
            "segments": {"0.0_5.0": "Sparse event", "5.0_25.0": "Dense event"},
        }
    }
    path = tmp_path / "annotation.json"
    path.write_text(json.dumps(annotation), encoding="utf-8")
    parsed = parse_training_annotations(path)
    assert [event["is_sparse"] for event in parsed["video-a"]["events"]] == [True, False]
    rows = [
        {"video_id": f"video-{index}", "is_sparse": True}
        for index in range(SPLIT_SIZES["oracle_dev"])
        for _ in range(MIN_SPARSE_EVENTS["oracle_dev"] // SPLIT_SIZES["oracle_dev"])
    ]
    assert validate_split(rows, split_name="oracle_dev")["sparse_event_count"] == 100


def test_pilot_matrix_explicit_strategies_are_sorted_and_k_bounded():
    import importlib.util
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts/build_longvale_v2_pilot_matrix.py"
    spec = importlib.util.spec_from_file_location("longvale_v2_matrix_fixture", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    payload = {
        "timestamps": torch.tensor([[float(index), float(index + 1)] for index in range(12)]),
        "energy_scores": torch.arange(12, dtype=torch.float32),
        "onset_scores": torch.arange(12, dtype=torch.float32).flip(0),
        "eventness_cached_scores": torch.zeros(12),
        "metadata": {"duration_sec": 12.0},
    }
    for condition in (
        "uniform_topk", "energy_topk", "onset_topk", "eventness_cached_topk",
        "random_topk_seed_20260720", "oracle_topk",
    ):
        indices = module.select_indices(
            condition,
            sample_id="sample",
            target_start=5.1,
            target_end=5.9,
            target_duration=12.0,
            score_payload=payload,
        )
        assert indices == sorted(set(indices))
        assert len(indices) == 8
        assert min(indices) >= 0 and max(indices) < 12


def test_pilot_matrix_mismatch_uses_normalized_target_time():
    import importlib.util
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts/build_longvale_v2_pilot_matrix.py"
    spec = importlib.util.spec_from_file_location("longvale_v2_matrix_mismatch_fixture", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    target = {
        "timestamps": torch.tensor([[float(index), float(index + 1)] for index in range(10)]),
        "energy_scores": torch.zeros(10), "onset_scores": torch.zeros(10),
        "eventness_cached_scores": torch.zeros(10), "metadata": {"duration_sec": 10.0},
    }
    source = {
        "timestamps": torch.tensor([[float(index), float(index + 1)] for index in range(20)]),
        "energy_scores": torch.zeros(20), "onset_scores": torch.zeros(20),
        "eventness_cached_scores": torch.zeros(20), "metadata": {"duration_sec": 20.0},
    }
    indices = module.select_indices(
        "oracle_mismatch_seed_20260720",
        sample_id="sample",
        target_start=5.0,
        target_end=6.0,
        target_duration=10.0,
        score_payload=target,
        mismatch_payload=source,
    )
    assert 10 in indices and 11 in indices
