import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_valor32k_confidence_p2_manifest.py"
)
SPEC = importlib.util.spec_from_file_location("build_valor32k_confidence_p2_manifest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _question(modality: str, row_id: int, answer: str) -> dict:
    return {
        "id": row_id,
        "question": f"Question for {modality}?",
        "options": [answer, "other"],
        "correct_answer_idx": 0,
        "modality": modality,
        "category": "description",
    }


def _pilot(video_id: str, answer: str) -> dict:
    return {
        "video_id": video_id,
        "selection_audio_category": "description",
        "questions": {
            "visual": _question("visual", int(video_id[-1]) * 10 + 1, answer),
            "audio": _question("audio", int(video_id[-1]) * 10 + 2, answer),
            "audio-visual": _question("audio-visual", int(video_id[-1]) * 10 + 3, answer),
        },
    }


def test_build_media_assets_preserves_ids_with_underscores(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    media = media_root / "abc_def_0.000_10.000.mp4"
    media.write_bytes(b"fake-media")
    audit = tmp_path / "audit.tsv"
    audit.write_text(
        "abc_def_0.000_10.000.mp4\tcodec_type=video;codec_type=audio;"
        "sample_rate=44100;channels=2;duration=10.047000;\n",
        encoding="utf-8",
    )

    assets, report = module.build_media_assets(
        [{"video_id": "abc_def"}], media_root, audit
    )

    assert assets[0]["physical_video_id"] == "abc_def"
    assert assets[0]["sha256"] == module.sha256_file(media)
    assert report["all_have_audio_video"] is True


def test_condition_manifest_is_deterministic_and_separates_stages() -> None:
    pilot = [_pilot("video1", "cat"), _pilot("video2", "dog")]
    assets = [
        {
            "physical_video_id": item["video_id"],
            "official_split": "train",
            "relative_media_path": f"{item['video_id']}.mp4",
            "sha256": item["video_id"].ljust(64, "0"),
            "duration_sec": 10.0,
            "audio_sample_rate_hz": 44100,
            "audio_channels": 2,
            "stream_window_count": 9,
        }
        for item in pilot
    ]

    first, report = module.build_condition_manifest(pilot, assets, "unit-seed")
    second, second_report = module.build_condition_manifest(pilot, assets, "unit-seed")

    assert first == second
    assert report == second_report
    assert report["question_count"] == 6
    assert report["record_count"] == 84
    assert report["first_stage_record_count"] == 72
    assert report["second_stage_record_count"] == 12
    assert report["mismatch_self_mapping_count"] == 0
    assert report["mismatch_cross_split_count"] == 0
    assert report["mismatch_missing_target_normalization_count"] == 0
    assert report["first_stage_shifted_count"] == 0
    assert report["second_stage_non_shifted_count"] == 0
    assert report["duplicate_record_id_count"] == 0
    assert report["quality_relevance_sync_independently_varied"] is True
    mismatch = [row for row in first if row["condition"] == "mismatch"]
    assert all(
        row["physical_video_id"] != row["audio_source_physical_video_id"]
        for row in mismatch
    )
    assert all(
        row["corruption_recipe"]["normalization"]["stream_window_count"] == 9
        for row in mismatch
    )
