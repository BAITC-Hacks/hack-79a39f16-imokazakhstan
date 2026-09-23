"""Strict artifact boundaries for the team's weather and model implementations.

``verified_original`` is an assertion made by the weather provider and backed by
its recorded availability evidence. Schema validation cannot independently
prove that a public archive existed at the asserted time. Inspect that evidence
before accepting a teammate's bundle for a scored historical replay.

Model factories are trusted server/CLI configuration. Never populate a factory
string from an uploaded file, an LLM tool argument, or an untrusted web form.
The teammate's factory owns its framework and deserialization choices.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from wind_forecast.contracts import (
    ForecastRequest,
    WeatherBundle,
    WeatherPoint,
    require_utc,
)

_MODES = {"fixture", "historical", "live"}
_PROVENANCE = {"verified_original", "unverified", "synthetic"}
_FACTORY = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


class BundleWeatherProvider:
    """Select the latest eligible complete weather bundle from JSON artifacts.

    A directory must contain canonical bundle ``*.json`` files. A JSON object
    may be the bundle itself or contain it under ``weather``. Metadata-only run
    manifests cannot replace full bundles: the complete ``rows`` array is
    required. Larger bundles are sliced to the exact requested turbine/hours.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch(self, request: ForecastRequest) -> WeatherBundle:
        if request.mode not in _MODES:
            raise ValueError(f"unsupported forecast mode: {request.mode}")
        if self.path.is_file():
            paths = [self.path]
        elif self.path.is_dir():
            paths = sorted(self.path.glob("*.json"))
            if not paths:
                raise ValueError("weather bundle directory contains no JSON files")
        else:
            raise ValueError("weather bundle path does not exist or is not a file/directory")

        expected = {
            (turbine, request.issue_time + timedelta(hours=lead))
            for turbine in request.turbine_ids
            for lead in range(1, request.horizon_hours + 1)
        }
        eligible: list[WeatherBundle] = []
        excluded = {"unavailable_at_issue": 0, "unverified_or_synthetic": 0, "missing_hours": 0}
        for path in paths:
            payload = _read_json_object(path, "weather bundle")
            if "weather" in payload:
                payload = _object(payload["weather"], "weather")
            try:
                bundle = _parse_bundle(payload)
            except ValueError as exc:
                raise ValueError(f"invalid weather bundle {path.name}: {exc}") from exc
            if (
                bundle.available_at > request.issue_time
                or bundle.run_init_time > request.issue_time
            ):
                excluded["unavailable_at_issue"] += 1
                continue
            if request.mode in {"historical", "live"} and (
                bundle.provenance_status != "verified_original" or bundle.is_synthetic
            ):
                excluded["unverified_or_synthetic"] += 1
                continue
            keys = {(row.turbine_id, row.valid_time) for row in bundle.rows}
            if not expected <= keys:
                excluded["missing_hours"] += 1
                continue
            eligible.append(bundle)
        if not eligible:
            reasons = ", ".join(f"{key}={count}" for key, count in excluded.items() if count)
            raise ValueError(
                f"no eligible weather bundle for {request.issue_time.isoformat()} "
                f"and all requested turbine/hours ({reasons or 'no bundles'})"
            )
        selected = max(
            eligible,
            key=lambda bundle: (bundle.available_at, bundle.run_init_time, bundle.bundle_id),
        )
        rows = tuple(
            sorted(
                (row for row in selected.rows if (row.turbine_id, row.valid_time) in expected),
                key=lambda row: (row.turbine_id, row.valid_time),
            )
        )
        return replace(selected, rows=rows)


