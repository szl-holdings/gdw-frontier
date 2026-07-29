from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class WorkspaceState:
    max_events: int = 1_000
    _events: deque[dict[str, Any]] = field(default_factory=deque)
    _lock: Lock = field(default_factory=Lock)

    def record(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(event)
            while len(self._events) > self.max_events:
                self._events.popleft()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            decisions = Counter(str(item["decision"]) for item in self._events)
            return {
                "status": "MODELED",
                "bounded_event_count": len(self._events),
                "decision_mix": dict(sorted(decisions.items())),
            }

