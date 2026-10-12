"""Causal per-stream smoothing and immutable snapshots for audio gates."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class StreamingGateConfig:
    ema_beta: float = 0.8
    on_threshold: float = 0.05
    hold_threshold: float = 0.025
    max_rise: float = 0.25
    max_fall: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.ema_beta < 1.0:
            raise ValueError("ema_beta must be in [0,1)")
        if not 0.0 <= self.hold_threshold <= self.on_threshold <= 1.0:
            raise ValueError("gate thresholds must satisfy 0 <= hold <= on <= 1")
        if not 0.0 < self.max_rise <= 1.0 or not 0.0 < self.max_fall <= 1.0:
            raise ValueError("max_rise and max_fall must be in (0,1]")


@dataclass(frozen=True)
class StreamingGateUpdate:
    stream_id: str
    memory_version: int
    timestamp_sec: float
    raw_gate: float
    ema_gate: float
    output_gate: float
    active: bool


@dataclass(frozen=True)
class StreamingMemorySnapshot:
    stream_id: str
    memory_version: int
    last_input_timestamp: float
    gate_value: float
    gate_active: bool
    question_id: str | None = None


@dataclass
class _MutableGateState:
    memory_version: int = 0
    last_timestamp: float = float("-inf")
    ema_gate: float = 0.0
    output_gate: float = 0.0
    active: bool = False


class StreamingConfidenceStateManager:
    """Keep explicit causal gate state isolated by ``stream_id``."""

    def __init__(self, config: StreamingGateConfig | None = None) -> None:
        self.config = config or StreamingGateConfig()
        self._states: dict[str, _MutableGateState] = {}

    def update(
        self,
        stream_id: str,
        raw_gate: float,
        timestamp_sec: float,
        force_close: bool = False,
    ) -> StreamingGateUpdate:
        key = _validate_stream_id(stream_id)
        raw = float(raw_gate)
        timestamp = float(timestamp_sec)
        if not math.isfinite(raw) or not 0.0 <= raw <= 1.0:
            raise ValueError("raw_gate must be finite and in [0,1]")
        if not math.isfinite(timestamp):
            raise ValueError("timestamp_sec must be finite")
        state = self._states.setdefault(key, _MutableGateState())
        if timestamp < state.last_timestamp:
            raise ValueError("timestamps must be non-decreasing within a stream")

        if force_close:
            ema = 0.0
            active = False
            output = 0.0
            raw = 0.0
        else:
            beta = self.config.ema_beta
            ema = beta * state.ema_gate + (1.0 - beta) * raw
            threshold = self.config.hold_threshold if state.active else self.config.on_threshold
            active = ema >= threshold
            target = ema if active else 0.0
            lower = max(0.0, state.output_gate - self.config.max_fall)
            upper = min(1.0, state.output_gate + self.config.max_rise)
            output = min(max(target, lower), upper)

        state.memory_version += 1
        state.last_timestamp = timestamp
        state.ema_gate = ema
        state.output_gate = output
        state.active = active
        return StreamingGateUpdate(
            stream_id=key,
            memory_version=state.memory_version,
            timestamp_sec=timestamp,
            raw_gate=raw,
            ema_gate=ema,
            output_gate=output,
            active=active,
        )

    def snapshot(
        self,
        stream_id: str,
        question_id: str | None = None,
    ) -> StreamingMemorySnapshot:
        key = _validate_stream_id(stream_id)
        if key not in self._states:
            raise KeyError(f"Unknown stream_id: {key}")
        state = self._states[key]
        return StreamingMemorySnapshot(
            stream_id=key,
            memory_version=state.memory_version,
            last_input_timestamp=state.last_timestamp,
            gate_value=state.output_gate,
            gate_active=state.active,
            question_id=question_id,
        )

    def reset(self, stream_id: str) -> None:
        self._states.pop(_validate_stream_id(stream_id), None)

    def has_stream(self, stream_id: str) -> bool:
        return _validate_stream_id(stream_id) in self._states

    def __len__(self) -> int:
        return len(self._states)


def _validate_stream_id(stream_id: str) -> str:
    value = str(stream_id).strip()
    if not value:
        raise ValueError("stream_id must be non-empty")
    return value
