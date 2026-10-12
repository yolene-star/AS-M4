import importlib.util
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / f"scripts/{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_training_event_pooling_uses_official_overlap():
    module = load_script("train_unav_s1r_semantic_projector_v1")
    payload = {
        "timestamps": torch.tensor([[0.0, 1.0], [1.0, 2.0]]),
        "audio_embedding": torch.stack((torch.ones(768), torch.full((768,), 3.0))),
        "events": [{"onset": 0.5, "offset": 1.5, "event_label": "event"}],
    }
    vector, label = module.event_windows(payload)[0]
    assert label == "event"
    assert torch.allclose(vector, torch.full((768,), 2.0))


def test_evaluator_event_pooling_matches_training():
    train = load_script("train_unav_s1r_semantic_projector_v1")
    evaluate = load_script("evaluate_unav_s1r_projector_v1")
    payload = {
        "timestamps": torch.tensor([[0.0, 1.0], [1.0, 2.0]]),
        "audio_embedding": torch.stack((torch.ones(768), torch.full((768,), 5.0))),
        "events": [{"onset": 0.25, "offset": 1.25, "event_label": "event"}],
    }
    expected = train.event_windows(payload)[0][0]
    actual = evaluate.events(payload)[0][0]
    assert torch.allclose(actual, expected)
