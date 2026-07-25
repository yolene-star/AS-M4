import json
from pathlib import Path

import pytest

from intersuit.harness.dynamic_selector_v2_metrics import (
    assert_golden_results,
    validate_longvale_fixture,
    validate_unav_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures/dynamic_selector_v2"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_frozen_longvale_fixture_matches_official_input_contract():
    annotation = _load("longvale_seg_caption_golden.json")
    predictions = _load("longvale_seg_caption_predictions.json")

    validate_longvale_fixture(annotation, predictions)


def test_longvale_fixture_rejects_missing_sentence_prediction():
    annotation = _load("longvale_seg_caption_golden.json")
    predictions = _load("longvale_seg_caption_predictions.json")[:1]

    with pytest.raises(ValueError, match="every annotated sentence"):
        validate_longvale_fixture(annotation, predictions)


def test_frozen_unav_fixture_matches_official_input_contract():
    annotation = _load("unav_detection_golden.json")
    predictions = _load("unav_detection_predictions.json")

    validate_unav_fixture(annotation, predictions)


def test_unav_fixture_rejects_ragged_prediction_columns():
    annotation = _load("unav_detection_golden.json")
    predictions = _load("unav_detection_predictions.json")
    predictions["score"] = predictions["score"][:1]

    with pytest.raises(ValueError, match="equal length"):
        validate_unav_fixture(annotation, predictions)


def test_normalized_golden_contract_requires_rouge_l_and_full_tiou_grid():
    longvale = {"BLEU4": 1.0, "METEOR": 1.0, "ROUGE-L": 1.0, "CIDEr": 10.0}
    unav = {"average_mAP": 1.0, "mAP_by_tIoU": [1.0] * 9}

    assert_golden_results(longvale, unav)

    broken = dict(longvale, **{"ROUGE-L": 0.9})
    with pytest.raises(AssertionError, match="ROUGE-L"):
        assert_golden_results(broken, unav)
