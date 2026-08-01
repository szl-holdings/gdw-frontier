from __future__ import annotations


def calibration_error(predicted_confidence: float, observed_success: float) -> float:
    return predicted_confidence - observed_success


def update_threshold(
    current: float,
    observed_error: float,
    learning_rate: float = 0.1,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    if minimum > maximum:
        raise ValueError("minimum threshold cannot exceed maximum threshold")
    proposed = current - learning_rate * observed_error
    return min(maximum, max(minimum, proposed))


def propose_threshold_update(
    current: float,
    predicted_confidence: float,
    observed_success: float,
    learning_rate: float = 0.1,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> dict[str, float]:
    error = calibration_error(predicted_confidence, observed_success)
    proposed = update_threshold(current, error, learning_rate, minimum, maximum)
    return {
        "current_threshold": current,
        "predicted_confidence": predicted_confidence,
        "observed_success": observed_success,
        "calibration_error": error,
        "learning_rate": learning_rate,
        "proposed_threshold": proposed,
        "proposed_delta": proposed - current,
    }
