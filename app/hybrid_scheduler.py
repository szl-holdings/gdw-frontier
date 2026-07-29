from __future__ import annotations

from .models import SchedulerMode

ALLOWED_MODES: frozenset[str] = frozenset({"interactive", "balanced", "throughput"})


def validate_mode(mode: str) -> SchedulerMode:
    if mode not in ALLOWED_MODES:
        raise ValueError(f"unsupported scheduler mode: {mode}")
    return mode  # type: ignore[return-value]

