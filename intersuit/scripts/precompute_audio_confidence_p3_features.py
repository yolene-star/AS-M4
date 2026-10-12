#!/usr/bin/env python
"""从 SHA 锁定的 Valor32K P2 条件生成正式 P3 冻结 M4 特征包。

默认合同仍关闭 feature_precompute，因此在计划评审把对应授权改为 true 之前，
本脚本会 fail closed。``--audit-only`` 不加载模型或 GPU，可独立复核已有资产。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

import torch
import torch.nn.functional as F

from intersuit.data_processing.valor32k_confidence import VirtualAudioMaterializer, sha256_file
from intersuit.harness.runners.run_predictions_from_plan import load_model_once, _load_video_tensor
from intersuit.train.audio_confidence_precompute import (
    FrozenEncoderBundle,
    audit_feature_manifest,
    precompute_feature_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/audio_confidence_gate_p3_contract_v1.json"
DEFAULT_P2_MANIFEST = (
    INTERSUIT_ROOT
    / "datasets/VALOR32K_AVQA_V2_AUDIT/p2/as_m4_cgf_valor32k_p2_conditions_v1.json"
)
DEFAULT_MEDIA_ROOT = INTERSUIT_ROOT / "datasets/VALOR32K_AVQA_V2_AUDIT/pilot/media"
DEFAULT_MODEL = INTERSUIT_ROOT / "checkpoints/M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1"
DEFAULT_OUTPUT = INTERSUIT_ROOT / "datasets/VALOR32K_AVQA_V2_AUDIT/p3/frozen_features_v1"


def _composite_sha256(values: Mapping[str, str]) -> str:
    payload = json.dumps(dict(sorted(values.items())), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class FrozenM4FeatureExtractor:
    """复用冻结 M4 的 BEATs/projector、视觉塔和 token embedding。"""

    def __init__(self, model_path: Path, media_root: Path, device: str) -> None:
        self.media_root = Path(media_root)
        self.device = device
        self.tokenizer, self.model, self.image_processor, _ = load_model_once(
            str(model_path), device
        )
        self.body = self.model.get_model()
        self.hidden_size = int(self.model.config.hidden_size)
        self._video_cache: dict[str, torch.Tensor] = {}
        self._question_cache: dict[str, torch.Tensor] = {}
        if any(parameter.requires_grad for parameter in self.model.parameters()):
            raise RuntimeError("冻结 M4 特征提取器仍存在 requires_grad=true 参数")
        meta_parameters = [
            name for name, parameter in self.model.named_parameters() if parameter.is_meta
        ]
        if meta_parameters:
            raise RuntimeError(f"冻结 M4 仍存在 meta 参数：{meta_parameters[:8]}")

    @torch.inference_mode()
    def encode_audio(self, windows: torch.Tensor, timestamps: torch.Tensor) -> torch.Tensor:
        encoder = self.body.get_scene_audio_encoder()
        output = encoder(
            windows.unsqueeze(0).to(self.device),
            sample_mask=torch.ones(1, windows.shape[0], dtype=torch.bool, device=self.device),
            timestamps=timestamps.unsqueeze(0).to(self.device),
        )
        return output.features[0].detach().float().cpu()

    @torch.inference_mode()
    def encode_video(self, row: Mapping[str, Any], timestamps: torch.Tensor) -> torch.Tensor:
        relative = str(row["video_relative_media_path"])
        cached = self._video_cache.get(relative)
        if cached is None:
            media_path = self.media_root / relative
            tensor = _load_video_tensor(
                str(media_path), self.image_processor, self.model, self.device,
                max_frames=int(row["target_stream_window_count"]),
            )
            features = self.model.encode_multimodals(
                tensor, [0], split_sizes=[int(tensor.shape[0])]
            )[0]
            # Gate 只消费逐窗口 summary；视觉 token 在这里按帧平均，模型权重保持冻结。
            cached = features.float().mean(dim=1).detach().cpu()
            self._video_cache[relative] = cached
        aligned = F.interpolate(
            cached.transpose(0, 1).unsqueeze(0),
            size=int(timestamps.shape[0]), mode="linear", align_corners=False,
        ).squeeze(0).transpose(0, 1)
        return aligned.contiguous()

    @torch.inference_mode()
    def encode_question(self, text: str) -> torch.Tensor:
        cached = self._question_cache.get(text)
        if cached is None:
            tokenized = self.tokenizer(text, return_tensors="pt", add_special_tokens=True)
            input_ids = tokenized.input_ids.to(self.device)
            attention = tokenized.attention_mask.to(self.device, dtype=torch.bool)
            embeddings = self.body.embed_tokens(input_ids)
            weights = attention.to(embeddings.dtype).unsqueeze(-1)
            cached = (
                (embeddings * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1.0)
            )[0].detach().float().cpu()
            self._question_cache[text] = cached
        return cached.clone()


def _source_fingerprints(model_path: Path, extractor: FrozenM4FeatureExtractor) -> dict[str, str]:
    from intersuit.harness.dynamic_selector_contract import module_state_sha256

    config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
    beats = Path(config["scene_audio_beats_checkpoint"])
    vision_tower = extractor.body.get_vision_tower()
    if isinstance(vision_tower, list):
        vision_tower = vision_tower[0]
    return {
        "audio": _composite_sha256(
            {
                "beats": sha256_file(beats),
                "scene_audio_encoder_state": module_state_sha256(
                    extractor.body.get_scene_audio_encoder()
                ),
            }
        ),
        "video": _composite_sha256(
            {
                "vision_tower_state": module_state_sha256(vision_tower),
                "mm_projector_state": module_state_sha256(extractor.body.mm_projector),
            }
        ),
        "question": module_state_sha256(extractor.body.embed_tokens),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--p2-manifest", type=Path, default=DEFAULT_P2_MANIFEST)
    parser.add_argument("--media-root", type=Path, default=DEFAULT_MEDIA_ROOT)
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--feature-manifest", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.audit_only:
        path = args.feature_manifest or (args.output_root / "feature_manifest.json")
        print(json.dumps(audit_feature_manifest(path), ensure_ascii=False, indent=2))
        return
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract["authorization"].get("feature_precompute") is not True:
        raise PermissionError("P3 contract 尚未授权 feature_precompute；停止，不加载模型/GPU")
    scope = contract.get("feature_precompute_scope") or {}
    max_records = int(scope.get("max_records", 0))
    selected_record_ids = [str(value) for value in scope.get("selected_record_ids", [])]
    if max_records <= 0 or args.limit is None or not 0 < args.limit <= max_records:
        raise PermissionError("P3 feature precompute 必须提供不超过合同 max_records 的正 limit")
    allowed_output = scope.get("allowed_output_root")
    if allowed_output:
        allowed_output_path = Path(allowed_output)
        if not allowed_output_path.is_absolute():
            allowed_output_path = PROJECT_ROOT / allowed_output_path
        if args.output_root.resolve() != allowed_output_path.resolve():
            raise PermissionError("P3 feature precompute 输出目录不在合同白名单")
    if scope.get("overwrite_existing") is False and args.output_root.exists() and any(args.output_root.iterdir()):
        raise FileExistsError("P3 feature precompute 合同禁止覆盖已有输出")
    if args.device.startswith("cuda") and os.environ.get("CUDA_VISIBLE_DEVICES") != "2":
        raise RuntimeError("正式 P3 预计算必须显式设置 CUDA_VISIBLE_DEVICES=2")
    expected_manifest_sha = contract["dataset"]["condition_manifest_sha256"]
    actual_manifest_sha = sha256_file(args.p2_manifest)
    if actual_manifest_sha != expected_manifest_sha:
        raise ValueError(
            f"P2 manifest SHA 漂移：expected={expected_manifest_sha}, actual={actual_manifest_sha}"
        )
    rows = json.loads(args.p2_manifest.read_text(encoding="utf-8"))
    rows = [row for row in rows if row.get("first_stage_training") is True]
    if selected_record_ids:
        by_id = {str(row["record_id"]): row for row in rows}
        missing = sorted(set(selected_record_ids) - set(by_id))
        if missing:
            raise ValueError(f"合同指定的 P2 record_id 缺失：{missing}")
        rows = [by_id[record_id] for record_id in selected_record_ids]
        if args.limit != len(rows):
            raise PermissionError("锁定 record_id smoke 要求 limit 与样本数精确一致")
    materializer = VirtualAudioMaterializer(args.media_root, verify_source_sha256=True)
    extractor = FrozenM4FeatureExtractor(args.model_path, args.media_root, args.device)
    bundle = FrozenEncoderBundle(
        audio_encoder=extractor.encode_audio,
        video_encoder=extractor.encode_video,
        question_encoder=extractor.encode_question,
        hidden_size=extractor.hidden_size,
        audio_sample_rate=16000,
        source_fingerprints=_source_fingerprints(args.model_path, extractor),
    )
    manifest_path = precompute_feature_manifest(
        rows, materializer.materialize, bundle, args.output_root,
        p2_manifest_sha256=actual_manifest_sha, limit=args.limit,
    )
    print(json.dumps(audit_feature_manifest(manifest_path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
