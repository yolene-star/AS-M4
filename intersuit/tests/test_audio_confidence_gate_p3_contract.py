import json
from pathlib import Path
from types import SimpleNamespace

import torch
from torch import nn

from intersuit.model.streaming_av.confidence_gate import FirstStageAudioConfidenceGate
from intersuit.model.streaming_av.builder import StreamingAVModule
from intersuit.train.trainable_parts import enable_audio_confidence_gate_only


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "configs" / "audio_confidence_gate_p3_contract_v1.json"


def test_p3_contract_locks_data_scope_and_keeps_training_closed() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert contract["status"] == "PASS_P3_PREFLIGHT_TRAINING_CLOSED"
    assert contract["dataset"]["condition_manifest_sha256"] == (
        "007d5b1d936323d28adee252ce6f407d458d24b3042b1caf1bb8a0086f301e26"
    )
    assert contract["dataset"]["first_stage_record_count"] == 5400
    assert contract["dataset"]["second_stage_allowed"] is False
    assert contract["supervision"]["sync_loss_weight"] == 0.0
    assert contract["runtime"]["cuda_visible_devices"] == "2"
    assert [stage["steps"] for stage in contract["canary_sequence"]] == [2, 20, 150]
    assert not any(
        contract["authorization"][key]
        for key in (
            "feature_precompute",
            "two_step_training",
            "twenty_step_training",
            "one_hundred_fifty_step_training",
            "p4_7b",
        )
    )


def test_p3_contract_parameter_count_matches_real_gate() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    model = nn.Module()
    model.streaming_av_module = nn.Module()
    model.streaming_av_module.first_stage_confidence_gate = FirstStageAudioConfidenceGate(
        hidden_size=3584, projection_dim=128
    )
    model.frozen_reference = nn.Linear(2, 2)

    names = enable_audio_confidence_gate_only(model)
    count = sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)

    assert len(names) == contract["trainable_scope"][
        "expected_parameter_tensor_count_hidden3584_projection128"
    ]
    assert count == contract["trainable_scope"][
        "expected_parameter_count_hidden3584_projection128"
    ]
    assert all(
        contract["trainable_scope"]["parameter_prefix"] in name for name in names
    )


def test_dormant_gate_preserves_legacy_initialization_and_old_state_load() -> None:
    config = SimpleNamespace(hidden_size=8, streaming_av_align_dim=8)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(20260830)
        current = StreamingAVModule(config)
    old_state = {
        name: value.clone()
        for name, value in current.state_dict().items()
        if not name.startswith("first_stage_confidence_gate.")
    }
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(20260830)
        reloaded = StreamingAVModule(config)
    missing, unexpected = reloaded.load_state_dict(old_state, strict=False)

    assert missing
    assert all(name.startswith("first_stage_confidence_gate.") for name in missing)
    assert unexpected == []
    assert torch.equal(current.fusion.audio_projector.weight, reloaded.fusion.audio_projector.weight)
