"""AVE D1 validation 基础策略 runner 的 CPU 单元测试。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest
import torch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "run_ave_official_d1_validation_baselines.py"
SPEC = importlib.util.spec_from_file_location(
    "run_ave_official_d1_validation_baselines", MODULE_PATH
)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_validation_input_guard_rejects_test_sealed(tmp_path):
    candidate = tmp_path / "candidate.jsonl"
    media = tmp_path / "media.jsonl"
    preflight = tmp_path / "preflight.json"
    write_jsonl(
        candidate,
        [
            {
                "youtube_id": "x",
                "official_split": "validation",
                "has_local_boundary": True,
            }
        ],
    )
    write_jsonl(
        media,
        [
            {
                "youtube_id": "x",
                "official_split": "validation",
                "test_sealed": True,
                "status": "present",
            }
        ],
    )
    preflight.write_text(
        json.dumps(
            {
                "status": "pass",
                "sample_count": 1,
                "invalid_count": 0,
                "test_accessed": False,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sealed"):
        module.load_validation_inputs(candidate, media, preflight, expected_count=1)


def test_decode_audio_mono_16k_rejects_empty_via_mock(tmp_path, monkeypatch):
    class EmptyContainer:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def decode(self, audio):
            return []

    monkeypatch.setattr(module.av, "open", lambda *args, **kwargs: EmptyContainer())

    with pytest.raises(ValueError, match="未解码出音频"):
        module.decode_audio_mono_16k(tmp_path / "empty.mp4")


def test_run_strategies_freezes_expected_matrix_and_localization_subset():
    rows = [
        {
            "youtube_id": "local",
            "has_local_boundary": True,
            "annotations": [{"event_start_sec": 2, "event_end_sec": 4}],
        },
        {
            "youtube_id": "full",
            "has_local_boundary": False,
            "annotations": [{"event_start_sec": 0, "event_end_sec": 10}],
        },
    ]
    scores = {
        "local": {
            "energy_scores": [0, 0, 1, 1, 0, 0, 0, 0, 0, 0],
            "onset_scores": [0, 0, 2, 1, 0, 0, 0, 0, 0, 0],
        },
        "full": {
            "energy_scores": list(range(10)),
            "onset_scores": list(range(10)),
        },
    }

    records, metrics = module.run_strategies(rows, scores)

    assert len(records) == 2 * 28
    primary = metrics["local_boundary_primary"]
    assert primary["oracle_topk|K=4|seed=None"]["sample_count"] == 1
    assert primary["oracle_topk|K=4|seed=None"]["window_hit_at_k"] == 1.0
    assert (
        metrics["all_physical_diagnostic"]["all|K=10|seed=None"]["sample_count"]
        == 2
    )
    assert set(metrics["random_local_boundary_summary"]["K=4"]["per_seed"]) == {
        str(seed) for seed in module.FIXED_CONTROL_SEEDS
    }


def test_waveform_cache_reuses_media_sha_bound_tensor(tmp_path, monkeypatch):
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"media")
    media_sha = module.file_sha256(media)
    rows = [
        {
            "youtube_id": "sample",
            "media_path": str(media),
            "media_sha256": media_sha,
        }
    ]
    waveform = torch.zeros(160_000)
    waveform[32_000:48_000] = 1.0
    monkeypatch.setattr(module, "decode_audio_mono_16k", lambda path: waveform)

    first, first_hits, first_new = module.extract_or_load_waveform_scores(
        rows, tmp_path / "cache"
    )
    second, second_hits, second_new = module.extract_or_load_waveform_scores(
        rows, tmp_path / "cache"
    )

    assert first == second
    assert (first_hits, first_new) == (0, 1)
    assert (second_hits, second_new) == (1, 0)
    assert int(torch.tensor(first["sample"]["energy_scores"]).argmax().item()) == 2
