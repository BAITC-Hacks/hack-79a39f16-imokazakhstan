"""Per-turbine supervised power regression with chronological diagnostics.

The validation set uses *measured* weather. Its scores describe the regression
component, not 24–48 hour performance with archived numerical weather forecasts.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from wind_forecast.contracts import (
    ForecastRequest,
    ForecastResult,
    ForecastRow,
    Observation,
    WeatherBundle,
    require_utc,
)
from wind_forecast.models.power_curve import _audit_weather

FEATURES = ("wind_ms", "temp_c", "hour_sin", "hour_cos", "year_sin", "year_cos")
VALIDATION_NOTE = (
    "Chronological holdout with measured wind/temperature; these scores are not "
    "24–48 hour forecast accuracy. Evaluate against original archived weather "
    "to measure the complete forecasting system."
)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _features(wind: float, temperature: float | None, time: datetime) -> list[float]:
    hour = 2 * math.pi * time.hour / 24
    year_start = time.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    next_year = year_start.replace(year=time.year + 1)
    season = 2 * math.pi * (time - year_start).total_seconds() / (next_year - year_start).total_seconds()
    return [float(wind), float(temperature) if _finite(temperature) else float("nan"),
            math.sin(hour), math.cos(hour), math.sin(season), math.cos(season)]


def _metrics(actual: list[float], predicted: Iterable[float]) -> dict[str, float | int]:
    errors = [float(pred) - target for target, pred in zip(actual, predicted, strict=True)]
    return {"n": len(errors), "mae": sum(abs(e) for e in errors) / len(errors),
            "rmse": math.sqrt(sum(e * e for e in errors) / len(errors)),
            "bias": sum(errors) / len(errors)}


def _baseline_predictions(train: list[Observation], valid: list[Observation]) -> list[float]:
    bins: dict[int, list[float]] = defaultdict(list)
    for row in train:
        bins[int(row.wind_ms)].append(float(row.power_norm))
    means = {key: sum(values) / len(values) for key, values in bins.items()}
    return [means[min(means, key=lambda key: abs(key - int(row.wind_ms)))] for row in valid]


class HistogramPowerRegressor:
    """Train one CPU gradient-boosted model per turbine using eligible hours.

    Explicit issue_time is mandatory: fit filters both measurement time and
    publication time. There is no implicit fallback when training is too sparse.
    The application keeps its dependency-free baseline for synthetic fixtures.
    """

    model_id = "histogram-gradient-boosting-power-v1"
    features = FEATURES

    def __init__(self, *, min_samples: int = 168, max_iter: int = 160) -> None:
        if type(min_samples) is not int or min_samples < 96:
            raise ValueError("min_samples must be an integer of at least 96")
        if type(max_iter) is not int or max_iter < 1:
            raise ValueError("max_iter must be a positive integer")
        self.min_samples = min_samples
        self.max_iter = max_iter
        self.training_report: dict[str, Any] = {}
        self.trained_through: datetime | None = None
        self._models: dict[str, Any] = {}
        self._ranges: dict[str, dict[str, float | None]] = {}
        self._latest_availability: datetime | None = None

    def _estimator(self):
        try:
            from sklearn.ensemble import HistGradientBoostingRegressor
        except ImportError as exc:
            raise ImportError("ML forecasting requires scikit-learn; install project requirements") from exc
        return HistGradientBoostingRegressor(
            loss="squared_error", learning_rate=0.06, max_iter=self.max_iter,
            max_leaf_nodes=15, min_samples_leaf=20, l2_regularization=1.0,
            early_stopping=False, random_state=42,
        )

    def fit(
        self,
        observations: list[Observation],
        *,
        issue_time: datetime,
        training_limit: datetime | None = None,
        turbine_ids: tuple[str, ...] | None = None,
    ) -> "HistogramPowerRegressor":
        """Filter, diagnose on a chronological holdout, then refit eligible data.

        No fitted state survives a failed refit. Hyperparameters are fixed; the
        holdout is reported without selecting/tuning a model on its scores.
        """
        try:
            import sklearn
            from threadpoolctl import threadpool_limits
        except ImportError as exc:
            raise ImportError("ML forecasting requires scikit-learn; install project requirements") from exc

        self._models = {}
        self._ranges = {}
        self.training_report = {}
        self.trained_through = None
        self._latest_availability = None
        issue = require_utc(issue_time, "issue_time")
        limit = min(issue, require_utc(training_limit, "training_limit")) if training_limit else issue
        requested = tuple(turbine_ids) if turbine_ids is not None else tuple(sorted({r.turbine_id for r in observations}))
        if not requested or len(set(requested)) != len(requested):
            raise ValueError("fit needs unique turbine_ids and at least one turbine")
        grouped: dict[str, list[Observation]] = defaultdict(list)
        excluded: Counter[str] = Counter()
        seen: set[tuple[str, datetime]] = set()
        missing_temperature = 0
        for row in observations:
            if row.turbine_id not in requested:
                excluded["unrequested_turbine"] += 1
                continue
            if row.observed_at > limit or row.available_at > issue:
                excluded["unavailable_at_issue_or_after_training_limit"] += 1
                continue
            if row.available_at < row.observed_at:
                raise ValueError("observation available_at precedes its completed hour")
            if any((row.observed_at.minute, row.observed_at.second, row.observed_at.microsecond)):
                raise ValueError("training observations must be complete UTC hour ends")
            key = (row.turbine_id, row.observed_at)
            if key in seen:
                raise ValueError("duplicate turbine/hour in training observations")
            seen.add(key)
            if row.quality_flag != "ok":
                excluded["quality_flag"] += 1
                continue
            if not _finite(row.wind_ms) or not _finite(row.power_norm) or row.wind_ms < 0:
                excluded["missing_or_invalid_wind_power"] += 1
                continue
            if not _finite(row.temp_c):
                missing_temperature += 1
            grouped[row.turbine_id].append(row)

        models, ranges, reports = {}, {}, {}
        eligible: list[Observation] = []
        for turbine in requested:
            rows = sorted(grouped[turbine], key=lambda r: r.observed_at)
            if len(rows) < self.min_samples:
                raise ValueError(f"ML model needs at least {self.min_samples} valid hourly rows for {turbine}; got {len(rows)}")
            eligible.extend(rows)
            holdout_count = min(720, max(24, math.ceil(len(rows) * 0.2)))
            valid = rows[-holdout_count:]
            # Emulate one fixed initial issue. Reporting-delayed rows are purged.
            validation_issue = valid[0].observed_at - timedelta(hours=1)
            train = [row for row in rows[:-holdout_count] if row.available_at <= validation_issue]
            features = [_features(r.wind_ms, r.temp_c, r.observed_at) for r in rows]
            targets = [float(r.power_norm) for r in rows]
            validation: dict[str, Any] = {"state": "unavailable", "note": VALIDATION_NOTE,
                "validation_issue": validation_issue.isoformat(), "training_rows": len(train),
                "holdout_rows": len(valid), "holdout_start": valid[0].observed_at.isoformat(),
                "holdout_end": valid[-1].observed_at.isoformat(),
                "purged_delayed_training_rows": len(rows) - holdout_count - len(train)}
            if len(train) >= 48:
                diagnostic_model = self._estimator()
                with threadpool_limits(limits=1):
                    diagnostic_model.fit(
                        [_features(r.wind_ms, r.temp_c, r.observed_at) for r in train],
                        [r.power_norm for r in train],
                    )
                    validation_predictions = diagnostic_model.predict(
                        [_features(r.wind_ms, r.temp_c, r.observed_at) for r in valid])
                actual = [float(r.power_norm) for r in valid]
                ml_metrics = _metrics(actual, validation_predictions)
                baseline_metrics = _metrics(actual, _baseline_predictions(train, valid))
                validation.update({"state": "available", "training_end": train[-1].observed_at.isoformat(),
                    "ml": ml_metrics, "empirical_baseline": baseline_metrics,
                    "ml_better_mae": ml_metrics["mae"] < baseline_metrics["mae"]})
            else:
                validation["reason"] = "Fewer than 48 eligible training hours before holdout issue."
            model = self._estimator()
            with threadpool_limits(limits=1):
                model.fit(features, targets)
            temperatures = [float(r.temp_c) for r in rows if _finite(r.temp_c)]
            feature_range = {"wind_min": min(r.wind_ms for r in rows),
                "wind_max": max(r.wind_ms for r in rows), "power_min": min(targets),
                "power_max": max(targets), "temp_min": min(temperatures) if temperatures else None,
                "temp_max": max(temperatures) if temperatures else None}
            models[turbine], ranges[turbine] = model, feature_range
            reports[turbine] = {"training_rows": len(rows), "training_start": rows[0].observed_at.isoformat(),
                "training_end": rows[-1].observed_at.isoformat(),
                "missing_temperature_rows": len(rows) - len(temperatures),
                "feature_ranges": feature_range, "validation": validation}

        serial_rows = [[r.turbine_id, r.observed_at.isoformat(), r.available_at.isoformat(),
                        r.wind_ms, r.temp_c if _finite(r.temp_c) else None, r.power_norm]
                       for r in sorted(eligible, key=lambda r: (r.turbine_id, r.observed_at))]
        training_hash = hashlib.sha256(json.dumps(serial_rows, allow_nan=False).encode()).hexdigest()
        self._models, self._ranges = models, ranges
        self.trained_through = max(r.observed_at for r in eligible)
        self._latest_availability = max(r.available_at for r in eligible)
        self.training_report = {"model_id": self.model_id, "algorithm": "HistGradientBoostingRegressor",
            "scikit_learn_version": sklearn.__version__,
            "features": list(FEATURES), "target_units": "normalized_active_power",
            "trained_through": self.trained_through.isoformat(), "training_limit": limit.isoformat(),
            "latest_training_available_at": self._latest_availability.isoformat(),
            "training_data_hash": training_hash, "training_rows": len(eligible),
            "excluded_rows": dict(excluded), "missing_temperature_rows": missing_temperature,
            "hyperparameters": models[requested[0]].get_params(), "by_turbine": reports,
            "validation_note": VALIDATION_NOTE,
            "missing_temperature_policy": "Native missing-value branches; no future-value imputation.",
            "output_policy": "Original normalized scale; no assumed [0,1] clipping or conversion to MW."}
        return self

    def predict(self, request: ForecastRequest, observations: list[Observation], weather: WeatherBundle) -> ForecastResult:
        from threadpoolctl import threadpool_limits

        del observations  # Prediction features come exclusively from issued weather and calendar time.
        if not self._models or self.trained_through is None or self._latest_availability is None:
            raise ValueError("fit the ML model before calling predict")
        if max(self.trained_through, self._latest_availability) > request.issue_time:
            raise ValueError("model training observations were unavailable at requested issue_time")
        _audit_weather(request, weather)
        if weather.run_init_time > request.issue_time:
            raise ValueError("weather run initialized after issue_time")
        if request.mode == "live" and (weather.is_synthetic or weather.provenance_status != "verified_original"):
            raise ValueError("live ML forecasting requires verified real weather")
        if any(t not in self._models for t in request.turbine_ids):
            raise ValueError("ML model has no fitted estimator for a requested turbine")

        warnings = [VALIDATION_NOTE]
        degraded = weather.is_synthetic
        if weather.is_synthetic:
            warnings.append("Synthetic demo output; not an evaluation forecast.")
        output: list[ForecastRow] = []
        for turbine in request.turbine_ids:
            points = sorted((p for p in weather.rows if p.turbine_id == turbine), key=lambda p: p.valid_time)
            if any(not _finite(p.wind_ms) or p.wind_ms < 0 for p in points):
                raise ValueError("ML forecast requires finite nonnegative wind for every turbine/hour")
            feature_range = self._ranges[turbine]
            out_of_range = sum(not feature_range["wind_min"] <= p.wind_ms <= feature_range["wind_max"] for p in points)
            if out_of_range:
                degraded = True
                warnings.append(f"{turbine}: {out_of_range} weather hours have wind outside the training range; tree extrapolation is limited.")
            missing_temp = sum(not _finite(p.temp_c) for p in points)
            if missing_temp:
                warnings.append(f"{turbine}: {missing_temp} weather hours have missing temperature; model missing-value branches were used.")
            with threadpool_limits(limits=1):
                predicted = self._models[turbine].predict([_features(p.wind_ms, p.temp_c, p.valid_time) for p in points])
            for point, value in zip(points, predicted, strict=True):
                value = float(value)
                if not math.isfinite(value):
                    raise ValueError("ML model returned a non-finite prediction")
                output.append(ForecastRow(turbine, request.issue_time, point.valid_time,
                    int((point.valid_time - request.issue_time).total_seconds() // 3600), value))
        identity = {"model": self.model_id, "training": self.training_report["training_data_hash"],
            "parameters": self.training_report["hyperparameters"], "issue_time": request.issue_time.isoformat(),
            "weather": [[p.turbine_id, p.valid_time.isoformat(), p.wind_ms,
                p.temp_c if _finite(p.temp_c) else None] for p in sorted(weather.rows, key=lambda p: (p.turbine_id, p.valid_time))]}
        return ForecastResult(f"forecast-{request.request_id}", request.request_id, "1.0", self.model_id,
            weather.bundle_id, hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest(),
            datetime.now(timezone.utc), "degraded" if degraded else "ok", weather.is_synthetic,
            tuple(warnings), tuple(output))
