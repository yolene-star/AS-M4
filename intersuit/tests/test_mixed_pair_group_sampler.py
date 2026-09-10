from __future__ import annotations

from collections import Counter

from intersuit.train.audio_confidence import MixedPairGroupBatchSampler


def test_mixed_sampler_keeps_groups_and_mixes_router_labels() -> None:
    records = []
    for group, modality in (("g1", "visual"), ("g2", "audio"), ("g3", "audio_visual"), ("g4", "visual")):
        for index in range(2):
            records.append({"pair_group_id": group, "modality": modality})
    sampler = MixedPairGroupBatchSampler(records, groups_per_batch=2, shuffle=False, seed=0)
    batches = list(iter(sampler))
    assert len(batches) == 2
    first = [records[index] for index in batches[0]]
    assert Counter(row["pair_group_id"] for row in first) == {"g1": 2, "g2": 2}
    assert {0 if row["modality"] == "visual" else 1 for row in first} == {0, 1}