def weather_bundle_dict(bundle: WeatherBundle) -> dict[str, object]:
    """Serialize all canonical metadata and rows for replay/persistence.

    Source metadata must contain credential-free source URIs. API keys do not
    belong in weather artifacts.
    """
    return {
        "bundle_id": bundle.bundle_id,
        "provider": bundle.provider,
        "weather_model": bundle.weather_model,
        "run_init_time": bundle.run_init_time.isoformat(),
        "available_at": bundle.available_at.isoformat(),
        "retrieved_at": bundle.retrieved_at.isoformat(),
        "availability_basis": bundle.availability_basis,
        "source_uri": bundle.source_uri,
        "source_hash": bundle.source_hash,
        "provenance_status": bundle.provenance_status,
        "is_synthetic": bundle.is_synthetic,
        "rows": [
            {
                "turbine_id": row.turbine_id,
                "valid_time": row.valid_time.isoformat(),
                "wind_ms": row.wind_ms,
                "temp_c": row.temp_c,
                "wind_direction_deg": row.wind_direction_deg,
                "wind_height_m": row.wind_height_m,
            }
            for row in bundle.rows
        ],
    }


def load_team_predictor(
    factory: str,
    model_path: str,
    metadata_path: str,
    *,
    issue_time: datetime,
    training_limit: datetime,
    mode: str,
) -> tuple[Any, dict[str, object]]:
    """Validate a model artifact, then call a configured trusted Python factory.

    The factory is ``module:function`` and is called with keyword arguments
    ``model_path: Path`` and ``metadata: dict``. Its result must expose matching
    ``model_id`` and callable ``predict(request, observations, weather)``.
    Artifact hashing binds metadata to the supplied file; the team must also
    substantiate its declared training cutoff and data hash.
    """
    if not isinstance(factory, str) or not _FACTORY.fullmatch(factory):
        raise ValueError("model factory must use the trusted 'module:function' format")
    if mode not in _MODES:
        raise ValueError(f"unsupported forecast mode: {mode}")
    if not isinstance(issue_time, datetime) or not isinstance(training_limit, datetime):
        raise ValueError("issue_time and training_limit must be timezone-aware datetimes")
    issue = require_utc(issue_time, "issue_time")
    limit = require_utc(training_limit, "training_limit")
    path = Path(model_path)
    if not path.is_file():
        raise ValueError("model_path must point to an existing single model file")
    metadata = _read_json_object(Path(metadata_path), "model metadata")
    model_id = _text(metadata.get("model_id"), "model_id")
    trained_through = _timestamp(metadata.get("trained_through"), "trained_through")
    if trained_through > min(issue, limit):
        raise ValueError("model trained_through exceeds issue_time or training_limit")
    features = metadata.get("features")
    if not isinstance(features, list) or not features:
        raise ValueError("model features must be a nonempty list of feature names")
    feature_names = [_text(feature, "features entry") for feature in features]
    if len(set(feature_names)) != len(feature_names):
        raise ValueError("model features must not contain duplicate names")
    if metadata.get("target_units") != "normalized_active_power":
        raise ValueError("model target_units must be 'normalized_active_power'")
    synthetic = _boolean(metadata.get("trained_on_synthetic"), "trained_on_synthetic")
    if mode in {"historical", "live"} and synthetic:
        raise ValueError("historical/live forecasts require a model trained on real data")
    _text(metadata.get("training_data_hash"), "training_data_hash")
    expected_hash = _text(metadata.get("artifact_sha256"), "artifact_sha256")
    if not _SHA256.fullmatch(expected_hash):
        raise ValueError("artifact_sha256 must contain exactly 64 hexadecimal characters")
    actual_hash = _file_sha256(path)
    if actual_hash != expected_hash.lower():
        raise ValueError("model artifact_sha256 does not match the model file")

    module_name, function_name = factory.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        builder = getattr(module, function_name)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"cannot import configured model factory: {factory}") from exc
    if not callable(builder):
        raise ValueError("configured model factory is not callable")
    normalized_metadata = dict(metadata)
    normalized_metadata["trained_through"] = trained_through.isoformat()
    normalized_metadata["artifact_sha256"] = actual_hash
    predictor = builder(model_path=path, metadata=dict(normalized_metadata))
    if getattr(predictor, "model_id", None) != model_id:
        raise ValueError("predictor model_id does not match model metadata")
    if not callable(getattr(predictor, "predict", None)):
        raise ValueError("predictor must expose a callable predict method")
    return predictor, normalized_metadata


