from __future__ import annotations

from typing import get_args

from .models import SchedulerMode

# The server-defined mode constants, exactly as the SchedulerMode Literal declares them.
CANONICAL_MODES: tuple[SchedulerMode, ...] = get_args(SchedulerMode)
ALLOWED_MODES: frozenset[str] = frozenset(CANONICAL_MODES)


def validate_mode(mode: str) -> SchedulerMode:
    """Return the server-defined constant equal to ``mode``, never the caller's object.

    Receipts, metric labels and log records therefore carry one of CANONICAL_MODES by
    construction rather than request data. CodeQL py/log-injection traced
    ``payload.scheduler_mode`` through the former ``return mode`` into ``log_event``.
    """
    for canonical in CANONICAL_MODES:
        if mode == canonical:
            return canonical
    raise ValueError(f"unsupported scheduler mode: {mode}")
