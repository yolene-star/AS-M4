"""AVE D1 固定 micro-window、策略与 localization 指标协议测试。"""

from __future__ import annotations

import pytest
import torch

from intersuit.harness.ave_d1_protocol import (
    D1_BUDGETS,
    MAIN_BUDGET,
    WINDOW_COUNT,
    aggregate_evaluations,
    build_five_seed_sample_random_rankings,
    build_micro_window_timestamps,
    evaluate_selection,
    normalize_target_intervals,
    oracle_ranked_indices,
    paired_bootstrap_mean_difference,
    ranked_score_indices,
    sample_specific_random_ranking,
    select_from_ranking,
    uniform_topk_indices,
    waveform_energy_onset_scores,
)
from intersuit.harness.dynamic_selector_contract import FIXED_CONTROL_SEEDS


def test_frozen_window_and_budget_contract():
    timestamps = build_micro_window_timestamps()

    assert timestamps.shape == (10, 2)
    assert torch.equal(timestamps[0], torch.tensor([0.0, 1.0]))
    assert torch.equal(timestamps[-1], torch.tensor([9.0, 10.0]))
    assert WINDOW_COUNT == 10
    assert MAIN_BUDGET == 4
    assert D1_BUDGETS == (2, 4, 8)


def test_target_intervals_are_clipped_sorted_and_unioned():
    intervals = normalize_target_intervals(
        [
            {"event_start_sec": 3, "event_end_sec": 6},
            {"event_start_sec": 1, "event_end_sec": 4},
            {"event_start_sec": 8, "event_end_sec": 10},
        ]
    )

    assert intervals == [(1.0, 6.0), (8.0, 10.0)]
    with pytest.raises(ValueError, match="positive duration"):
        normalize_target_intervals([{"event_start_sec": 2, "event_end_sec": 2}])


def test_score_ranking_ties_break_earlier_and_k_uses_minimum():
    timestamps = build_micro_window_timestamps()
    scores = torch.tensor([0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    ranking = ranked_score_indices(scores, timestamps)

    assert ranking[:3] == [1, 2, 0]
    assert select_from_ranking(ranking, 99) == ranking


def test_uniform_centers_are_deterministic_for_all_budgets():
    indices = list(range(10))

    assert uniform_topk_indices(indices, 2) == [2, 7]
    assert uniform_topk_indices(indices, 4) == [1, 3, 6, 8]
    assert uniform_topk_indices(indices, 8) == [0, 1, 3, 4, 5, 6, 8, 9]
    assert uniform_topk_indices(indices[:3], 8) == [0, 1, 2]


def test_oracle_ranks_all_positive_windows_before_neighbours():
    timestamps = build_micro_window_timestamps()
    intervals = normalize_target_intervals(
        [{"event_start_sec": 2, "event_end_sec": 4}]
    )
    ranking = oracle_ranked_indices(timestamps, intervals)

    assert ranking[:2] == [2, 3]
    assert set(ranking) == set(range(10))


def test_random_is_sample_specific_reproducible_and_uses_fixed_seeds():
    first = build_five_seed_sample_random_rankings(range(10), "video-a")
    second = build_five_seed_sample_random_rankings(range(10), "video-a")
    other = build_five_seed_sample_random_rankings(range(10), "video-b")

    assert first == second
    assert tuple(int(seed) for seed in first) == FIXED_CONTROL_SEEDS
    assert any(first[seed] != other[seed] for seed in first)
    assert sample_specific_random_ranking(
        range(10), sample_id="video-a", seed=FIXED_CONTROL_SEEDS[0]
    ) == first[str(FIXED_CONTROL_SEEDS[0])]


def test_waveform_scores_freeze_padding_energy_and_onset_definition():
    sample_rate = 1000
    waveform = torch.zeros(9_500)
    waveform[2_000:3_000] = 1.0
    energy, onset, audit = waveform_energy_onset_scores(waveform, sample_rate)

    assert energy.shape == onset.shape == (10,)
    assert int(torch.argmax(energy).item()) == 2
    assert float(energy[2].item()) == pytest.approx(1.0)
    assert int(torch.argmax(onset).item()) == 2
    assert audit["padded_sample_count"] == 500
    assert audit["normalized_sample_count"] == 10_000


def test_selection_metrics_preserve_rank_but_emit_temporal_order():
    timestamps = build_micro_window_timestamps()
    intervals = normalize_target_intervals(
        [{"event_start_sec": 2, "event_end_sec": 4}]
    )
    result = evaluate_selection(
        strategy="synthetic",
        budget=4,
        ranked_selected_indices=[8, 3, 2, 1, 0],
        timestamps=timestamps,
        target_intervals=intervals,
    )

    assert result.selected_ranked_indices == [8, 3, 2, 1]
    assert result.selected_time_order_indices == [1, 2, 3, 8]
    assert result.window_hit == 1.0
    assert result.positive_window_recall == 1.0
    assert result.first_hit_rank == 2
    assert result.max_tiou == pytest.approx(0.5)
    assert result.compression_ratio == pytest.approx(0.4)
    with pytest.raises(ValueError, match=r"min\(K, N_valid\)"):
        evaluate_selection(
            strategy="too-short",
            budget=4,
            ranked_selected_indices=[2, 3],
            timestamps=timestamps,
            target_intervals=intervals,
        )


def test_aggregate_and_paired_bootstrap_are_reproducible():
    timestamps = build_micro_window_timestamps()
    intervals = [(2.0, 4.0)]
    hit = evaluate_selection(
        strategy="hit",
        budget=2,
        ranked_selected_indices=[2, 3],
        timestamps=timestamps,
        target_intervals=intervals,
    )
    miss = evaluate_selection(
        strategy="miss",
        budget=2,
        ranked_selected_indices=[8, 9],
        timestamps=timestamps,
        target_intervals=intervals,
    )
    aggregate = aggregate_evaluations([hit, miss])
    first = paired_bootstrap_mean_difference(
        [1.0, 1.0, 1.0, 1.0],
        [0.0, 0.0, 1.0, 0.0],
        resamples=1000,
    )
    second = paired_bootstrap_mean_difference(
        [1.0, 1.0, 1.0, 1.0],
        [0.0, 0.0, 1.0, 0.0],
        resamples=1000,
    )

    assert aggregate["window_hit_at_k"] == pytest.approx(0.5)
    assert aggregate["first_hit_missing_count"] == 1
    assert first == second
    assert first["mean_difference"] == pytest.approx(0.75)
    assert first["ci95_lower"] >= 0.0
