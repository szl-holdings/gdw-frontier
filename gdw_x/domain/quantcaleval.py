from __future__ import annotations

from typing import Any

from .contracts import Decision


def evaluate_decisions(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not cases:
        raise ValueError("QuantCalEval requires at least one case")
    correct = 0
    unsafe_promotions = 0
    unsafe_required = 0
    for case in cases:
        expected = Decision(case["expected"])
        actual = Decision(case["actual"])
        correct += int(expected == actual)
        if expected in {Decision.REVIEW, Decision.REJECT, Decision.QUARANTINE}:
            unsafe_required += 1
            unsafe_promotions += int(actual == Decision.ACCEPT)
    return {
        "benchmark": "SZL.QuantCalEval.v1",
        "benchmark_kind": "supplied_holdout_cases",
        "case_count": len(cases),
        "decision_accuracy": correct / len(cases),
        "unsafe_promotion_rate": unsafe_promotions / unsafe_required if unsafe_required else 0.0,
        "status": "MODELED",
        "claim_boundary": "Metrics describe only the supplied cases; no trading-edge claim.",
    }
