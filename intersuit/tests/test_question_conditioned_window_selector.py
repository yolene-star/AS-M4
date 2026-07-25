import torch

from intersuit.model.streaming_av.question_conditioned_window_selector import (
    QuestionConditionedWindowSelector,
)


def _selector_and_inputs():
    torch.manual_seed(7)
    selector = QuestionConditionedWindowSelector(
        audio_dim=4,
        question_dim=6,
        projection_dim=5,
        top_k=3,
    )
    features = torch.randn(2, 9, 4)
    questions = torch.randn(2, 6)
    timestamps = torch.tensor([[float(i), float(i + 1)] for i in range(9)])
    mask = torch.ones(2, 9, dtype=torch.bool)
    mask[1, 7:] = False
    return selector, features, questions, timestamps, mask


def _assert_same_selection(left, right):
    assert torch.equal(left.selection_mask, right.selection_mask)
    assert torch.equal(left.selected_source_indices, right.selected_source_indices)
    assert torch.equal(left.selected_timestamps, right.selected_timestamps)
    assert torch.allclose(left.selected_features, right.selected_features)
    assert torch.allclose(left.selection_scores, right.selection_scores)


def test_selector_has_trainable_parameters_and_gradients():
    selector, features, questions, timestamps, mask = _selector_and_inputs()
    assert sum(parameter.numel() for parameter in selector.parameters()) > 0

    output = selector(features, questions, timestamps, valid_mask=mask)
    loss = output.all_scores[mask].sum()
    loss.backward()

    assert selector.audio_projection.weight.grad is not None
    assert selector.question_projection.weight.grad is not None
    assert torch.isfinite(selector.audio_projection.weight.grad).all()
    assert torch.isfinite(selector.question_projection.weight.grad).all()


def test_whole_prefix_equals_step_by_step_causal_replay():
    selector, features, questions, timestamps, mask = _selector_and_inputs()
    whole = selector(features, questions, timestamps, valid_mask=mask)

    state = None
    replay = None
    for index in range(features.shape[1]):
        replay = selector(
            features[:, index : index + 1],
            questions,
            timestamps[index : index + 1],
            valid_mask=mask[:, index : index + 1],
            state=state,
        )
        state = replay.state

    _assert_same_selection(whole, replay)
    assert replay.state.retained_features.shape[1] == selector.top_k
    assert torch.all(replay.state.retained_mask.sum(dim=1) <= selector.top_k)


def test_causal_replay_is_chunk_size_invariant():
    selector, features, questions, timestamps, mask = _selector_and_inputs()
    outputs = []
    for chunk_size in (1, 2, 4, 9):
        state = None
        output = None
        for start in range(0, features.shape[1], chunk_size):
            end = min(start + chunk_size, features.shape[1])
            output = selector(
                features[:, start:end],
                questions,
                timestamps[start:end],
                valid_mask=mask[:, start:end],
                source_indices=torch.arange(start, end),
                state=state,
            )
            state = output.state
        outputs.append(output)

    for output in outputs[1:]:
        _assert_same_selection(outputs[0], output)


def test_future_perturbation_cannot_change_saved_prefix_output():
    selector, features, questions, timestamps, mask = _selector_and_inputs()
    prefix = selector(features[:, :5], questions, timestamps[:5], valid_mask=mask[:, :5])
    changed = features.clone()
    changed[:, 5:] = changed[:, 5:] * 1000.0 + 500.0
    changed_prefix = selector(changed[:, :5], questions, timestamps[:5], valid_mask=mask[:, :5])

    _assert_same_selection(prefix, changed_prefix)


def test_ties_use_timestamp_then_source_index_and_outputs_are_chronological():
    selector = QuestionConditionedWindowSelector(2, 2, projection_dim=2, top_k=2)
    with torch.no_grad():
        selector.audio_projection.weight.copy_(torch.eye(2))
        selector.question_projection.weight.copy_(torch.eye(2))
    features = torch.tensor([[[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]])
    question = torch.tensor([[1.0, 0.0]])
    timestamps = torch.tensor([[[2.0, 3.0], [0.0, 1.0], [0.0, 1.0]]])
    source_indices = torch.tensor([[2, 1, 0]])

    output = selector(features, question, timestamps, source_indices=source_indices)

    assert output.selected_source_indices.tolist() == [[0, 1]]
    assert output.selection_mask.tolist() == [[True, True]]


def test_invalid_windows_never_enter_memory():
    selector = QuestionConditionedWindowSelector(2, 2, projection_dim=2, top_k=3)
    features = torch.randn(1, 4, 2)
    question = torch.randn(1, 2)
    timestamps = torch.tensor([[0.0, 1.0], [1.0, 2.0], [2.0, 3.0], [3.0, 4.0]])
    mask = torch.tensor([[True, False, True, False]])

    output = selector(features, question, timestamps, valid_mask=mask)

    assert output.selection_mask.sum().item() == 2
    assert set(output.selected_source_indices[output.selection_mask].tolist()) == {0, 2}
    assert torch.all(output.selected_source_indices[~output.selection_mask] == -1)
    assert torch.isfinite(output.all_scores).all()
    assert torch.isfinite(output.selection_scores).all()
