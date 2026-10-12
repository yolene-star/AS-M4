#!/usr/bin/env python3
"""构建 LLP/AVVP 单数据集、四模态、四条件 CPU smoke manifest。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = REPO_ROOT / "intersuit/harness/configs/llp_modality_evaluator_v1.json"
DEFAULT_OUTPUT = REPO_ROOT / "intersuit/harness/artifacts/llp_modality_evaluator_v1"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def stable_key(seed: int, *parts: Any) -> str:
    text = "|".join([str(seed), *(str(part) for part in parts)])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def window_key(row: dict[str, Any]) -> tuple[str, float, float]:
    return row["sample_id"], float(row["window_start"]), float(row["window_end"])


def select_balanced_records(
    rows: list[dict[str, Any]], roles: list[str], per_role: int, seed: int
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for role in roles:
        candidates = sorted(
            (row for row in rows if row["modality_role"] == role),
            key=lambda row: stable_key(
                seed, role, row["sample_id"], row["window_start"], row.get("event_label")
            ),
        )
        by_media: dict[str, dict[str, Any]] = {}
        for row in candidates:
            by_media.setdefault(row["sample_id"], row)
        if len(by_media) < per_role:
            raise ValueError(f"{role} 独立物理媒体不足：{len(by_media)} < {per_role}")
        selected.extend(list(by_media.values())[:per_role])
    return selected


def choose_background_label(
    row: dict[str, Any], label_vocabulary: list[str], active_by_window: dict[tuple[str, float, float], set[str]], seed: int
) -> str:
    active = active_by_window[window_key(row)]
    candidates = sorted(
        (label for label in label_vocabulary if label not in active),
        key=lambda label: stable_key(seed, "background", *window_key(row), label),
    )
    if not candidates:
        raise ValueError("背景窗口找不到合法负标签")
    return candidates[0]


def choose_mismatch_donor(
    row: dict[str, Any], target_label: str, audio_by_window: dict[tuple[str, float, float], set[str]], seed: int
) -> tuple[str, float, float]:
    candidates = [
        key
        for key, labels in audio_by_window.items()
        if key[0] != row["sample_id"] and labels and target_label not in labels
    ]
    if not candidates:
        raise ValueError(f"找不到 mismatch donor：{target_label}")
    return min(candidates, key=lambda key: stable_key(seed, "mismatch", *window_key(row), target_label, *key))


def expand_record(
    row: dict[str, Any], target_label: str, donor: tuple[str, float, float], media: dict[str, dict[str, Any]],
    audio_root: Path, queries: dict[str, Any]
) -> list[dict[str, Any]]:
    role = row["modality_role"]
    target_by_query = {
        "audio_presence": role in {"audio_only", "audio_visual"},
        "visual_presence": role in {"visual_only", "audio_visual"},
    }
    prompts = {
        "audio_presence": f"Between {row['window_start']:.1f} and {row['window_end']:.1f} seconds, is the sound event '{target_label}' audible? Answer yes or no.",
        "visual_presence": f"Between {row['window_start']:.1f} and {row['window_end']:.1f} seconds, is the event '{target_label}' visibly present? Answer yes or no.",
    }
    source_id = row["sample_id"]
    donor_id, donor_start, donor_end = donor
    base_id = hashlib.sha256(
        f"{source_id}|{row['window_start']}|{row['window_end']}|{role}|{target_label}".encode("utf-8")
    ).hexdigest()[:20]
    expanded: list[dict[str, Any]] = []
    for query_type, query_config in queries.items():
        for condition in query_config["conditions"]:
            expanded.append(
                {
                    "eval_id": f"{base_id}:{query_type}:{condition}",
                    "base_id": base_id,
                    "dataset": "LLP/AVVP",
                    "split": "validation",
                    "sample_id": source_id,
                    "physical_media_id": media[source_id]["physical_media_id"],
                    "video_path": media[source_id]["media_path"],
                    "window_start": float(row["window_start"]),
                    "window_end": float(row["window_end"]),
                    "modality_role": role,
                    "event_label": target_label,
                    "query_type": query_type,
                    "condition": condition,
                    "prompt": prompts[query_type],
                    "target_answer": "yes" if target_by_query[query_type] else "no",
                    "correct_audio_path": str(audio_root / f"{media[source_id]['physical_media_id']}.wav"),
                    "mismatch_sample_id": donor_id,
                    "mismatch_audio_path": str(audio_root / f"{media[donor_id]['physical_media_id']}.wav"),
                    "mismatch_window_start": donor_start,
                    "mismatch_window_end": donor_end,
                }
            )
    return expanded


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    contract = read_json(args.contract)
    if contract["authorization"]["gpu_model_effect_eval"] is not False:
        raise ValueError("CPU manifest 阶段必须关闭 GPU effect eval")
    scope = contract["pilot_scope"]
    media_rows = read_jsonl(resolve_path(scope["media_manifest"]))
    media = {row["clip_id"]: row for row in media_rows if row["official_split"] == "validation"}
    if len(media) != int(scope["official_validation_media"]):
        raise ValueError(f"validation 媒体数量不匹配：{len(media)}")
    unified = [
        row
        for row in read_jsonl(resolve_path(scope["unified_event_manifest"]))
        if row.get("dataset") == "LLP"
        and row.get("split") == contract["source_manifest_split"]
        and row.get("sample_id") in media
    ]
    if {row["sample_id"] for row in unified} != set(media):
        raise ValueError("统一标注与 pilot50 媒体集合不一致")
    active_by_window: dict[tuple[str, float, float], set[str]] = defaultdict(set)
    audio_by_window: dict[tuple[str, float, float], set[str]] = defaultdict(set)
    for row in unified:
        label = row.get("event_label")
        if label:
            active_by_window[window_key(row)].add(label)
            if row["modality_role"] in {"audio_only", "audio_visual"}:
                audio_by_window[window_key(row)].add(label)
    labels = sorted({row["event_label"] for row in unified if row.get("event_label")})
    selected = select_balanced_records(
        unified, contract["roles"], int(scope["records_per_role"]), int(scope["selection_seed"])
    )
    audio_root = resolve_path(scope["audio_16k_root"])
    output_rows: list[dict[str, Any]] = []
    for row in selected:
        label = row.get("event_label") or choose_background_label(
            row, labels, active_by_window, int(scope["selection_seed"])
        )
        donor = choose_mismatch_donor(row, label, audio_by_window, int(scope["selection_seed"]))
        output_rows.extend(expand_record(row, label, donor, media, audio_root, contract["queries"]))
    expected = len(contract["roles"]) * int(scope["records_per_role"]) * sum(
        len(config["conditions"]) for config in contract["queries"].values()
    )
    if len(output_rows) != expected or len({row["eval_id"] for row in output_rows}) != expected:
        raise ValueError("展开后的 eval_id 数量不匹配")
    missing_media = sorted(
        {
            path
            for row in output_rows
            for path in (row["video_path"], row["correct_audio_path"], row["mismatch_audio_path"])
            if not resolve_path(path).is_file()
        }
    )
    if missing_media:
        raise ValueError(f"smoke 输入缺失：{missing_media[:3]}")
    args.output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_root / "manifest.jsonl"
    manifest_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in output_rows),
        encoding="utf-8",
    )
    summary = {
        "status": "PASS_LLP_MODALITY_CPU_MANIFEST_SMOKE_GPU_CLOSED",
        "base_record_count": len(selected),
        "expanded_row_count": len(output_rows),
        "role_counts": dict(sorted(Counter(row["modality_role"] for row in selected).items())),
        "query_condition_counts": dict(
            sorted(
                Counter(f"{row['query_type']}:{row['condition']}" for row in output_rows).items()
            )
        ),
        "physical_media_count": len({row["sample_id"] for row in selected}),
        "manual_labels_used": False,
        "gpu_used": False,
        "model_effect_eval_authorized": False,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }
    (args.output_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, sort_keys=True))
