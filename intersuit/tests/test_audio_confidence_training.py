import torch
from torch.utils.data import DataLoader

from intersuit.train.audio_confidence import (
    FrozenFeaturePack,
    GateFeatureCollator,
    FrozenFeatureManifestDataset,
    PairGroupBatchSampler,
    compute_gate_losses,
    gate_only_train_step,
    load_gate_only_checkpoint,
    load_frozen_feature_pack,
    save_gate_only_checkpoint,
    save_frozen_feature_pack,
)
from intersuit.model.streaming_av.confidence_gate import FirstStageAudioConfidenceGate


def _pack(batch=2, steps=3, hidden=4):
    router = torch.tensor(([1.0, 0.0] * ((batch + 1) // 2))[:batch])
    relevance_mask = torch.ones(batch, steps, dtype=torch.bool)
    relevance_mask[router == 0] = False
    return FrozenFeaturePack(
        audio=torch.randn(batch, steps, hidden), video=torch.randn(batch, steps, hidden),
        question=torch.randn(batch, hidden), valid_mask=torch.ones(batch, steps, dtype=torch.bool),
        router_target=router, quality_rank=torch.tensor([[2., 1., 0.]]).repeat(batch, 1),
        quality_mask=torch.ones(batch, steps, dtype=torch.bool),
        relevance_rank=torch.tensor([[2., 1., 0.]]).repeat(batch, 1),
        relevance_mask=relevance_mask,
        sync_rank=torch.ones(batch, steps),
    )


def test_feature_pack_roundtrip_and_collator(tmp_path):
    path = tmp_path / "pack.pt"
    pack = _pack()
    save_frozen_feature_pack(path, pack)
    loaded = load_frozen_feature_pack(path)
    assert loaded.audio.shape == pack.audio.shape
    batch = GateFeatureCollator()([pack.as_dict(), pack.as_dict()])
    assert batch["audio"].shape[0] == 4


def test_gate_losses_are_finite_and_sync_closed():
    torch.manual_seed(1)
    gate = FirstStageAudioConfidenceGate(hidden_size=4, projection_dim=3)
    pack = _pack()
    output = gate(pack.audio, pack.video, pack.question, valid_mask=pack.valid_mask)
    loss, metrics = compute_gate_losses(output, {key: value for key, value in pack.as_dict().items() if isinstance(value, torch.Tensor)})
    assert torch.isfinite(loss)
    assert metrics["sync_loss"].item() == 0.0


def test_relevance_rank_ignores_visual_only_rows():
    torch.manual_seed(2)
    gate = FirstStageAudioConfidenceGate(hidden_size=4, projection_dim=3)
    pack = _pack()
    output = gate(pack.audio, pack.video, pack.question, valid_mask=pack.valid_mask)
    batch = {key: value for key, value in pack.as_dict().items() if isinstance(value, torch.Tensor)}
    _, baseline = compute_gate_losses(output, batch)
    batch["relevance_rank"] = batch["relevance_rank"].clone()
    batch["relevance_rank"][1] = torch.tensor([1000.0, -1000.0, 500.0])
    _, changed = compute_gate_losses(output, batch)
    assert torch.equal(baseline["relevance_rank"], changed["relevance_rank"])


def test_pairwise_ranking_does_not_compare_unrelated_groups():
    torch.manual_seed(3)
    gate = FirstStageAudioConfidenceGate(hidden_size=4, projection_dim=3)
    pack = _pack()
    output = gate(pack.audio, pack.video, pack.question, valid_mask=pack.valid_mask)
    batch = {key: value for key, value in pack.as_dict().items() if isinstance(value, torch.Tensor)}
    batch["pair_group_id"] = ["left", "right"]
    # 每组内部 rank 为常数，跨组虽不同但不应产生排序 pair。
    batch["quality_rank"] = torch.tensor([[2.0, 2.0, 2.0], [1.0, 1.0, 1.0]])
    batch["relevance_rank"] = batch["quality_rank"].clone()
    batch["relevance_mask"] = torch.ones_like(batch["relevance_mask"])
    _, metrics = compute_gate_losses(output, batch)
    assert metrics["quality_rank"].item() == 0.0
    assert metrics["relevance_rank"].item() == 0.0


def test_gate_only_step_and_checkpoint_restore(tmp_path):
    torch.manual_seed(4)
    gate = FirstStageAudioConfidenceGate(hidden_size=4, projection_dim=3)
    optimizer = torch.optim.AdamW(gate.parameters(), lr=1e-3)
    pack = _pack()
    batch = GateFeatureCollator()([
        {**pack.as_dict(), "metadata": {"pair_group_id": "pair-a", "shifted": False}}
    ])
    loss, _ = gate_only_train_step(gate, batch, optimizer, device="cpu")
    assert torch.isfinite(loss)

    checkpoint = tmp_path / "gate.pt"
    save_gate_only_checkpoint(checkpoint, gate, optimizer, step=1)
    expected = {name: value.clone() for name, value in gate.state_dict().items()}
    with torch.no_grad():
        next(gate.parameters()).add_(1)
    assert load_gate_only_checkpoint(checkpoint, gate, optimizer) == 1
    assert all(torch.equal(gate.state_dict()[name], value) for name, value in expected.items())


def test_manifest_dataset_and_pair_group_sampler_keep_groups_isolated(tmp_path):
    records = []
    for index, group in enumerate(("a", "a", "b")):
        pack = _pack(batch=1)
        path = tmp_path / f"{index}.pt"
        save_frozen_feature_pack(
            path, pack, {"pair_group_id": group, "shifted": False}
        )
        records.append({
            "record_id": str(index), "pair_group_id": group,
            "feature_path": path.name, "feature_sha256": "unused",
        })
    manifest = tmp_path / "manifest.json"
    manifest.write_text(__import__("json").dumps({
        "schema": "as_m4_audio_confidence_feature_manifest_v1", "records": records,
    }))
    dataset = FrozenFeatureManifestDataset(manifest)
    sampler = PairGroupBatchSampler(dataset.records, shuffle=False, seed=7)
    loader = DataLoader(dataset, batch_sampler=sampler, collate_fn=GateFeatureCollator())
    batches = list(loader)
    assert [batch["audio"].shape[0] for batch in batches] == [2, 1]
    assert batches[0]["pair_group_id"] == ["a", "a"]
    assert batches[1]["pair_group_id"] == ["b"]
