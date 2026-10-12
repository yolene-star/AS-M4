from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path("intersuit/scripts/build_omnimmi_audio_requirement_review.py").resolve()
SPEC = spec_from_file_location("build_omnimmi_audio_requirement_review", SCRIPT)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
candidate_label = MODULE.candidate_label


def test_audio_required_candidate() -> None:
    label, audio, visual = candidate_label("What did the woman say?", "She said hello")
    assert label == "audio_required_candidate"
    assert audio
    assert not visual


def test_audio_visual_joint_candidate() -> None:
    label, audio, visual = candidate_label(
        "When is the man shown talking outside?", "At the end"
    )
    assert label == "audio_visual_joint_candidate"
    assert audio and visual


def test_visual_only_candidate() -> None:
    label, audio, visual = candidate_label("What color is the coat?", "Black")
    assert label == "visual_only_candidate"
    assert not audio
    assert visual


def test_audio_cues_do_not_match_inside_unrelated_words() -> None:
    label, audio, visual = candidate_label(
        "What is she wearing while using a basket?", "A spring coat"
    )
    assert label == "visual_only_candidate"
    assert not audio
    assert visual
