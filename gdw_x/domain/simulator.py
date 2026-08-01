from __future__ import annotations

import statistics
from dataclasses import dataclass

from .contracts import SimulationResult
from .drift import expected_calibration_error


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    predictions: tuple[float, ...]
    raw_confidence: tuple[float, ...]
    returns: tuple[float, ...]
    realized_costs: tuple[float, ...]
    fault_tags: tuple[str, ...] = ()

    def validate(self) -> None:
        sizes = {len(self.predictions), len(self.raw_confidence), len(self.returns), len(self.realized_costs)}
        if len(sizes) != 1 or not self.predictions:
            raise ValueError("scenario arrays must be non-empty and aligned")


@dataclass(frozen=True)
class ThresholdPolicy:
    trade_threshold: float = 0.01
    abstain_threshold: float = 0.55
    quarantine_threshold: float = 0.25
    maximum_position: float = 1.0


def max_drawdown(equity: list[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        drawdown = min(drawdown, value - peak)
    return abs(drawdown)


def run_scenario(
    scenario: Scenario,
    *,
    confidence_multiplier: float,
    policy: ThresholdPolicy | None = None,
) -> SimulationResult:
    scenario.validate()
    active_policy = policy or ThresholdPolicy()
    calibrated = [min(1.0, max(0.0, value * confidence_multiplier)) for value in scenario.raw_confidence]
    outcomes = [1.0 if prediction * realized_return > 0.0 else 0.0 for prediction, realized_return in zip(scenario.predictions, scenario.returns)]
    actions: list[float] = []
    governance_blocks = 0
    for prediction, confidence in zip(scenario.predictions, calibrated):
        if confidence < active_policy.quarantine_threshold:
            governance_blocks += 1
            actions.append(0.0)
        elif confidence < active_policy.abstain_threshold or abs(prediction) < active_policy.trade_threshold:
            actions.append(0.0)
        else:
            direction = 1.0 if prediction > 0.0 else -1.0
            actions.append(direction * min(abs(prediction), active_policy.maximum_position))
    pnl = [
        action * realized_return - abs(action) * cost
        for action, realized_return, cost in zip(actions, scenario.returns, scenario.realized_costs)
    ]
    equity: list[float] = []
    running = 0.0
    for value in pnl:
        running += value
        equity.append(running)
    false_actions = [action != 0.0 and outcome == 0.0 for action, outcome in zip(actions, outcomes)]
    abstentions = [action == 0.0 for action in actions]
    return SimulationResult(
        scenario_id=scenario.scenario_id,
        observations=len(actions),
        directional_accuracy=statistics.fmean(outcomes),
        calibration_error=expected_calibration_error(calibrated, outcomes),
        abstention_rate=statistics.fmean(abstentions),
        false_action_rate=statistics.fmean(false_actions),
        utility=sum(pnl),
        maximum_drawdown=max_drawdown(equity),
        governance_blocks=governance_blocks,
        fault_tags=list(scenario.fault_tags),
    )


def default_scenarios() -> dict[str, Scenario]:
    return {
        "abrupt_regime_shift": Scenario(
            scenario_id="abrupt_regime_shift",
            predictions=(0.8, 0.6, 0.5, 0.4, -0.5, -0.7, -0.8, -0.9),
            raw_confidence=(0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55),
            returns=(0.02, 0.01, 0.01, -0.01, 0.02, 0.03, 0.02, 0.01),
            realized_costs=(0.001,) * 8,
            fault_tags=("abrupt_drift",),
        ),
        "gradual_drift": Scenario(
            scenario_id="gradual_drift",
            predictions=(0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, -0.1),
            raw_confidence=(0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5),
            returns=(0.02, 0.01, 0.01, 0.005, -0.005, -0.01, -0.01, -0.02),
            realized_costs=(0.001,) * 8,
            fault_tags=("gradual_drift",),
        ),
        "recurring_regime": Scenario(
            scenario_id="recurring_regime",
            predictions=(0.6, -0.6, 0.5, -0.5, 0.7, -0.7, 0.4, -0.4),
            raw_confidence=(0.8,) * 8,
            returns=(0.02, -0.02, 0.01, -0.01, 0.02, -0.02, 0.01, -0.01),
            realized_costs=(0.001,) * 8,
            fault_tags=("recurring_regime",),
        ),
        "telemetry_loss": Scenario(
            scenario_id="telemetry_loss",
            predictions=(0.5,) * 8,
            raw_confidence=(0.0,) * 8,
            returns=(0.01, -0.01) * 4,
            realized_costs=(0.001,) * 8,
            fault_tags=("telemetry_loss",),
        ),
    }
