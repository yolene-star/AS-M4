#!/usr/bin/env python
"""在单张显式 CUDA 卡上运行 Dynamic Window Selector 的 8 条 D0 smoke。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import traceback

import torch

from intersuit.harness.dynamic_selector_contract import module_state_sha256
from intersuit.model.streaming_av.dynamic_window_selector import (
    DynamicWindowSelector,
    LegacyMultiscaleDynamicWindowSelector,
)
from intersuit.model.streaming_av.fusion import GatedAVFusion


SMOKE_IDS = (
    "01_all_matches_fixed",
    "02_k1_earlier_tie",
    "03_k_gt_n_with_padding",
    "04_all_padding",
    "05_silence",
    "06_legacy_multiscale_regression",
    "07_strict_causal_future_perturbation",
    "08_gate0_rollback",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("intersuit/harness/artifacts/dynamic_selector_d0_contract/gpu_smokes"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().astimezone()
    if not torch.cuda.is_available():
        raise RuntimeError("D0 GPU smoke requires CUDA")
    if torch.cuda.device_count() != 1:
        raise RuntimeError(
            "D0 GPU smoke requires exactly one visible GPU; set CUDA_VISIBLE_DEVICES explicitly"
        )
    device = torch.device("cuda:0")
    device_name = torch.cuda.get_device_name(device)
    cases = {
        "01_all_matches_fixed": _smoke_all_matches_fixed,
        "02_k1_earlier_tie": _smoke_k1_earlier_tie,
        "03_k_gt_n_with_padding": _smoke_k_gt_n_with_padding,
        "04_all_padding": _smoke_all_padding,
        "05_silence": _smoke_silence,
        "06_legacy_multiscale_regression": _smoke_legacy_multiscale,
        "07_strict_causal_future_perturbation": _smoke_strict_causal,
        "08_gate0_rollback": _smoke_gate0,
    }
    matrix = {
        "stage": "D0-single-gpu-smokes",
        "smoke_ids": list(SMOKE_IDS),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "logical_device": "cuda:0",
        "visible_gpu_count": torch.cuda.device_count(),
        "device_name": device_name,
        "torch_version": torch.__version__,
        "started_at": started_at.isoformat(),
    }
    _write_json(output_dir / "smoke_matrix.json", matrix)
    (output_dir / "run.started_at").write_text(started_at.isoformat() + "\n", encoding="utf-8")

    results = []
    for smoke_id in SMOKE_IDS:
        case_dir = output_dir / smoke_id
        case_dir.mkdir(parents=True, exist_ok=True)
        case_started_at = datetime.now().astimezone()
        case_config = {
            "id": smoke_id,
            "device": "cuda:0",
            "physical_gpu": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "started_at": case_started_at.isoformat(),
        }
        _write_json(case_dir / "config.json", case_config)
        (case_dir / "run.started_at").write_text(
            case_started_at.isoformat() + "\n",
            encoding="utf-8",
        )
        torch.cuda.reset_peak_memory_stats(device)
        try:
            with torch.inference_mode():
                details = cases[smoke_id](device)
            torch.cuda.synchronize(device)
            result = {
                **case_config,
                "status": "pass",
                "peak_memory_bytes": torch.cuda.max_memory_allocated(device),
                "details": details,
            }
            (case_dir / "stdout.log").write_text(
                json.dumps(result, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (case_dir / "stderr.log").write_text("", encoding="utf-8")
        except Exception:
            result = {
                **case_config,
                "status": "fail",
                "peak_memory_bytes": torch.cuda.max_memory_allocated(device),
            }
            (case_dir / "stdout.log").write_text("", encoding="utf-8")
            (case_dir / "stderr.log").write_text(traceback.format_exc(), encoding="utf-8")
        _write_json(case_dir / "result.json", result)
        results.append(result)

    status = "pass" if len(results) == 8 and all(row["status"] == "pass" for row in results) else "fail"
    summary = {
        "status": status,
        "expected_smoke_count": 8,
        "completed_smoke_count": len(results),
        "passed_smoke_count": sum(row["status"] == "pass" for row in results),
        "matrix": matrix,
        "results": results,
        "finished_at": datetime.now().astimezone().isoformat(),
    }
    _write_json(output_dir / "smoke_summary.json", summary)
    (output_dir / "smoke_report.md").write_text(_build_report(summary), encoding="utf-8")
    print(json.dumps({"status": status, "passed": summary["passed_smoke_count"]}, ensure_ascii=False))
    return 0 if status == "pass" else 1


def _base_inputs(device: torch.device, steps: int = 6):
    features = torch.arange(steps * 4, device=device, dtype=torch.float32).reshape(1, steps, 4)
    timestamps = torch.tensor(
        [[float(index), float(index + 1)] for index in range(steps)],
        device=device,
    )
    windows = torch.zeros(1, steps, 64, device=device)
    mask = torch.ones(1, steps, dtype=torch.bool, device=device)
    return features, timestamps, windows, mask


def _smoke_all_matches_fixed(device):
    features, timestamps, windows, mask = _base_inputs(device)
    selector = DynamicWindowSelector(input_dim=4, top_k=6).to(device).eval()
    before = module_state_sha256(selector)
    output = selector(features, timestamps, mask, windows)
    _require(torch.equal(output.selected_features, features), "All features differ from Fixed")
    _require(torch.equal(output.selected_timestamps[0], timestamps), "All timestamps differ from Fixed")
    _require(module_state_sha256(selector) == before, "selector state SHA changed")
    return {"selected_count": int(output.selection_mask.sum().item()), "state_sha256": before}


def _smoke_k1_earlier_tie(device):
    features, timestamps, windows, mask = _base_inputs(device, steps=4)
    features.zero_()
    selector = DynamicWindowSelector(input_dim=4, top_k=1).to(device).eval()
    output = selector(features, timestamps, mask, windows)
    _require(output.selected_source_indices.item() == 0, "tie-break did not choose earliest window")
    return {"selected_source_index": 0}


def _smoke_k_gt_n_with_padding(device):
    features, timestamps, windows, mask = _base_inputs(device)
    mask[:, 4:] = False
    output = DynamicWindowSelector(input_dim=4, top_k=9).to(device).eval()(
        features,
        timestamps,
        mask,
        windows,
    )
    indices = output.selected_source_indices[output.selection_mask]
    _require(indices.tolist() == [0, 1, 2, 3], "K>N or padding contract failed")
    return {"selected_source_indices": indices.tolist()}


def _smoke_all_padding(device):
    features, timestamps, windows, mask = _base_inputs(device)
    mask.zero_()
    output = DynamicWindowSelector(input_dim=4, top_k=3).to(device).eval()(
        features,
        timestamps,
        mask,
        windows,
    )
    _require(not output.selection_mask.any().item(), "all-padding input selected a window")
    _require(torch.isfinite(output.dynamic_thresholds).all().item(), "non-finite threshold")
    return {"selected_count": 0}


def _smoke_silence(device):
    features, timestamps, windows, mask = _base_inputs(device, steps=4)
    features.zero_()
    output = DynamicWindowSelector(input_dim=4, top_k=2).to(device).eval()(
        features,
        timestamps,
        mask,
        windows,
    )
    _require(torch.count_nonzero(output.eventness_cached_scores).item() == 0, "silence scored as event")
    _require(torch.isfinite(output.selected_features).all().item(), "silence output is non-finite")
    return {"selected_source_indices": output.selected_source_indices[0, :2].tolist()}


def _smoke_legacy_multiscale(device):
    features, timestamps, windows, mask = _base_inputs(device)
    features.zero_()
    features[0, 2, 0] = 1.0
    windows[0, 2] = 0.2
    selector = LegacyMultiscaleDynamicWindowSelector(
        input_dim=4,
        top_k=3,
        nms_iou=1.0,
    ).to(device).eval()
    output = selector(features, timestamps, mask, windows)
    _require(output.selection_mask.any().item(), "legacy regression selected no burst")
    _require(torch.isfinite(output.selected_features).all().item(), "legacy output is non-finite")
    return {"selected_count": int(output.selection_mask.sum().item())}


def _smoke_strict_causal(device):
    selector = DynamicWindowSelector(input_dim=4, top_k=1).to(device).eval()
    timestamps = torch.tensor(
        [[float(index), float(index + 1)] for index in range(6)],
        device=device,
    )
    prefix = torch.arange(12, device=device, dtype=torch.float32).reshape(1, 3, 4)
    suffix_a = torch.zeros(1, 3, 4, device=device)
    suffix_b = torch.full((1, 3, 4), 999.0, device=device)
    windows = torch.ones(1, 6, 32, device=device) * 0.1

    def replay(features):
        state = None
        rows = []
        for index in range(6):
            output = selector(
                features[:, index : index + 1],
                timestamps[index : index + 1],
                torch.ones(1, 1, dtype=torch.bool, device=device),
                windows[:, index : index + 1],
                state=state,
                audio_source_indices=torch.tensor([[index]], device=device),
            )
            state = output.state
            rows.append(
                (
                    output.selected_features.clone(),
                    output.selection_scores.clone(),
                    output.selected_source_indices.clone(),
                )
            )
        return rows

    replay_a = replay(torch.cat([prefix, suffix_a], dim=1))
    replay_b = replay(torch.cat([prefix, suffix_b], dim=1))
    for index in range(3):
        _require(
            all(torch.equal(a, b) for a, b in zip(replay_a[index], replay_b[index])),
            f"future perturbation changed output at prefix index {index}",
        )
    return {"unchanged_prefix_windows": 3}


def _smoke_gate0(device):
    fusion = GatedAVFusion(hidden_size=4, fusion_init="identity").to(device).eval()
    video = torch.randn(1, 3, 2, 4, device=device)
    audio = torch.randn(1, 3, 4, device=device)
    before = module_state_sha256(fusion)
    output = fusion(video, audio, torch.zeros(1, 3, device=device))
    _require(torch.equal(output, video), "gate=0 did not exactly recover video")
    _require(module_state_sha256(fusion) == before, "fusion parameter SHA changed")
    return {"exact_video_recovery": True, "state_sha256": before}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_report(summary: dict) -> str:
    rows = "\n".join(
        f"- [{'x' if row['status'] == 'pass' else ' '}] `{row['id']}`"
        for row in summary["results"]
    )
    return f"""# Dynamic Window Selector D0 单卡 Smoke 报告

## 结论

状态：**{summary["status"]}**；通过 {summary["passed_smoke_count"]}/8。

## 单卡环境

- `CUDA_VISIBLE_DEVICES={summary["matrix"]["cuda_visible_devices"]}`
- 逻辑设备：`cuda:0`
- GPU：`{summary["matrix"]["device_name"]}`
- PyTorch：`{summary["matrix"]["torch_version"]}`

## Smoke 矩阵

{rows}
"""


if __name__ == "__main__":
    raise SystemExit(main())
