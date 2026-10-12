#!/usr/bin/env python
"""诊断音频语义重构 R2 的旧答案目标与合法文本目标。

本脚本保持 BEATs、scene-audio projector 和 Qwen input embedding 全部冻结。
它只在 R0 的 expanded validation 上比较固定文本 prototype，不训练参数，不
读取 test_locked，也不把模板 QA 当作新的 ground truth。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import torch
from safetensors import safe_open
from transformers import AutoTokenizer


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
R1_SCRIPT = INTERSUIT_ROOT / "scripts/evaluate_audio_semantic_reconstruction_r1.py"
DEFAULT_FEATURE_MANIFEST = (
    INTERSUIT_ROOT
    / "harness/artifacts/audio_semantic_reconstruction_r1_expanded/full/features"
    / "ave_hf_window_feature_manifest.jsonl"
)
DEFAULT_CHECKPOINT = (
    INTERSUIT_ROOT / "checkpoints/AS-M4-BEATs-semantic-contrast-canary150-v1"
)
DEFAULT_OUTPUT = (
    INTERSUIT_ROOT / "harness/artifacts/audio_semantic_reconstruction_r2_targets"
)
EMBED_KEY = "model.embed_tokens.weight"
FIXED_SEEDS = (17, 23, 47)
BOOTSTRAP_REPLICATES = 10_000
QUESTION_TEMPLATE = "Which sound event is audible in this video?"
DESCRIPTION_TEMPLATE = "A recording of the sound event: {label}."


def import_r1_module():
    spec = importlib.util.spec_from_file_location("audio_semantic_r1_for_r2", R1_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


r1 = import_r1_module()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def old_answer_token_ids(tokenizer, label: str) -> list[int]:
    """精确复现训练预处理对单条 assistant answer 的 unmasked token 序列。"""

    token_ids = tokenizer.apply_chat_template(
        [{"role": "assistant", "content": label}],
        tokenize=True,
    )
    if not token_ids:
        raise ValueError(f"旧答案目标 token 为空：{label}")
    return [int(value) for value in token_ids[1:]]


def plain_token_ids(tokenizer, text: str) -> list[int]:
    values = tokenizer(text, add_special_tokens=False).input_ids
    if not values:
        raise ValueError(f"文本目标 token 为空：{text!r}")
    return [int(value) for value in values]


def target_text_and_tokens(tokenizer, label: str) -> dict[str, dict[str, Any]]:
    canonical_ids = plain_token_ids(tokenizer, label)
    return {
        "old_answer_token_mean": {
            "text": label,
            "token_ids": old_answer_token_ids(tokenizer, label),
            "legal_target": False,
            "source": "training assistant target including chat envelope and end token",
        },
        "canonical_event_label": {
            "text": label,
            "token_ids": canonical_ids,
            "legal_target": True,
            "source": "R0 canonical label table",
        },
        "question_plus_canonical_answer": {
            "text": f"Question: {QUESTION_TEMPLATE} Answer: {label}",
            "token_ids": plain_token_ids(
                tokenizer,
                f"Question: {QUESTION_TEMPLATE} Answer: {label}",
            ),
            "legal_target": True,
            "source": "fixed train-side audio question template plus canonical label",
        },
        "label_description_prototype": {
            "text": DESCRIPTION_TEMPLATE.format(label=label),
            "token_ids": plain_token_ids(
                tokenizer, DESCRIPTION_TEMPLATE.format(label=label)
            ),
            "legal_target": True,
            "source": "fixed description template applied to train canonical label table",
        },
    }


def load_selected_embeddings(
    shard_path: Path,
    token_ids: list[int],
) -> tuple[dict[int, torch.Tensor], str]:
    unique = sorted(set(token_ids))
    values: dict[int, torch.Tensor] = {}
    digest = hashlib.sha256()
    with safe_open(shard_path, framework="pt", device="cpu") as handle:
        if EMBED_KEY not in handle.keys():
            raise ValueError(f"Qwen embedding key 缺失：{EMBED_KEY}")
        tensor_slice = handle.get_slice(EMBED_KEY)
        vocab_size, hidden_size = tensor_slice.get_shape()
        if hidden_size != 3584:
            raise ValueError(f"Qwen hidden size 异常：{hidden_size}")
        for token_id in unique:
            if token_id < 0 or token_id >= vocab_size:
                raise ValueError(f"token id 越界：{token_id}")
            row = tensor_slice[token_id : token_id + 1].float().squeeze(0)
            values[token_id] = row
            digest.update(str(token_id).encode("ascii"))
            digest.update(row.contiguous().numpy().tobytes())
    return values, digest.hexdigest()


def mean_token_embedding(
    token_ids: list[int], embedding_rows: dict[int, torch.Tensor]
) -> torch.Tensor:
    return torch.stack([embedding_rows[token_id] for token_id in token_ids]).mean(
        dim=0
    )


def evaluate_fixed_targets(
    query_rows: list[dict[str, Any]],
    labels: list[str],
    target_vectors: dict[str, torch.Tensor],
    bootstrap_seed: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    prototypes = r1.normalize_rows(
        torch.stack([target_vectors[label] for label in labels])
    )
    label_to_index = {label: index for index, label in enumerate(labels)}
    audio = r1.normalize_rows(
        torch.stack([row["projected_audio"] for row in query_rows])
    )
    similarities = audio @ prototypes.T
    predictions = similarities.argmax(dim=1)
    per_sample = []
    per_class_correct: Counter[str] = Counter()
    per_class_total: Counter[str] = Counter()
    margins: list[float] = []
    for row_index, row in enumerate(query_rows):
        true_index = label_to_index[row["canonical_label"]]
        positive = float(similarities[row_index, true_index].item())
        wrong = similarities[row_index].clone()
        wrong[true_index] = -torch.inf
        hard_index = int(wrong.argmax().item())
        hard = float(wrong[hard_index].item())
        margin = positive - hard
        predicted = labels[int(predictions[row_index].item())]
        correct = predicted == row["canonical_label"]
        margins.append(margin)
        per_class_correct[row["canonical_label"]] += int(correct)
        per_class_total[row["canonical_label"]] += 1
        per_sample.append(
            {
                "sample_id": row["sample_id"],
                "canonical_label": row["canonical_label"],
                "predicted_label": predicted,
                "hard_negative_label": labels[hard_index],
                "positive_similarity": positive,
                "hard_negative_similarity": hard,
                "margin": margin,
                "correct": correct,
            }
        )
    per_class_recall = {
        label: per_class_correct[label] / per_class_total[label]
        for label in labels
    }
    macro = mean(per_class_recall.values())
    rank_first = sum(value > 0 for value in margins) / len(margins)
    metrics = {
        "query_count": len(query_rows),
        "label_count": len(labels),
        "macro_class_recall_at_1": macro,
        "balanced_accuracy": macro,
        "accuracy": sum(row["correct"] for row in per_sample) / len(per_sample),
        "rank_first": rank_first,
        "margin_mean": mean(margins),
        "margin_bootstrap_95_ci": r1.bootstrap_mean_ci(
            margins, bootstrap_seed, BOOTSTRAP_REPLICATES
        ),
        "per_class_recall_at_1": per_class_recall,
        "per_class_query_count": dict(sorted(per_class_total.items())),
        "random_class_baseline": 1.0 / len(labels),
    }
    return metrics, per_sample


def aggregate(seed_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "macro_class_recall_at_1",
        "balanced_accuracy",
        "accuracy",
        "rank_first",
        "margin_mean",
    )
    output: dict[str, Any] = {}
    for key in keys:
        values = [float(item[key]) for item in seed_metrics]
        output[key] = {
            "mean": mean(values),
            "std": pstdev(values),
            "worst_seed": min(values),
            "per_seed": values,
        }
    output["margin_ci_lower_worst_seed"] = min(
        item["margin_bootstrap_95_ci"][0] for item in seed_metrics
    )
    output["margin_ci_upper_worst_seed"] = min(
        item["margin_bootstrap_95_ci"][1] for item in seed_metrics
    )
    output["per_class_recall_at_1"] = seed_metrics[0]["per_class_recall_at_1"]
    output["per_class_query_count"] = seed_metrics[0]["per_class_query_count"]
    output["random_class_baseline"] = seed_metrics[0]["random_class_baseline"]
    output["query_count"] = seed_metrics[0]["query_count"]
    output["label_count"] = seed_metrics[0]["label_count"]
    return output


def load_projected_validation(
    feature_rows: list[dict[str, Any]],
    checkpoint: Path,
) -> tuple[list[dict[str, Any]], str]:
    weight, bias, projector_sha = r1.load_current_projector(
        checkpoint / "model-00003-of-00004.safetensors"
    )
    output = []
    for row in feature_rows:
        if row["split"] != "validation":
            continue
        payload = torch.load(
            row["audio_feature_path"], map_location="cpu", weights_only=True
        )
        embeddings = payload["audio_embedding"].float()
        valid = torch.as_tensor(
            payload.get("valid_sample_count"), dtype=torch.long
        )
        raw = r1.sample_weighted_mean(embeddings, valid)
        projected = torch.nn.functional.linear(raw, weight, bias)
        output.append({**row, "projected_audio": projected})
    if len(output) != 196:
        raise ValueError(f"expanded validation 必须为 196 条，实际 {len(output)}")
    return output, projector_sha


def build_review(metrics: dict[str, Any], decision: dict[str, Any]) -> str:
    lines = [
        "# 音频语义重构 R2 文本目标诊断",
        "",
        f"- 结论：`{decision['status']}`",
        "- 音频：冻结 BEATs + 当前冻结 scene-audio projector。",
        "- 文本：冻结 Qwen input embedding，仅按预注册 token 序列做均值。",
        "- `answer_choice_text`：本 AVE clip-level 集合没有选择题，明确记为 `NOT_APPLICABLE`。",
        "- AVE 官方 `test_locked` 未访问；本阶段未训练任何参数。",
        "",
        "## Validation 结果",
        "",
        "| 目标 | rank-first | margin CI 下界 | 相对旧目标 | 合法目标 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    old_rank = metrics["targets"]["old_answer_token_mean"]["rank_first"]["mean"]
    for name, values in metrics["targets"].items():
        lines.append(
            f"| `{name}` | {values['rank_first']['mean']:.6f} | "
            f"{values['margin_ci_lower_worst_seed']:.6f} | "
            f"{values['rank_first']['mean'] - old_rank:+.6f} | "
            f"{str(values['legal_target']).lower()} |"
        )
    lines.extend(
        [
            "",
            "## 下一步",
            "",
            decision["next_step_zh"],
            "",
        ]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    feature_manifest = Path(args.feature_manifest).resolve()
    checkpoint = Path(args.checkpoint).resolve()
    output_root = Path(args.output_root).resolve()
    feature_rows = read_jsonl(feature_manifest)
    if len(feature_rows) != 804:
        raise ValueError("R2 必须使用冻结的 804 条 train+validation feature manifest")
    if {row["split"] for row in feature_rows} != {"train", "validation"}:
        raise ValueError("R2 输入不得包含 test_locked")
    labels = sorted({row["canonical_label"] for row in feature_rows})
    if len(labels) != 28:
        raise ValueError(f"R2 必须包含 28 个 canonical labels，实际 {len(labels)}")

    tokenizer = AutoTokenizer.from_pretrained(
        checkpoint, local_files_only=True, trust_remote_code=False
    )
    target_specs = {
        label: target_text_and_tokens(tokenizer, label) for label in labels
    }
    all_token_ids = [
        token_id
        for label in labels
        for spec in target_specs[label].values()
        for token_id in spec["token_ids"]
    ]
    index = json.loads(
        (checkpoint / "model.safetensors.index.json").read_text(encoding="utf-8")
    )
    embed_shard = checkpoint / index["weight_map"][EMBED_KEY]
    embedding_rows, selected_embedding_sha = load_selected_embeddings(
        embed_shard, all_token_ids
    )
    query_rows, projector_sha = load_projected_validation(
        feature_rows, checkpoint
    )

    target_names = list(next(iter(target_specs.values())).keys())
    target_metrics: dict[str, Any] = {}
    per_sample_output: list[dict[str, Any]] = []
    target_lock: dict[str, Any] = {}
    for target_name in target_names:
        vectors = {
            label: mean_token_embedding(
                target_specs[label][target_name]["token_ids"],
                embedding_rows,
            )
            for label in labels
        }
        seed_metrics = []
        first_per_sample = None
        for seed in FIXED_SEEDS:
            result, per_sample = evaluate_fixed_targets(
                query_rows, labels, vectors, bootstrap_seed=seed
            )
            seed_metrics.append(result)
            if first_per_sample is None:
                first_per_sample = per_sample
        values = aggregate(seed_metrics)
        values["legal_target"] = bool(
            target_specs[labels[0]][target_name]["legal_target"]
        )
        target_metrics[target_name] = values
        assert first_per_sample is not None
        per_sample_output.extend(
            {"target": target_name, **row} for row in first_per_sample
        )
        target_lock[target_name] = {
            label: {
                "text": target_specs[label][target_name]["text"],
                "token_ids": target_specs[label][target_name]["token_ids"],
                "source": target_specs[label][target_name]["source"],
            }
            for label in labels
        }

    old_rank = target_metrics["old_answer_token_mean"]["rank_first"]["mean"]
    legal_gates = {}
    for name, values in target_metrics.items():
        if not values["legal_target"]:
            continue
        legal_gates[name] = {
            "rank_first_at_least_0_65": values["rank_first"]["mean"] >= 0.65,
            "margin_ci_lower_above_zero": values["margin_ci_lower_worst_seed"] > 0,
            "rank_first_improvement_over_old_at_least_10pp": (
                values["rank_first"]["mean"] >= old_rank + 0.10
            ),
        }
    passing_legal_targets = [
        name for name, gates in legal_gates.items() if all(gates.values())
    ]
    status = "PASS" if passing_legal_targets else "FAIL"
    metrics = {
        "status": status,
        "sample_count": len(feature_rows),
        "validation_count": len(query_rows),
        "label_count": len(labels),
        "targets": target_metrics,
        "answer_choice_text": {
            "status": "NOT_APPLICABLE",
            "reason_zh": "AVE clip-level 事件集合不是多选题，不存在 answer_letter/answer_choice_text。",
        },
        "legal_target_gates": legal_gates,
        "passing_legal_targets": passing_legal_targets,
        "formal_test_metric_accessed": False,
        "training_performed": False,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    metrics_path = output_root / "metrics.json"
    per_sample_path = output_root / "per_sample.jsonl"
    target_lock_path = output_root / "target_lock.json"
    write_json(metrics_path, metrics)
    write_jsonl(per_sample_path, per_sample_output)
    write_json(target_lock_path, target_lock)

    config = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stage": "R2",
        "fixed_seeds": list(FIXED_SEEDS),
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "audio_representation": "valid-sample-weighted raw BEATs clip mean through frozen current scene-audio projector",
        "question_template": QUESTION_TEMPLATE,
        "description_template": DESCRIPTION_TEMPLATE,
        "old_target_reconstruction": "tokenizer.apply_chat_template(single assistant answer)[1:]",
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "git_head": git_head(),
        "implementation_sha256": sha256(Path(__file__).resolve()),
        "formal_test_metric_accessed": False,
        "training_performed": False,
    }
    config_path = output_root / "config.json"
    write_json(config_path, config)
    decision = {
        "stage": "R2",
        "status": status,
        "passing_legal_targets": passing_legal_targets,
        "projector_training_authorized": status == "PASS",
        "fusion_changes_authorized": False,
        "selector_training_authorized": False,
        "formal_test_authorized": False,
        "next_step": (
            "independent_review_then_r3_projector_only"
            if status == "PASS"
            else "stop_and_redesign_audio_semantic_target"
        ),
        "next_step_zh": (
            "至少一个合法目标通过；完成独立 review 后才可进入 R3 projector-only 小训练。"
            if status == "PASS"
            else "没有合法文本目标通过 R2 合取门禁；按计划停止，不训练 projector、fusion 或 selector。"
        ),
    }
    decision_path = output_root / "decision.json"
    write_json(decision_path, decision)
    review_path = output_root / "review.md"
    review_path.write_text(build_review(metrics, decision), encoding="utf-8")
    manifest_lock = {
        "inputs": {
            "feature_manifest": {
                "path": str(feature_manifest),
                "sha256": sha256(feature_manifest),
            },
            "checkpoint_index": {
                "path": str(checkpoint / "model.safetensors.index.json"),
                "sha256": sha256(checkpoint / "model.safetensors.index.json"),
            },
            "tokenizer_json": {
                "path": str(checkpoint / "tokenizer.json"),
                "sha256": sha256(checkpoint / "tokenizer.json"),
            },
            "selected_qwen_embedding_rows_sha256": selected_embedding_sha,
            "scene_audio_projector_tensor_sha256": projector_sha,
        },
        "outputs": {
            "config.json": sha256(config_path),
            "metrics.json": sha256(metrics_path),
            "per_sample.jsonl": sha256(per_sample_path),
            "target_lock.json": sha256(target_lock_path),
            "review.md": sha256(review_path),
            "decision.json": sha256(decision_path),
        },
    }
    write_json(output_root / "manifest_lock.json", manifest_lock)
    return {
        "status": status,
        "output_root": str(output_root),
        "passing_legal_targets": passing_legal_targets,
        "targets": {
            name: {
                "rank_first": values["rank_first"]["mean"],
                "margin_ci_lower": values["margin_ci_lower_worst_seed"],
                "legal_target": values["legal_target"],
            }
            for name, values in target_metrics.items()
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-manifest", default=str(DEFAULT_FEATURE_MANIFEST))
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    return parser


def main() -> None:
    result = run(build_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
