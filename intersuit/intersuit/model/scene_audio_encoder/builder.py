"""Factory for AS-M4 scene-audio encoders."""

from __future__ import annotations

from .scene_audio_encoder import (
    DummySceneAudioEncoder,
    FrozenBEATsSceneAudioEncoder,
    FrozenBEATsV4SceneAudioEncoder,
    FrozenTorchaudioSceneAudioEncoder,
    PrecomputedSceneAudioEncoder,
)


def build_scene_audio_encoder(config):
    """Build a scene-audio encoder from config.

    Supported first-pass backends intentionally avoid external model downloads:

    - ``dummy``: deterministic waveform statistics, useful for harnesses.
    - ``precomputed``: accepts already extracted features.
    - ``frozen_torchaudio``: frozen torchaudio SSL bundle such as WAV2VEC2_BASE.
    - ``beats``: frozen local BEATs plus a trainable audio projector.
    - ``beats_v4``: frozen local BEATs plus locked v4 semantic projector and M4 adapter.
    """

    encoder_type = getattr(config, "scene_audio_encoder_type", "dummy")
    hidden_size = int(getattr(config, "scene_audio_hidden_size", 768))
    if encoder_type is None:
        encoder_type = "dummy"
    encoder_type = encoder_type.lower()

    if encoder_type == "dummy":
        return DummySceneAudioEncoder(hidden_size=hidden_size)
    if encoder_type == "precomputed":
        input_dim = getattr(config, "scene_audio_precomputed_dim", None)
        return PrecomputedSceneAudioEncoder(
            hidden_size=hidden_size,
            input_dim=input_dim,
            shared_semantic_space=bool(getattr(config, "scene_audio_precomputed_shared_space", False)),
        )
    if encoder_type == "frozen_torchaudio":
        return FrozenTorchaudioSceneAudioEncoder(
            hidden_size=hidden_size,
            bundle_name=getattr(config, "scene_audio_torchaudio_bundle", "WAV2VEC2_BASE"),
            sample_rate=int(getattr(config, "scene_audio_sample_rate", 16000)),
            weight_path=getattr(config, "scene_audio_torchaudio_weight_path", None) or None,
        )
    if encoder_type == "beats":
        encoder = FrozenBEATsSceneAudioEncoder(
            hidden_size=hidden_size,
            checkpoint_path=getattr(
                config,
                "scene_audio_beats_checkpoint",
                "intersuit/checkpoints/BEATs_iter3_plus_AS2M.pt",
            ),
            code_root=getattr(
                config,
                "scene_audio_beats_code_root",
                "third_party/OmniMMI/baselines/videollama2/model",
            ),
            sample_rate=int(getattr(config, "scene_audio_sample_rate", 16000)),
            expected_sha256=getattr(config, "scene_audio_beats_checkpoint_sha256", None),
            encode_chunk_size=int(
                getattr(config, "scene_audio_beats_encode_chunk_size", 0)
            ),
        )
        config.scene_audio_beats_checkpoint_sha256 = encoder.checkpoint_sha256
        return encoder
    if encoder_type == "beats_v4":
        encoder = FrozenBEATsV4SceneAudioEncoder(
            hidden_size=hidden_size,
            checkpoint_path=getattr(
                config,
                "scene_audio_beats_checkpoint",
                "intersuit/checkpoints/BEATs_iter3_plus_AS2M.pt",
            ),
            code_root=getattr(
                config,
                "scene_audio_beats_code_root",
                "third_party/OmniMMI/baselines/videollama2/model",
            ),
            semantic_projector_path=getattr(config, "scene_audio_v4_projector_path", None),
            semantic_projector_sha256=getattr(
                config, "scene_audio_v4_projector_sha256", None
            ),
            sample_rate=int(getattr(config, "scene_audio_sample_rate", 16000)),
            expected_sha256=getattr(config, "scene_audio_beats_checkpoint_sha256", None),
            encode_chunk_size=int(getattr(config, "scene_audio_beats_encode_chunk_size", 0)),
        )
        config.scene_audio_beats_checkpoint_sha256 = encoder.checkpoint_sha256
        config.scene_audio_v4_projector_sha256 = encoder.semantic_projector_sha256
        return encoder

    raise ValueError(f"Unknown scene audio encoder: {encoder_type}")
