import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_valor32k_avqa_pilot_manifest.py"
SPEC = importlib.util.spec_from_file_location("build_valor32k_avqa_pilot_manifest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
build_manifest = module.build_manifest


def _row(modality: str, row_id: int) -> dict:
    return {
        "caption": "A clip with a visible and audible event.",
        "video_id": "video000001",
        "question": f"Question for {modality}?",
        "options": ["A", "B", "C", "D"],
        "correct_answer_idx": 0,
        "rephrased_answers": ["A one", "The answer is A", "It is A"],
        "quality_rating": "obvious",
        "modality": modality,
        "category": "description",
        "source_tags": ["audio"] if modality == "audio" else ["frames"],
        "id": row_id,
        "oid": 1,
        "model": "gpt4o",
    }


def test_build_manifest_selects_train_tri_modal_video_without_leakage() -> None:
    rows = [_row("visual", 1), _row("audio", 2), _row("audio-visual", 3)]
    manifest, report = build_manifest(
        train_rows=rows,
        validation_rows=[],
        test_rows=[],
        exclusion_ids=set(),
        seed="unit-test",
        quotas={"description": 1},
    )

    assert report["status"] == "pass"
    assert report["selected_video_count"] == 1
    assert report["selected_question_count"] == 3
    assert report["selected_vs_existing_exclusion_overlap"] == 0
    assert manifest[0]["official_split"] == "train"
    assert set(manifest[0]["questions"]) == {"visual", "audio", "audio-visual"}
