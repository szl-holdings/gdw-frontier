from __future__ import annotations

from .models import Decision, ReceiptStatus


def decide(*, confidence: float, receipt_status: ReceiptStatus) -> Decision:
    """Apply a small deterministic MODELED policy; this is not a production trust claim."""
    if receipt_status != "valid":
        return "withhold"
    if confidence >= 0.85:
        return "allow"
    if confidence >= 0.60:
        return "review"
    return "withhold"

