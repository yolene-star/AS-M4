import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load("build_valor32k_confidence_p2_manifest.py")
auditor = _load("audit_valor32k_confidence_p2_manifest.py")


def _question(modality: str, row_id: int, answer: str) -> dict:
    return {
        "id": row_id,
        "question": f"Question for {modality}?",
        "options": [answer, "other"],
        "correct_answer_idx": 0,
        "modality": modality,
        "category": "description",
    }


def _pilot(index: int, answer: str) -> dict:
    return {
        "video_id": f"video{index}",
        "selection_audio_category": "description",
        "questions": {
            modality: _question(modality, index * 10 + offset, answer)
            for offset, modality in enumerate(("visual", "audio", "audio-visual"), 1)
        },
    }


def test_independent_audit_detects_label_mutation() -> None:
    pilot = [_pilot(index, f"answer{index}") for index in range(1, 151)]
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
    rows, _ = builder.build_condition_manifest(pilot, assets, "unit-seed")

    passed = auditor.audit_manifest(pilot, assets, rows)
    assert passed["status"] == "PASS_P2_INDEPENDENT_AUDIT"

    rows[0]["quality_rank"] = 0
    failed = auditor.audit_manifest(pilot, assets, rows)
    assert failed["status"] == "FAIL_P2_INDEPENDENT_AUDIT"
    assert failed["label_error_count"] == 1
