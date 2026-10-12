import importlib.util
import hashlib
import sys
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "intersuit/model/scene_audio_encoder/scene_audio_encoder.py"
SPEC = importlib.util.spec_from_file_location("scene_audio_v4_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

def test_v4_adapter_shape_and_freeze_contract():
    model = object.__new__(MODULE.FrozenBEATsV4SceneAudioEncoder)
    torch.nn.Module.__init__(model)
    model.encoder_dim = 768
    model.audio_projector = torch.nn.Linear(768, 3584)
    model.semantic_projector = torch.nn.Sequential(torch.nn.Linear(768, 256), torch.nn.GELU(), torch.nn.Linear(256, 768))
    model.m4_adapter = torch.nn.Linear(768, 3584)
    for parameter in model.semantic_projector.parameters(): parameter.requires_grad = False
    for parameter in model.audio_projector.parameters(): parameter.requires_grad = False
    assert model.m4_adapter(torch.randn(2, 4, 768)).shape == (2, 4, 3584)
    assert all(not parameter.requires_grad for parameter in model.semantic_projector.parameters())
    assert all(not parameter.requires_grad for parameter in model.audio_projector.parameters())


def _install_fake_frozen_beats_parent(monkeypatch):
    def fake_init(
        self,
        hidden_size,
        checkpoint_path,
        code_root,
        sample_rate=16000,
        expected_sha256=None,
        encode_chunk_size=0,
    ):
        torch.nn.Module.__init__(self)
        self.encoder_dim = 768
        self.hidden_size = int(hidden_size)
        self.audio_projector = torch.nn.Linear(768, int(hidden_size))
        self.checkpoint_sha256 = "beats-sha"
        object.__setattr__(self, "_beats_model", torch.nn.Linear(1, 1))

    monkeypatch.setattr(MODULE.FrozenBEATsSceneAudioEncoder, "__init__", fake_init)


def _write_v4_checkpoint(path: Path, *, finite: bool = True) -> str:
    state = {
        "down.weight": torch.zeros(256, 768),
        "down.bias": torch.zeros(256),
        "up.weight": torch.zeros(768, 256),
        "up.bias": torch.zeros(768),
    }
    if not finite:
        state["down.weight"][0, 0] = float("nan")
    torch.save({"state_dict": state}, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v4_checkpoint_is_sha_locked_and_semantic_weights_are_frozen(
    monkeypatch,
    tmp_path,
):
    _install_fake_frozen_beats_parent(monkeypatch)
    checkpoint = tmp_path / "semantic.pt"
    checkpoint_sha = _write_v4_checkpoint(checkpoint)

    encoder = MODULE.FrozenBEATsV4SceneAudioEncoder(
        hidden_size=3584,
        checkpoint_path="unused-beats.pt",
        code_root="unused-source",
        semantic_projector_path=str(checkpoint),
        semantic_projector_sha256=checkpoint_sha,
    )
    projected = encoder.project(torch.randn(1, 2, 768))

    assert projected.shape == (1, 2, 3584)
    assert encoder.semantic_projector_sha256 == checkpoint_sha
    assert all(not parameter.requires_grad for parameter in encoder.semantic_projector.parameters())
    assert all(not parameter.requires_grad for parameter in encoder.audio_projector.parameters())
    assert any(parameter.requires_grad for parameter in encoder.m4_adapter.parameters())


def test_v4_checkpoint_contract_fails_closed(monkeypatch, tmp_path):
    _install_fake_frozen_beats_parent(monkeypatch)
    checkpoint = tmp_path / "semantic.pt"
    _write_v4_checkpoint(checkpoint, finite=False)

    with pytest.raises(ValueError, match="requires scene_audio_v4_projector_path"):
        MODULE.FrozenBEATsV4SceneAudioEncoder(
            hidden_size=8,
            checkpoint_path="unused-beats.pt",
            code_root="unused-source",
            semantic_projector_path=None,
        )
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        MODULE.FrozenBEATsV4SceneAudioEncoder(
            hidden_size=8,
            checkpoint_path="unused-beats.pt",
            code_root="unused-source",
            semantic_projector_path=str(checkpoint),
            semantic_projector_sha256="0" * 64,
        )
    with pytest.raises(ValueError, match="weights must be finite"):
        MODULE.FrozenBEATsV4SceneAudioEncoder(
            hidden_size=8,
            checkpoint_path="unused-beats.pt",
            code_root="unused-source",
            semantic_projector_path=str(checkpoint),
        )
