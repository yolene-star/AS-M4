"""CPU tests for prediction generation from an E0-E7 matrix plan."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RUNNER_PATH = ROOT / "intersuit" / "harness" / "runners" / "run_predictions_from_plan.py"
SPEC = importlib.util.spec_from_file_location("as_m4_run_predictions_from_plan", RUNNER_PATH)
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_checkpoint_config_overrides_ambiguous_run_directory_name(tmp_path):
    checkpoint = tmp_path / "checkpoint-150"
    _write_json(
        checkpoint / "config.json",
        {"model_type": "qwen2", "architectures": ["LlavaQwenForCausalLM"]},
    )

    assert runner.resolve_model_name_from_checkpoint_config(checkpoint, "checkpoint-150") == "LongVA-Qwen2"


def test_checkpoint_config_keeps_path_fallback_for_non_qwen_models(tmp_path):
    checkpoint = tmp_path / "checkpoint-150"
    _write_json(checkpoint / "config.json", {"model_type": "llava_llama"})

    assert runner.resolve_model_name_from_checkpoint_config(checkpoint, "checkpoint-150") == "checkpoint-150"


def test_iter_qa_samples_extracts_human_gpt_pairs(tmp_path):
    manifest = tmp_path / "manifest.json"
    _write_json(
        manifest,
        [
            {
                "id": "s0",
                "video_features": "v.pt",
                "conversations": [
                    {"from": "human", "value": "Q1<image>"},
                    {"from": "gpt", "value": "A1"},
                    {"from": "human", "value": "Q2"},
                    {"from": "gpt", "value": "A2"},
                ],
            }
        ],
    )

    rows = runner.iter_qa_samples(manifest)

    assert [row["id"] for row in rows] == ["s0_turn0", "s0_turn1"]
    assert rows[0]["answer"] == "A1"


def test_iter_qa_samples_preserves_video_path(tmp_path):
    manifest = tmp_path / "manifest.json"
    _write_json(
        manifest,
        [
            {
                "id": "water",
                "video_path": "local_demo/assets/water.mp4",
                "video_max_frames": 8,
                "scene_audio_path": "local_demo/assets/water.mp4",
                "scene_audio_selected_indices": [1, 3],
                "generation_mode": "parallel",
                "context": "Can you describe the video?",
                "new_query": "How many people in the video?",
                "new_query_pos": 20,
                "accept_contains": ["two people"],
                "accept_regex": ["\\btwo\\b"],
                "conversations": [
                    {"from": "human", "value": "<image>\nHow many people in the video?"},
                    {"from": "gpt", "value": "two"},
                ],
            }
        ],
    )

    rows = runner.iter_qa_samples(manifest)

    assert rows[0]["video_path"] == "local_demo/assets/water.mp4"
    assert rows[0]["video_max_frames"] == 8
    assert rows[0]["scene_audio_path"] == "local_demo/assets/water.mp4"
    assert rows[0]["scene_audio_selected_indices"] == [1, 3]
    assert rows[0]["video_features"] is None
    assert rows[0]["generation_mode"] == "parallel"
    assert rows[0]["context"] == "Can you describe the video?"
    assert rows[0]["new_query"] == "How many people in the video?"
    assert rows[0]["accept_contains"] == ["two people"]
    assert rows[0]["accept_regex"] == ["\\btwo\\b"]


def test_load_scene_audio_applies_explicit_indices_and_reuses_cache(monkeypatch, tmp_path):
    from intersuit.streaming import audio_stream

    calls = []

    def fake_load(path, sample_rate, mono):
        calls.append((str(path), sample_rate, mono))
        return torch.arange(3 * 16000, dtype=torch.float32), 16000

    monkeypatch.setattr(audio_stream, "load_scene_audio", fake_load)
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"fixture")
    qa = {
        "scene_audio_path": str(media),
        "scene_audio_sample_rate": 16000,
        "scene_audio_window_sec": 1.0,
        "scene_audio_hop_sec": 1.0,
        "scene_audio_expected_duration_sec": 2.5,
        "scene_audio_selected_indices": [0, 2],
    }
    cache = OrderedDict()

    first, timestamps, metadata = runner._load_scene_audio_tensor(qa, audio_cache=cache)
    second, _, _ = runner._load_scene_audio_tensor(qa, audio_cache=cache)

    assert first.shape == second.shape == (2, 16000)
    assert timestamps.tolist() == [[0.0, 1.0], [2.0, 2.5]]
    assert metadata["full_window_count"] == 3
    assert metadata["waveform_num_samples"] == 40000
    assert metadata["decoded_waveform_num_samples"] == 48000
    assert metadata["selected_source_indices"] == [0, 2]
    assert metadata["selection_stage"] == "external_pre_encoder"
    assert metadata["selection_trace"]["policy_name"] == "external_indices"
    assert len(calls) == 1


def test_load_scene_audio_fixed_all_is_true_all_and_auditable(monkeypatch, tmp_path):
    from intersuit.streaming import audio_stream

    monkeypatch.setattr(
        audio_stream,
        "load_scene_audio",
        lambda path, sample_rate, mono: (torch.zeros(3 * 16000), 16000),
    )
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"fixture")
    qa = {
        "id": "all-windows",
        "scene_audio_path": str(media),
        "scene_audio_sample_rate": 16000,
        "scene_audio_window_sec": 1.0,
        "scene_audio_hop_sec": 1.0,
        "scene_audio_selection_policy": "fixed_all",
    }

    samples, timestamps, metadata = runner._load_scene_audio_tensor(qa, audio_cache=OrderedDict())

    assert samples.shape[0] == timestamps.shape[0] == 3
    assert metadata["window_count"] == metadata["full_window_count"] == 3
    assert metadata["selection_trace"]["selected_window_count"] == 3
    assert metadata["selection_trace"]["selected_source_indices"] == [0, 1, 2]


def test_runner_oracle_policy_is_diagnostic_only():
    qa = {
        "id": "oracle",
        "scene_audio_selection_policy": "oracle_topk",
        "scene_audio_selection_top_k": 1,
        "scene_audio_target_intervals": [[1.0, 2.0]],
    }
    samples = torch.zeros(3, 16)
    timestamps = torch.tensor([[0.0, 1.0], [1.0, 2.0], [2.0, 3.0]])

    with pytest.raises(PermissionError, match="diagnostic inference"):
        runner._apply_external_scene_audio_selection(qa, samples, timestamps)

    qa["scene_audio_selection_diagnostic"] = True
    selected, _, trace = runner._apply_external_scene_audio_selection(qa, samples, timestamps)
    assert selected.shape[0] == 1
    assert trace["selected_source_indices"] == [1]
    assert trace["policy_metadata"]["uses_ground_truth"] is True


@pytest.mark.parametrize("indices", [[1, 1], [2, 0], [0, 3], []])
def test_load_scene_audio_rejects_invalid_explicit_indices(monkeypatch, tmp_path, indices):
    from intersuit.streaming import audio_stream

    monkeypatch.setattr(
        audio_stream,
        "load_scene_audio",
        lambda path, sample_rate, mono: (torch.zeros(3 * 16000), 16000),
    )
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"fixture")
    qa = {
        "scene_audio_path": str(media),
        "scene_audio_sample_rate": 16000,
        "scene_audio_window_sec": 1.0,
        "scene_audio_hop_sec": 1.0,
        "scene_audio_selected_indices": indices,
    }

    with pytest.raises(ValueError, match="scene_audio_selected_indices"):
        runner._load_scene_audio_tensor(qa, audio_cache=OrderedDict())


def test_prediction_correct_supports_contains_and_regex():
    contains_qa = {"answer": "two", "accept_contains": ["two people"]}
    regex_qa = {"answer": "kitchen", "accept_regex": [r"kitchen|sink"]}
    combined_qa = {"answer": "two", "accept_contains": ["two people"], "accept_regex": [r"\babout\s*2\b"]}
    exact_qa = {"answer": "two"}
    choice_qa = {"answer": "25s-33s", "choices": {"A": "0s-8s", "B": "9s-16s", "C": "17s-24s", "D": "25s-33s"}, "accept_contains": ["25s-33s"]}

    assert runner.prediction_correct("There are two people in the video.", contains_qa)
    assert runner.prediction_correct("The video shows a sink.", regex_qa)
    assert runner.prediction_correct("The video seems to be about2", combined_qa)
    assert runner.prediction_correct("two", exact_qa)
    assert runner.prediction_correct("D", choice_qa)
    assert not runner.prediction_correct("B", choice_qa)
    assert not runner.prediction_correct("three people", contains_qa)


def test_prediction_correct_label_terms_uses_phrase_boundaries():
    assert runner.prediction_correct(
        "The video shows a bus on the road.",
        {"answer": "Bus"},
        scorer="label_terms",
    )
    assert runner.prediction_correct(
        "Frying",
        {"answer": "Frying (food)"},
        scorer="label_terms",
    )
    assert runner.prediction_correct(
        "A man speaking can be heard.",
        {"answer": "Male speech, man speaking"},
        scorer="label_terms",
    )
    assert not runner.prediction_correct(
        "Business district",
        {"answer": "Bus"},
        scorer="label_terms",
    )
    assert not runner.prediction_correct(
        "A detailed answer",
        {"answer": "A"},
        scorer="label_terms",
    )


def test_jsonable_diagnostics_converts_tensors():
    value = [{"gate_mean": runner.torch.tensor(0.5), "gate": runner.torch.tensor([[0.25, 0.75]])}, None]

    converted = runner.jsonable_diagnostics(value)

    assert converted[0]["gate_mean"] == 0.5
    assert converted[0]["gate"] == [[0.25, 0.75]]


def test_oracle_backend_writes_prediction_files(tmp_path):
    manifest = tmp_path / "manifest.json"
    _write_json(
        manifest,
        [
            {
                "id": "s0",
                "video_features": "v.pt",
                "conversations": [
                    {"from": "human", "value": "Q<image>"},
                    {"from": "gpt", "value": "A"},
                ],
            }
        ],
    )
    plan = tmp_path / "plan.jsonl"
    with plan.open("w", encoding="utf-8") as f:
        for idx in range(8):
            exp_id = f"E{idx}"
            f.write(
                json.dumps(
                    {
                        "id": exp_id,
                        "manifest": str(manifest),
                        "output_jsonl": str(tmp_path / "pred" / f"{exp_id}.jsonl"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    result = runner.run_predictions(
        plan,
        backend="oracle",
        limit=1,
        feature_root=tmp_path,
        device="cpu",
        max_new_tokens=4,
    )

    assert result["status"] == "pass"
    e2_rows = (tmp_path / "pred" / "E2.jsonl").read_text(encoding="utf-8").splitlines()
    e3_rows = (tmp_path / "pred" / "E3.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(e2_rows[0])["correct"] is True
    assert json.loads(e3_rows[0])["correct"] is False


def test_oracle_backend_can_skip_locked_manifest_prefix(tmp_path):
    manifest = tmp_path / "manifest.json"
    _write_json(
        manifest,
        [
            {
                "id": sample_id,
                "conversations": [
                    {"from": "human", "value": "Q"},
                    {"from": "gpt", "value": "A"},
                ],
            }
            for sample_id in ("s0", "s1", "s2")
        ],
    )
    output = tmp_path / "pred.jsonl"
    plan = tmp_path / "plan.jsonl"
    plan.write_text(
        json.dumps({"id": "E2", "manifest": str(manifest), "output_jsonl": str(output)}) + "\n",
        encoding="utf-8",
    )

    runner.run_predictions(
        plan,
        backend="oracle",
        limit=None,
        skip=1,
        feature_root=tmp_path,
        device="cpu",
        max_new_tokens=4,
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["sample_id"] for row in rows] == ["s1", "s2"]


def test_oracle_backend_resume_validates_prefix_and_appends(tmp_path):
    manifest = tmp_path / "manifest.json"
    _write_json(
        manifest,
        [
            {
                "id": sample_id,
                "conversations": [
                    {"from": "human", "value": "Q"},
                    {"from": "gpt", "value": "A"},
                ],
            }
            for sample_id in ("s0", "s1", "s2")
        ],
    )
    output = tmp_path / "pred.jsonl"
    plan = tmp_path / "plan.jsonl"
    plan.write_text(
        json.dumps({"id": "E2", "manifest": str(manifest), "output_jsonl": str(output)}) + "\n",
        encoding="utf-8",
    )
    runner.run_predictions(
        plan, backend="oracle", limit=1, feature_root=tmp_path, device="cpu",
        max_new_tokens=4, resume=True,
    )
    runner.run_predictions(
        plan, backend="oracle", limit=None, feature_root=tmp_path, device="cpu",
        max_new_tokens=4, resume=True,
    )
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["sample_id"] for row in rows] == ["s0", "s1", "s2"]


def test_oracle_backend_resume_rejects_nonprefix_output(tmp_path):
    manifest = tmp_path / "manifest.json"
    _write_json(
        manifest,
        [{"id": "s0", "conversations": [{"from": "human", "value": "Q"}, {"from": "gpt", "value": "A"}]}],
    )
    output = tmp_path / "pred.jsonl"
    output.write_text(json.dumps({"sample_id": "wrong", "experiment_id": "E2"}) + "\n", encoding="utf-8")
    plan = tmp_path / "plan.jsonl"
    plan.write_text(json.dumps({"id": "E2", "manifest": str(manifest), "output_jsonl": str(output)}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="resume 输出前缀"):
        runner.run_predictions(
            plan, backend="oracle", limit=None, feature_root=tmp_path, device="cpu",
            max_new_tokens=4, resume=True,
        )


def test_dry_run_does_not_create_prediction_files(tmp_path):
    plan = tmp_path / "plan.jsonl"
    pred = tmp_path / "pred.jsonl"
    plan.write_text(json.dumps({"id": "E0", "manifest": str(tmp_path / "missing.json"), "output_jsonl": str(pred)}) + "\n", encoding="utf-8")

    result = runner.run_predictions(plan, backend="oracle", limit=1, feature_root=tmp_path, device="cpu", max_new_tokens=4, dry_run=True)

    assert result["dry_run"] is True
    assert not pred.exists()


def test_manifest_sha256_lock_rejects_changed_manifest(tmp_path):
    manifest = tmp_path / "manifest.json"
    _write_json(
        manifest,
        [
            {
                "id": "s0",
                "conversations": [
                    {"from": "human", "value": "Q"},
                    {"from": "gpt", "value": "A"},
                ],
            }
        ],
    )
    plan = tmp_path / "plan.jsonl"
    plan.write_text(
        json.dumps(
            {
                "id": "LOCKED",
                "manifest": str(manifest),
                "manifest_sha256": "0" * 64,
                "output_jsonl": str(tmp_path / "predictions.jsonl"),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="manifest SHA256 不匹配"):
        runner.run_predictions(
            plan,
            backend="oracle",
            limit=1,
            feature_root=tmp_path,
            device="cpu",
            max_new_tokens=4,
        )


def test_experiment_filter_runs_only_requested_ids(tmp_path):
    manifest = tmp_path / "manifest.json"
    _write_json(
        manifest,
        [
            {
                "id": "s0",
                "video_features": "v.pt",
                "conversations": [
                    {"from": "human", "value": "Q<image>"},
                    {"from": "gpt", "value": "A"},
                ],
            }
        ],
    )
    plan = tmp_path / "plan.jsonl"
    with plan.open("w", encoding="utf-8") as f:
        for exp_id in ("E1", "E2"):
            f.write(
                json.dumps(
                    {
                        "id": exp_id,
                        "manifest": str(manifest),
                        "output_jsonl": str(tmp_path / "pred" / f"{exp_id}.jsonl"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    result = runner.run_predictions(
        plan,
        backend="oracle",
        limit=1,
        feature_root=tmp_path,
        device="cpu",
        max_new_tokens=4,
        experiments={"E2"},
    )

    assert result["outputs"] == [str(tmp_path / "pred" / "E2.jsonl")]
    assert not (tmp_path / "pred" / "E1.jsonl").exists()
    assert (tmp_path / "pred" / "E2.jsonl").exists()


def test_apply_audio_condition_builds_counterfactual_audio():
    qa0 = {
        "id": "s0_turn0",
        "scene_audio": [[1.0, 1.0], [2.0, 2.0]],
        "scene_audio_timestamps": [[0.0, 1.0], [1.0, 2.0]],
    }
    qa1 = {
        "id": "s1_turn0",
        "scene_audio": [[9.0, 9.0], [8.0, 8.0]],
        "scene_audio_timestamps": [[10.0, 11.0], [11.0, 12.0]],
    }
    pool = [qa0, qa1]

    mismatched = runner.apply_audio_condition(qa0, {"audio_condition": "mismatched"}, pool, 0)
    noisy = runner.apply_audio_condition(qa0, {"audio_condition": "noisy"}, pool, 0)
    shifted = runner.apply_audio_condition(qa0, {"audio_condition": "shifted"}, pool, 0)
    muted = runner.apply_audio_condition(qa0, {"audio_condition": "muted"}, pool, 0)

    assert mismatched["scene_audio"] == qa1["scene_audio"]
    assert noisy["scene_audio"] != qa0["scene_audio"]
    assert len(noisy["scene_audio"]) == len(qa0["scene_audio"])
    assert shifted["scene_audio"] == [[2.0, 2.0], [1.0, 1.0]]
    assert shifted["scene_audio_timestamps"] == [[1.0, 2.0], [0.0, 1.0]]
    assert muted["scene_audio"] is None


def test_mismatched_condition_replaces_path_backed_audio():
    qa0 = {
        "scene_audio_path": "first.mp4",
        "scene_audio_sample_rate": 16000,
        "scene_audio_window_sec": 1.0,
        "scene_audio_hop_sec": 0.5,
    }
    qa1 = {
        "scene_audio_path": "second.mp4",
        "scene_audio_sample_rate": 8000,
        "scene_audio_window_sec": 2.0,
        "scene_audio_hop_sec": 1.0,
    }

    mismatched = runner.apply_audio_condition(
        qa0,
        {"audio_condition": "mismatched"},
        [qa0, qa1],
        0,
    )

    assert mismatched["scene_audio_path"] == "second.mp4"
    assert mismatched["scene_audio_sample_rate"] == 8000
    assert mismatched["scene_audio_window_sec"] == 2.0
    assert mismatched["scene_audio_hop_sec"] == 1.0


def test_mismatched_condition_skips_rows_without_audio():
    qa0 = {
        "id": "first_turn0",
        "sample_id": "first",
        "scene_audio_path": "first.mp4",
    }
    no_audio = {
        "id": "silent_turn0",
        "sample_id": "silent",
        "scene_audio_path": None,
    }
    qa2 = {
        "id": "third_turn0",
        "sample_id": "third",
        "scene_audio_path": "third.mp4",
    }

    mismatched = runner.apply_audio_condition(
        qa0,
        {"audio_condition": "mismatched"},
        [qa0, no_audio, qa2],
        0,
    )

    assert mismatched["scene_audio_path"] == "third.mp4"


def test_mismatched_condition_prefers_same_task_with_different_event():
    qa0 = {
        "id": "first_turn0",
        "sample_id": "first",
        "physical_media_id": "media-1",
        "scene_audio_path": "first.mp4",
        "task_type": "audio",
        "event_label": "dog",
    }
    same_event = {
        "id": "second_turn0",
        "sample_id": "second",
        "physical_media_id": "media-2",
        "scene_audio_path": "second.mp4",
        "task_type": "audio",
        "event_label": "dog",
    }
    different_event = {
        "id": "third_turn0",
        "sample_id": "third",
        "physical_media_id": "media-3",
        "scene_audio_path": "third.mp4",
        "task_type": "audio",
        "event_label": "piano",
    }

    mismatched = runner.apply_audio_condition(
        qa0,
        {"audio_condition": "mismatched"},
        [qa0, same_event, different_event],
        0,
    )

    assert mismatched["scene_audio_path"] == "third.mp4"
    assert mismatched["mismatch_source_physical_media_id"] == "media-3"
    assert mismatched["mismatch_source_event_label"] == "piano"


def test_scene_audio_backend_is_installed_once_per_signature(monkeypatch):
    import intersuit.model.scene_audio_encoder.builder as encoder_builder
    import intersuit.model.streaming_av.builder as streaming_builder

    calls = {"encoder": 0, "streaming": 0}

    def build_encoder(config):
        calls["encoder"] += 1
        return torch.nn.Linear(2, 2)

    def build_streaming(config):
        calls["streaming"] += 1
        return torch.nn.Linear(2, 2)

    monkeypatch.setattr(encoder_builder, "build_scene_audio_encoder", build_encoder)
    monkeypatch.setattr(streaming_builder, "build_streaming_av_module", build_streaming)

    class FakeModel:
        def __init__(self):
            self.config = SimpleNamespace(hidden_size=4)
            self.body = SimpleNamespace(streaming_av_module=None)
            self.weight = torch.nn.Parameter(torch.ones(1))

        def get_model(self):
            return self.body

        def parameters(self):
            return iter([self.weight])

    model = FakeModel()
    env = {
        "AS_M4_SCENE_AUDIO_ENCODER_TYPE": "beats",
        "AS_M4_SCENE_AUDIO_BEATS_CHECKPOINT": "beats.pt",
        "AS_M4_SCENE_AUDIO_BEATS_CODE_ROOT": "beats_source",
    }

    assert runner.maybe_replace_scene_audio_encoder(model, env) is True
    assert runner.maybe_replace_scene_audio_encoder(model, env) is False
    assert calls == {"encoder": 1, "streaming": 1}
    assert model.config.scene_audio_hidden_size == 4


def test_generated_token_slice_not_empty_when_tokens_exist():
    input_ids = torch.tensor([[10, 11, 12]])
    generated_only = torch.tensor([[21, 22]])
    full_sequence = torch.tensor([[10, 11, 12, 21, 22]])

    assert runner.extract_generated_token_ids(generated_only, input_ids, "generate").tolist() == [[21, 22]]
    assert runner.extract_generated_token_ids(full_sequence, input_ids, "generate").tolist() == [[21, 22]]


class _FakeTokenizer:
    eos_token_id = 2

    def batch_decode(self, ids, skip_special_tokens):
        values = ids[0].tolist()
        if skip_special_tokens:
            return ["answer" if 42 in values else ""]
        return ["<eos>" if values == [2] else "answer<eos>"]

    def convert_ids_to_tokens(self, token_id):
        return {2: "<eos>", 42: "answer"}.get(token_id, str(token_id))


def test_decode_preserves_non_special_generated_text():
    prediction, debug = runner.decode_generated_tokens(_FakeTokenizer(), torch.tensor([[42, 2]]))

    assert prediction == "answer"
    assert debug["decode_skip_special_tokens_false"] == "answer<eos>"


def test_first_eos_is_reported():
    prediction, debug = runner.decode_generated_tokens(_FakeTokenizer(), torch.tensor([[2]]))

    assert prediction == ""
    assert debug["first_token_is_eos"] is True
    assert debug["first_new_token"] == "<eos>"


def test_debug_audio_waveform_conditions_preserve_length_and_zero_pad_shifts():
    waveform = torch.arange(5, dtype=torch.float32)

    original = runner._apply_debug_waveform_condition(waveform, sample_rate=1, condition="original")
    silence = runner._apply_debug_waveform_condition(waveform, sample_rate=1, condition="silence")
    delayed = runner._apply_debug_waveform_condition(waveform, sample_rate=1, condition="shift", shift_seconds=2)
    advanced = runner._apply_debug_waveform_condition(waveform, sample_rate=1, condition="shift", shift_seconds=-2)

    assert torch.equal(original, waveform)
    assert torch.equal(silence, torch.zeros_like(waveform))
    assert delayed.tolist() == [0.0, 0.0, 0.0, 1.0, 2.0]
    assert advanced.tolist() == [2.0, 3.0, 4.0, 0.0, 0.0]
    assert all(value.shape == waveform.shape for value in (original, silence, delayed, advanced))


def test_first_token_logits_reports_focus_tokens_and_ranks():
    class Tokenizer:
        eos_token_id = 5

        def encode(self, text, add_special_tokens=False):
            return {"A": [0], "B": [1], "C": [2], "D": [3]}[text]

        def convert_ids_to_tokens(self, token_id):
            return str(token_id)

    debug = runner._first_token_logits_debug(torch.tensor([[4.0, 3.0, 2.0, 1.0, 0.0, -1.0]]), Tokenizer())

    assert debug["focus_tokens"]["A"]["rank"] == 1
    assert debug["focus_tokens"]["D"]["rank"] == 4
    assert debug["focus_tokens"]["EOS"]["rank"] == 6
    assert [item["token_id"] for item in debug["top10"]] == [0, 1, 2, 3, 4, 5]


def test_frozen_runtime_hash_accepts_original_model_without_audio_modules():
    class OriginalModel:
        def get_model(self):
            return SimpleNamespace()

    assert runner.frozen_runtime_module_sha256(OriginalModel()) == {
        "scene_audio_encoder": "__absent__",
        "streaming_av_module": "__absent__",
    }


def test_choice_prompt_contains_assistant_boundary():
    from intersuit.conversation import conv_templates

    query, choices = runner.format_choice_query("When is the word heard?", {"A": "0s-8s", "B": "9s-16s", "C": "17s-24s", "D": "25s-33s"})
    conv = conv_templates["qwen_1_5"].copy()
    conv.append_message(conv.roles[0], f"<image>\n{query}")
    conv.append_message(conv.roles[1], None)
    prompt = conv.get_prompt()

    assert choices == "A. 0s-8s\nB. 9s-16s\nC. 17s-24s\nD. 25s-33s"
    assert prompt.endswith("<|im_start|>assistant\n")
    assert prompt.count("<image>") == 1


def test_prediction_writer_does_not_drop_text(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    _write_json(
        manifest,
        [{"id": "s0", "conversations": [{"from": "human", "value": "<image>\nQ"}, {"from": "gpt", "value": "A"}]}],
    )
    prediction_path = tmp_path / "predictions.jsonl"
    plan = tmp_path / "plan.jsonl"
    plan.write_text(json.dumps({"id": "E0", "manifest": str(manifest), "model_path": "unused", "output_jsonl": str(prediction_path)}) + "\n", encoding="utf-8")
    monkeypatch.setattr(runner, "model_prediction", lambda *args, **kwargs: ("kept text", None, {"prompt": {}, "tokens": {}}))

    runner.run_predictions(plan, backend="model", limit=1, feature_root=tmp_path, device="cpu", max_new_tokens=4)

    row = json.loads(prediction_path.read_text(encoding="utf-8"))
    assert row["prediction"] == "kept text"
    assert row["runtime_seconds"] >= 0
    assert row["cuda_device"] is None
    assert row["cuda_peak_memory_allocated_bytes"] is None
    assert row["cuda_peak_memory_reserved_bytes"] is None


def test_runtime_observation_reads_cuda_peak_memory(monkeypatch):
    calls = []
    monkeypatch.setattr(runner.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(runner.torch.cuda, "synchronize", lambda device: calls.append(("sync", str(device))))
    monkeypatch.setattr(runner.torch.cuda, "reset_peak_memory_stats", lambda device: calls.append(("reset", str(device))))
    monkeypatch.setattr(runner.torch.cuda, "max_memory_allocated", lambda device: 123)
    monkeypatch.setattr(runner.torch.cuda, "max_memory_reserved", lambda device: 456)

    started_at, device = runner.begin_runtime_observation("cuda:0")
    observation = runner.finish_runtime_observation(started_at, device)

    assert calls == [("sync", "cuda:0"), ("reset", "cuda:0"), ("sync", "cuda:0")]
    assert observation["runtime_seconds"] >= 0
    assert observation["cuda_device"] == "cuda:0"
    assert observation["cuda_peak_memory_allocated_bytes"] == 123
    assert observation["cuda_peak_memory_reserved_bytes"] == 456


def test_model_backend_restores_experiment_environment(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    _write_json(
        manifest,
        [{"id": "s0", "conversations": [{"from": "human", "value": "Q"}, {"from": "gpt", "value": "A"}]}],
    )
    plan = tmp_path / "plan.jsonl"
    observed = []
    rows = [
        {"id": "gate0", "env": {"AS_M4_FORCE_AUDIO_GATE": "0"}},
        {"id": "oracle", "env": {"AS_M4_ENABLE_SCENE_AUDIO": "1"}},
    ]
    plan.write_text(
        "".join(
            json.dumps(
                {
                    **row,
                    "manifest": str(manifest),
                    "model_path": "unused",
                    "output_jsonl": str(tmp_path / f"{row['id']}.jsonl"),
                }
            )
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )

    def fake_prediction(*args, **kwargs):
        observed.append(
            {
                "force_gate": os.environ.get("AS_M4_FORCE_AUDIO_GATE"),
                "enable_audio": os.environ.get("AS_M4_ENABLE_SCENE_AUDIO"),
            }
        )
        return "A", None, {"prompt": {}, "tokens": {}}

    monkeypatch.delenv("AS_M4_FORCE_AUDIO_GATE", raising=False)
    monkeypatch.delenv("AS_M4_ENABLE_SCENE_AUDIO", raising=False)
    monkeypatch.setattr(runner, "model_prediction", fake_prediction)

    runner.run_predictions(
        plan, backend="model", limit=1, feature_root=tmp_path, device="cpu", max_new_tokens=4
    )

    assert observed == [
        {"force_gate": "0", "enable_audio": None},
        {"force_gate": None, "enable_audio": "1"},
    ]
    assert "AS_M4_FORCE_AUDIO_GATE" not in os.environ
    assert "AS_M4_ENABLE_SCENE_AUDIO" not in os.environ
