#!/usr/bin/env python
"""离线扫描 OmniMMI PT logits 的 temperature、beta 与候选 token。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import torch


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def prediction_key(row: dict[str, Any]) -> tuple[str, str]:
    return row["video"], json.dumps(
        row["conversation"], ensure_ascii=False, sort_keys=True
    )


def score_one(
    logits: torch.Tensor, temperature: float, beta: float, token_id: int
) -> dict[str, float | bool]:
    probabilities = torch.softmax(logits.float() / temperature, dim=-1)
    entropy = -(probabilities * torch.log(probabilities + 1e-5)).sum()
    reciprocal_perplexity = torch.exp(-entropy)
    probability = probabilities[token_id]
    threshold = beta * reciprocal_perplexity
    return {
        "probability": float(probability),
        "entropy": float(entropy),
        "reciprocal_perplexity": float(reciprocal_perplexity),
        "threshold": float(threshold),
        "silent": bool(probability > threshold),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    scores = load_jsonl(Path(args.scores_jsonl))
    predictions = load_jsonl(Path(args.baseline_predictions))
    prediction_by_video = {row["video"]: row for row in predictions}
    if len(prediction_by_video) != len(predictions):
        raise ValueError("baseline predictions 的 video 必须唯一")

    experiments = []
    for temperature in args.temperatures:
        for beta in args.betas:
            for token_id in args.token_ids:
                silent_count = 0
                baseline_agreement = 0
                changed_videos = []
                for row in scores:
                    payload = torch.load(
                        row["logits_path"], map_location="cpu", weights_only=True
                    )
                    result = score_one(
                        payload["final_logits"], temperature, beta, token_id
                    )
                    silent = bool(result["silent"])
                    silent_count += int(silent)
                    baseline_silent = prediction_by_video[row["video"]]["pred"] == ""
                    baseline_agreement += int(silent == baseline_silent)
                    if silent != baseline_silent:
                        changed_videos.append(row["video"])
                count = len(scores)
                experiments.append(
                    {
                        "temperature": temperature,
                        "beta": beta,
                        "token_id": token_id,
                        "silent_count": silent_count,
                        "count": count,
                        "pt_accuracy": silent_count / count if count else None,
                        "baseline_agreement_count": baseline_agreement,
                        "baseline_agreement": baseline_agreement / count if count else None,
                        "changed_videos": changed_videos,
                    }
                )

    summary = {
        "scores_jsonl": str(Path(args.scores_jsonl).resolve()),
        "baseline_predictions": str(Path(args.baseline_predictions).resolve()),
        "sample_count": len(scores),
        "temperatures": args.temperatures,
        "betas": args.betas,
        "token_ids": args.token_ids,
        "experiments": experiments,
    }
    output = Path(args.output_json).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores-jsonl", required=True)
    parser.add_argument("--baseline-predictions", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--temperatures", type=float, nargs="+", default=[0.05])
    parser.add_argument("--betas", type=float, nargs="+", default=[1e-6, 0.2])
    parser.add_argument("--token-ids", type=int, nargs="+", default=[151643, 151644, 151645])
    return parser


def main() -> None:
    summary = run(build_parser().parse_args())
    compact = [
        {
            key: experiment[key]
            for key in (
                "temperature",
                "beta",
                "token_id",
                "pt_accuracy",
                "baseline_agreement",
            )
        }
        for experiment in summary["experiments"]
    ]
    print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
