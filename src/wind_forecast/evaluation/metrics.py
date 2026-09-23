"""Small metric helpers. Pair only forecasts and targets that are actually available."""

from __future__ import annotations

from collections.abc import Sequence
from math import sqrt


def regression_metrics(actual: Sequence[float], predicted: Sequence[float]) -> dict[str, float]:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("actual and predicted must have equal, nonzero lengths")
    errors = [prediction - truth for truth, prediction in zip(actual, predicted, strict=True)]
    return {
        "n": float(len(errors)),
        "mae": sum(abs(error) for error in errors) / len(errors),
        "rmse": sqrt(sum(error * error for error in errors) / len(errors)),
    }