def _parse_bundle(payload: dict[str, Any]) -> WeatherBundle:
    texts = {
        field: _text(payload.get(field), field)
        for field in (
            "bundle_id",
            "provider",
            "weather_model",
            "availability_basis",
            "source_uri",
            "source_hash",
        )
    }
    provenance = payload.get("provenance_status")
    if not isinstance(provenance, str) or provenance not in _PROVENANCE:
        raise ValueError("provenance_status must be verified_original, unverified, or synthetic")
    synthetic = _boolean(payload.get("is_synthetic"), "is_synthetic")
    if synthetic != (provenance == "synthetic"):
        raise ValueError("is_synthetic and provenance_status disagree")
    run_init = _timestamp(payload.get("run_init_time"), "run_init_time")
    available = _timestamp(payload.get("available_at"), "available_at")
    retrieved = _timestamp(payload.get("retrieved_at"), "retrieved_at")
    if run_init > available:
        raise ValueError("run_init_time must not be later than available_at")
    if retrieved < available:
        raise ValueError("retrieved_at must not be earlier than available_at")
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("weather rows must be a nonempty list with complete point data")
    rows: list[WeatherPoint] = []
    keys: set[tuple[str, datetime]] = set()
    for index, raw in enumerate(raw_rows):
        row = _object(raw, f"rows[{index}]")
        turbine = _text(row.get("turbine_id"), f"rows[{index}].turbine_id")
        valid_time = _timestamp(row.get("valid_time"), f"rows[{index}].valid_time")
        if any((valid_time.minute, valid_time.second, valid_time.microsecond)):
            raise ValueError(f"rows[{index}].valid_time must align to a UTC hour")
        key = (turbine, valid_time)
        if key in keys:
            raise ValueError("weather rows contain a duplicate turbine/valid_time key")
        keys.add(key)
        wind = _number(row.get("wind_ms"), f"rows[{index}].wind_ms", optional=False)
        if wind < 0:
            raise ValueError(f"rows[{index}].wind_ms must be nonnegative")
        if "temp_c" not in row:
            raise ValueError(f"rows[{index}].temp_c is required (null is permitted)")
        temperature = _number(row["temp_c"], f"rows[{index}].temp_c")
        direction = _number(row.get("wind_direction_deg"), f"rows[{index}].wind_direction_deg")
        height = _number(row.get("wind_height_m"), f"rows[{index}].wind_height_m")
        if direction is not None and not 0 <= direction <= 360:
            raise ValueError(f"rows[{index}].wind_direction_deg must be between 0 and 360")
        if height is not None and height <= 0:
            raise ValueError(f"rows[{index}].wind_height_m must be positive")
        rows.append(
            WeatherPoint(
                turbine_id=turbine,
                valid_time=valid_time,
                wind_ms=wind,
                temp_c=temperature,
                wind_direction_deg=direction,
                wind_height_m=height,
            )
        )
    return WeatherBundle(
        **texts,
        run_init_time=run_init,
        available_at=available,
        retrieved_at=retrieved,
        provenance_status=provenance,
        is_synthetic=synthetic,
        rows=tuple(rows),
    )


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{label} path must point to an existing JSON file")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8-sig"),
            parse_constant=_invalid_constant,
            parse_float=_finite_json_float,
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"cannot read valid {label} JSON from {path.name}: {exc}") from exc
    return _object(payload, label)


def _invalid_constant(value: str) -> None:
    raise ValueError(f"nonfinite JSON constant: {value}")


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON number exceeds the finite numeric range")
    return number


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a JSON boolean")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    _text(value, field)
    try:
        return require_utc(datetime.fromisoformat(value.replace("Z", "+00:00")), field)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp with an explicit timezone") from exc


def _number(value: Any, field: str, *, optional: bool = True) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite JSON number" + (" or null" if optional else ""))
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
