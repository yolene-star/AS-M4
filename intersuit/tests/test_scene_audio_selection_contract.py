from __future__ import annotations

import pytest
import torch
from types import SimpleNamespace

from intersuit.model.streaming_av.selection_contract import (
    DEFAULT_SELECTION_POLICY_REGISTRY,
    EXTERNAL_PRE_ENCODER_STAGE,
    SelectionRequest,
    selection_results_from_model_output,
)


def _request(**overrides):
    values = {
        "sample_id": "sample-a",
        "source_indices": torch.arange(5),
        "timestamps": torch.tensor([[float(i), float(i + 1)] for i in range(5)]),
        "valid_mask": torch.tensor([True, True, True, True, False]),
        "top_k": 2,
    }
    values.update(overrides)
    return SelectionRequest(**values)


def test_fixed_all_selects_every_valid_window_and_reports_external_stage():
    result = DEFAULT_SELECTION_POLICY_REGISTRY.select("fixed_all", _request(top_k=None))

    assert result.selected_source_indices.tolist() == [0, 1, 2, 3]
    assert result.selected_window_count == result.valid_window_count == 4
    assert result.full_window_count == 5
    assert result.selection_stage == EXTERNAL_PRE_ENCODER_STAGE
    assert result.to_trace()["policy_metadata"]["all_windows"] is True


def test_external_indices_are_validated_and_restored_to_time_order():
    result = DEFAULT_SELECTION_POLICY_REGISTRY.select(
        "external_indices",
        _request(explicit_selected_source_indices=(1, 3)),
    )

    assert result.selected_positions.tolist() == [1, 3]
    assert result.selected_source_indices.tolist() == [1, 3]

    with pytest.raises(ValueError, match="唯一且递增"):
        DEFAULT_SELECTION_POLICY_REGISTRY.select(
            "external_indices",
            _request(explicit_selected_source_indices=(3, 1)),
        )


def test_random_is_reproducible_per_sample_and_seed():
    first = DEFAULT_SELECTION_POLICY_REGISTRY.select("random_topk", _request(seed=17))
    replay = DEFAULT_SELECTION_POLICY_REGISTRY.select("random_topk", _request(seed=17))
    other_sample = DEFAULT_SELECTION_POLICY_REGISTRY.select(
        "random_topk", _request(sample_id="sample-b", seed=17)
    )

    assert torch.equal(first.selected_source_indices, replay.selected_source_indices)
    assert first.policy_metadata["derived_seed_sha256"] == replay.policy_metadata["derived_seed_sha256"]
    assert first.policy_metadata["derived_seed_sha256"] != other_sample.policy_metadata["derived_seed_sha256"]


def test_score_policy_has_stable_tie_break_and_chronological_output():
    request = _request(scores={"energy": torch.tensor([0.1, 0.9, 0.9, 0.2, 100.0])})
    result = DEFAULT_SELECTION_POLICY_REGISTRY.select("energy_topk", request)

    assert result.selected_source_indices.tolist() == [1, 2]
    assert result.selection_scores.tolist() == pytest.approx([0.9, 0.9])


def test_oracle_is_fail_closed_outside_explicit_diagnostic_inference():
    intervals = torch.tensor([[2.1, 2.9]])
    with pytest.raises(PermissionError, match="diagnostic inference"):
        DEFAULT_SELECTION_POLICY_REGISTRY.select(
            "oracle_topk",
            _request(target_intervals=intervals, production=True),
        )
    with pytest.raises(PermissionError, match="diagnostic inference"):
        DEFAULT_SELECTION_POLICY_REGISTRY.select(
            "oracle_topk",
            _request(target_intervals=intervals, diagnostic=True, training=True),
        )
    with pytest.raises(ValueError, match="target_intervals"):
        DEFAULT_SELECTION_POLICY_REGISTRY.select(
            "oracle_topk",
            _request(diagnostic=True),
        )

    result = DEFAULT_SELECTION_POLICY_REGISTRY.select(
        "oracle_topk",
        _request(target_intervals=intervals, diagnostic=True),
    )
    assert 2 in result.selected_source_indices.tolist()
    assert result.policy_metadata["uses_ground_truth"] is True


def test_unknown_policy_and_invalid_shapes_fail_closed():
    with pytest.raises(ValueError, match="未知 selection policy"):
        DEFAULT_SELECTION_POLICY_REGISTRY.select("made_up", _request())
    with pytest.raises(ValueError, match="timestamps"):
        _request(timestamps=torch.zeros(4, 2))


def test_contract_normalizes_control_evidence_to_detached_cpu_tensors():
    timestamps = torch.tensor(
        [[float(i), float(i + 1)] for i in range(5)],
        requires_grad=True,
    )
    scores = torch.arange(5, dtype=torch.float32, requires_grad=True)

    request = _request(timestamps=timestamps, scores={"energy": scores})

    assert request.timestamps.device.type == "cpu"
    assert request.scores["energy"].device.type == "cpu"
    assert not request.timestamps.requires_grad
    assert not request.scores["energy"].requires_grad


def test_internal_model_output_is_adapted_to_the_same_selection_result_contract():
    output = SimpleNamespace(
        selection_mask=torch.tensor([[True, True, False]]),
        selected_source_indices=torch.tensor([[1, 3, -1]]),
        selection_scores=torch.tensor([[0.9, 0.8, 0.0]]),
        selected_timestamps=torch.tensor([[[1.0, 2.0], [3.0, 4.0], [0.0, 0.0]]]),
        source_weights=torch.tensor(
            [[[0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0, 0.0]]]
        ),
    )

    result = selection_results_from_model_output("current_micro_topk", output)[0]

    assert result.selection_stage == "internal_model"
    assert result.selected_positions.tolist() == [1, 3]
    assert result.selected_source_indices.tolist() == [1, 3]
    assert result.to_trace()["policy_name"] == "current_micro_topk"
