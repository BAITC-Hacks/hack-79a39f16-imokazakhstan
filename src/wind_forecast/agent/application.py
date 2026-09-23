"""Public application service shared by Streamlit, Python callers and the CLI."""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from wind_forecast.agent.artifacts import save_forecast_run, _safe_source_uri
from wind_forecast.agent.input_data import ObservationDataset, load_scada
from wind_forecast.agent.integrations import BundleWeatherProvider, load_team_predictor, weather_bundle_dict
from wind_forecast.agent.runner import WorkflowSession
from wind_forecast.agent.settings import RunConfig, parse_time, runtime_settings
from wind_forecast.contracts import ForecastRequest, ForecastResult, WeatherBundle, WorkflowTrace
from wind_forecast.data.loader import load_observations
from wind_forecast.demo_data import sample_observations
from wind_forecast.models.power_curve import EmpiricalPowerCurve
from wind_forecast.weather.mock import MockWeatherProvider


def json_safe(value):
    return json.loads(json.dumps(value, default=lambda v: v.isoformat() if isinstance(v, datetime)
                               else str(v) if isinstance(v, Path) else asdict(v), allow_nan=False))


def _write(path, data):
    path.write_text(json.dumps(json_safe(data), indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


@dataclass
class ApplicationRun:
    state: str
    request: ForecastRequest
    result: ForecastResult | None
    weather: WeatherBundle | None
    trace: WorkflowTrace
    run_dir: Path
    report: dict
    summary: str
    error: str | None = None

    def to_dict(self):
        return json_safe({"state": self.state, "request": self.request, "result": self.result,
                          "run_dir": self.run_dir, "report": self.report,
                          "summary": self.summary, "error": self.error})


def load_datasets(config: RunConfig) -> dict[str, ObservationDataset]:
    if config.mode == "fixture":
        rows = sample_observations()
        return {t: ObservationDataset(tuple(r for r in rows if r.turbine_id == t),
                    {"synthetic": True, "source": "built-in synthetic fixture"}) for t in config.turbine_ids}
    output = {}
    for turbine in config.turbine_ids:
        path = config.data_paths.get(turbine)
        if not path:
            raise ValueError(f"provide a CSV path or uploaded dataset for {turbine}")
        if config.input_format == "organizer":
            output[turbine] = load_scada(path, turbine, source_timezone=config.source_timezone,
                interval_label=config.interval_label, reporting_delay_minutes=config.reporting_delay_minutes)
        else:
            rows = load_observations(path)
            if any(r.turbine_id != turbine for r in rows):
                raise ValueError(f"canonical CSV mapped to {turbine} contains another turbine")
            output[turbine] = ObservationDataset(tuple(rows), {"source": Path(path).name,
                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "hourly_rows": len(rows),
                "convention": "canonical aware UTC hour-end timestamps"})
    return output


def _validate_observations(rows, turbines):
    seen = set()
    for r in rows:
        key = (r.turbine_id, r.observed_at)
        if key in seen or r.turbine_id not in turbines:
            raise ValueError("duplicate or unexpected turbine observation")
        seen.add(key)
        if any((r.observed_at.minute, r.observed_at.second, r.observed_at.microsecond)):
            raise ValueError("canonical observations must represent complete UTC hour ends")
        if r.available_at < r.observed_at:
            raise ValueError("observation available_at precedes its hour end")
        for value in (r.power_norm, r.wind_ms, r.temp_c):
            if value is not None and (isinstance(value, bool) or not math.isfinite(value)):
                raise ValueError("observation contains non-finite/non-numeric values")
        if r.wind_ms is not None and r.wind_ms < 0:
            raise ValueError("observed wind speed must be nonnegative")


def _provider(config):
    if config.weather_source == "mock":
        return MockWeatherProvider()
    if config.weather_source == "bundles":
        return BundleWeatherProvider(config.weather_path)
    if config.weather_source == "noaa_gfs":
        from wind_forecast.agent.noaa_archive import NoaaArchiveProvider
        return NoaaArchiveProvider(config.coordinates, config.cache_dir, wind_height_m=config.wind_height_m)
    if not config.weather_factory or ":" not in config.weather_factory:
        raise ValueError("weather_factory must be a trusted installed module:function")
    module, name = config.weather_factory.split(":", 1)
    provider = getattr(importlib.import_module(module), name)(config=config)
    if not callable(getattr(provider, "fetch", None)):
        raise ValueError("weather factory must return an object with fetch(request)")
    return provider


def _evaluate(result, observations):
    actuals = {(r.turbine_id, r.observed_at): r.power_norm for r in observations
               if r.power_norm is not None and r.quality_flag == "ok"}
    groups = {}
    for turbine in sorted({r.turbine_id for r in result.rows}):
        pairs = [(r.prediction, actuals[(r.turbine_id, r.valid_time)]) for r in result.rows
                 if r.turbine_id == turbine and (r.turbine_id, r.valid_time) in actuals]
        if pairs:
            errors = [p-y for p,y in pairs]
            groups[turbine] = {"n": len(pairs), "mae": sum(map(abs, errors))/len(errors),
                              "rmse": math.sqrt(sum(e*e for e in errors)/len(errors))}
    matched = sum(g["n"] for g in groups.values())
    return {"state": "available" if matched else "no_ground_truth", "matched_rows": matched,
            "missing_rows": len(result.rows)-matched, "by_turbine": groups,
            "note": "Held-out labels are used only after prediction. Metrics require eligible original weather; synthetic metrics are not scored."}


def _lineage(config, result, current):
    candidates = []
    for path in Path(config.output_root).glob("*/response.json"):
        if path.parent == current:
            continue
        try:
            old = json.loads(path.read_text())
            req = old["request"]
            old_result = old["result"]
            if not isinstance(old_result, dict) or not isinstance(old_result.get("rows"), list):
                continue
            if not all(isinstance(r,dict) and isinstance(r.get("prediction"),(int,float))
                       and not isinstance(r["prediction"],bool) and math.isfinite(r["prediction"])
                       and isinstance(r.get("turbine_id"),str) and isinstance(r.get("valid_time"),str)
                       for r in old_result["rows"]):
                continue
            if not isinstance(old_result.get("input_hash"),str):
                continue
            if (old["state"] == "completed" and req["issue_time"] == config.issue_time.isoformat()
                    and req["mode"] == config.mode and set(req["turbine_ids"]) == set(config.turbine_ids)
                    and req["horizon_hours"] == config.horizon_hours):
                candidates.append((path.stat().st_mtime_ns, path.parent.name, old))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if not candidates:
        return {"supersedes": None, "inputs_changed": None}
    _, version, old = max(candidates, key=lambda item:item[0])
    old_rows = {(r["turbine_id"], r["valid_time"]):r["prediction"] for r in old["result"]["rows"]}
    deltas = [abs(r.prediction-old_rows.get((r.turbine_id,r.valid_time.isoformat()),r.prediction)) for r in result.rows]
    return {"supersedes": version, "inputs_changed": old["result"]["input_hash"] != result.input_hash,
            "changed_prediction_count": sum(d>1e-12 for d in deltas), "largest_change": max(deltas,default=0)}


def _safe_error(exc):
    # Known local validation failures are useful. Arbitrary plugin/network exceptions may contain secrets.
    if isinstance(exc, (ValueError, FileNotFoundError, ImportError)):
        text = str(exc)[:600]
        for key, value in os.environ.items():
            if value and len(value)>6 and any(part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                text = text.replace(value, "[redacted]")
        return text
    return f"{type(exc).__name__}: operation could not complete; check configured input files, provider connectivity and installed dependencies."


def run_forecast(config: RunConfig | dict, *, datasets=None, predictor=None, weather_provider=None,
                 model_metadata=None, jev_client=None) -> ApplicationRun:
    """Execute one issue. Invalid configuration raises ValueError; run failures return saved states.

    Injected predictors/providers are trusted Python dependencies, never browser uploads.
    Training metadata is mandatory for injected predictors. Numerical outputs always
    come from predictor.predict, including when OpenAI orchestrates the tools.
    """
    if isinstance(config, dict):
        config = RunConfig.from_dict(config)
    request = config.request()
    trace = WorkflowTrace(request.request_id)
    report = {"assumptions": {"source_timezone":config.source_timezone,
        "interval_label":config.interval_label, "reporting_delay_minutes":config.reporting_delay_minutes,
        "operator_acknowledged":config.assumptions_confirmed,
        "organizer_confirmation": "not established by this application",
        "output_interval": "(valid_time - 1 hour, valid_time]",
        "units":"normalized_active_power; capacity and normalization denominator unconfirmed"},
        "training_limit":config.training_limit.isoformat(), "controller":{"requested":config.controller}}
    session, saved_dir, fetched_weather = None, None, None
    selected_provider = weather_provider
    all_rows, observations, training_hash = [], [], ""
    prepared = False
    warnings = ["Normalization denominator and capacity are unconfirmed; do not interpret values as MW or MWh."]

    def record(step, status, detail):
        trace.events.append({"run_id": request.request_id, "step": step, "status": status,
            "event_time": datetime.now(timezone.utc).isoformat(),
            "simulated_issue_time": request.issue_time.isoformat(), "detail": detail})

    try:
        if config.mode != "fixture" and not config.assumptions_confirmed:
            raise ValueError("acknowledge the selected timezone and interval assumptions before running real data")

        def fetch_weather():
            nonlocal fetched_weather, selected_provider
            if fetched_weather is None:
                record("fetch_weather", "started", "Retrieve original forecast by turbine coordinates")
                selected_provider = selected_provider or _provider(config)
                fetched_weather = selected_provider.fetch(request)
                record("fetch_weather", "ok", {"provider": fetched_weather.provider,
                    "rows": len(fetched_weather.rows), "available_at": fetched_weather.available_at.isoformat()})
            return {"provider": fetched_weather.provider, "rows": len(fetched_weather.rows),
                "available_at": fetched_weather.available_at.isoformat(),
                "provenance": fetched_weather.provenance_status, "synthetic": fetched_weather.is_synthetic}

        def prepare_data():
            nonlocal datasets, all_rows, observations, training_hash, prepared
            if fetched_weather is None:
                raise ValueError("fetch_weather must complete before prepare_data")
            if prepared:
                return report["observations"]
            record("prepare_data", "started", "Prepare complete hourly observations available at issue time")
            if config.mode == "fixture":
                datasets = load_datasets(config)
            else:
                datasets = datasets if datasets is not None else load_datasets(config)
            if any(t not in datasets for t in config.turbine_ids):
                raise ValueError("provide an observation dataset for each selected turbine")
            all_rows = [r for t in config.turbine_ids for r in datasets[t].observations]
            _validate_observations(all_rows, request.turbine_ids)
            observations = [r for r in all_rows if r.observed_at <= config.training_limit
                            and r.available_at <= request.issue_time and r.quality_flag == "ok"]
            for t in config.turbine_ids:
                if not any(r.turbine_id == t and r.wind_ms is not None and r.power_norm is not None for r in observations):
                    raise ValueError(f"no eligible wind/power training pairs for {t}")
            report["datasets"] = {t:datasets[t].report for t in config.turbine_ids}
            report["observations"] = {"eligible":len(observations), "excluded":len(all_rows)-len(observations),
                "latest_hour_end":max(r.observed_at for r in observations).isoformat(),
                "latest_available_at":max(r.available_at for r in observations).isoformat()}
            training_hash = hashlib.sha256(json.dumps(json_safe([asdict(r) for r in sorted(observations,
                key=lambda r:(r.turbine_id,r.observed_at))]),sort_keys=True).encode()).hexdigest()
            prepared = True
            record("prepare_data", "ok", report["observations"])
            return report["observations"]

        def train_model():
            nonlocal predictor, model_metadata, session
            if not prepared:
                raise ValueError("prepare_data must complete before train_model")
            if session is not None:
                return {"model": report["model"], "validation": report.get("model_validation", {})}
            record("train_model", "started", "Fit or load numerical predictor using eligible history")
            if predictor is None and config.model_factory:
                predictor, model_metadata = load_team_predictor(config.model_factory, config.model_path,
                    config.model_metadata_path, issue_time=request.issue_time, training_limit=config.training_limit, mode=config.mode)
            elif predictor is None and (config.model_kind == "gradient_boosting" or
                                         config.model_kind == "auto" and config.mode != "fixture"):
                from wind_forecast.models.gradient_boosting import HistogramPowerRegressor
                predictor = HistogramPowerRegressor().fit(observations, issue_time=request.issue_time,
                    training_limit=config.training_limit, turbine_ids=request.turbine_ids)
                model_metadata = {"model_id": predictor.model_id,
                    "trained_through": predictor.trained_through.isoformat(),
                    "training_data_hash": predictor.training_report["training_data_hash"],
                    "observation_data_hash": training_hash, "features": list(predictor.features),
                    "hyperparameters": predictor.training_report["hyperparameters"],
                    "scikit_learn_version": predictor.training_report["scikit_learn_version"],
                    "target_units": "normalized_active_power", "trained_on_synthetic": config.mode == "fixture",
                    "kind": "histogram gradient boosting fitted for this issue"}
                report["model_validation"] = predictor.training_report
                warnings.append("ML validation uses measured weather; archived-forecast accuracy needs separate replay evaluation.")
            elif predictor is None:
                predictor = EmpiricalPowerCurve().fit(observations)
                model_metadata = {"model_id":predictor.model_id,"trained_through":max(r.observed_at for r in observations).isoformat(),
                    "training_data_hash":training_hash,"features":["wind_ms","turbine_id"],
                    "target_units":"normalized_active_power","trained_on_synthetic":config.mode=="fixture",
                    "kind":"empirical baseline fitted for this issue"}
                warnings.append("Empirical baseline: coarse weather-grid wind differs from measured turbine wind; validate/calibrate before claiming accuracy.")
            if not model_metadata or model_metadata.get("model_id") != predictor.model_id:
                raise ValueError("predictor requires matching model metadata")
            if parse_time(model_metadata["trained_through"]) > config.training_limit:
                raise ValueError("model was trained beyond this issue's observation cutoff")
            if config.mode != "fixture" and model_metadata.get("trained_on_synthetic") is not False:
                raise ValueError("real runs require a model declared trained_on_synthetic=false")
            if model_metadata.get("target_units") != "normalized_active_power":
                raise ValueError("model target_units must be normalized_active_power")
            if not isinstance(model_metadata.get("features"),list) or not model_metadata["features"]:
                raise ValueError("model metadata must list its input features")
            if not isinstance(model_metadata.get("training_data_hash"),str) or not model_metadata["training_data_hash"]:
                raise ValueError("model metadata requires a training_data_hash")
            report["model"] = model_metadata
            session = WorkflowSession(request, observations, selected_provider, predictor,
                model_identity=model_metadata, trace=trace, extra_warnings=warnings)
            # The application-owned fetch tool already performed stage zero.
            session.weather, session.stage = fetched_weather, 1
            record("train_model", "ok", {"model_id": predictor.model_id,
                "validation": report.get("model_validation", {})})
            return {"model": report["model"], "validation": report.get("model_validation", {})}

        def session_step(name):
            if session is None:
                raise ValueError("train_model must complete before forecast tools")
            return getattr(session, name)()

        def inspect():
            analysis = session_step("inspect_forecast")
            if config.jev_enabled and "operations" not in report:
                from wind_forecast.agent.jev import review_operations
                record("review_operations", "started", "Interpret near-term operational evidence with Jev")
                report["operations"] = review_operations(
                    config, session.result, session.weather, observations, client=jev_client)
                review = report["operations"]
                record("review_operations", review["state"], {
                    "model": review.get("model"), "input_hash": review.get("input_hash"),
                    "needs_review": review.get("needs_review"), "reason": review.get("reason"),
                })
            review = report.get("operations")
            if review:
                return {**analysis, "operations": {"state": review["state"],
                    "by_turbine": review.get("by_turbine", {}), "message": review["message"]}}
            return analysis

        def save():
            nonlocal saved_dir
            if session is None:
                raise ValueError("train_model must complete before save_forecast")
            session._ready(4)
            if saved_dir is None:
                session.record("save_forecast", "ok", "Persist a new immutable forecast version")
                artifacts = save_forecast_run(session.result, trace, session.weather, config.output_root)
                saved_dir = artifacts.run_dir
            return {"saved": True, "version": saved_dir.name, "rows": len(session.result.rows)}

        tools = {"fetch_weather": fetch_weather, "prepare_data": prepare_data, "train_model": train_model,
            "audit_inputs": lambda: session_step("audit_inputs"),
            "predict_power": lambda: session_step("predict_power"),
            "inspect_forecast": inspect, "save_forecast": save}
        ai_text = ""
        if config.controller == "openai":
            settings = runtime_settings()
            if settings["api_key"] and settings["model"]:
                from wind_forecast.agent.openai_controller import OpenAIController
                outcome = OpenAIController(settings["api_key"],settings["model"]).run(
                    {"request":json_safe(asdict(request)), "model_selection":config.model_kind,
                     "workflow_steps":list(tools), "limitations":warnings}, tools,
                    is_complete=lambda:saved_dir is not None)
                trace.events.extend(outcome.events)
                report["controller"].update({"used":"openai" if outcome.completed else "openai_with_deterministic_fallback","usage":outcome.usage,
                    "fallback_reason":outcome.fallback_reason,"completed":outcome.completed})
                ai_text = outcome.report if outcome.completed else ""
            else:
                report["controller"].update({"used":"deterministic","fallback_reason":"OPENAI_API_KEY or OPENAI_MODEL is missing"})
        else:
            report["controller"]["used"] = "deterministic"
        # Safe continuation is idempotent even if an API failure occurred after saving.
        for function in tools.values():
            function()
        report["forecast"] = session.summary
        report["evaluation"] = _evaluate(session.result, all_rows)
        report["lineage"] = _lineage(config, session.result, saved_dir)
        report["weather"] = {"provider":session.weather.provider,"provenance":session.weather.provenance_status,
            "available_at":session.weather.available_at.isoformat(), "run_init_time":session.weather.run_init_time.isoformat(),
            "source_hash":session.weather.source_hash,"source_uri":_safe_source_uri(session.weather.source_uri)}
        summary = (f"Generated {len(session.result.rows)} hourly normalized-power predictions for "
                   f"{', '.join(request.turbine_ids)}, issued {request.issue_time.isoformat()}. "
                   f"Model: {predictor.model_id}. " +
                   ("SYNTHETIC demonstration; not a submission. " if config.mode == "fixture" else "") +
                   ("No observed targets are available for accuracy scoring at these forecast times." if report["evaluation"]["state"] == "no_ground_truth" else
                    "Held-out accuracy metrics are in report.json; review coverage and provenance."))
        if ai_text:
            summary += "\n\n" + ai_text
        if "operations" in report:
            review = report["operations"]
            summary += "\n\nJev operational review: " + review["state"] + ". " + review["message"]
            for turbine, advisory in review.get("by_turbine", {}).items():
                summary += f"\n- {turbine}: {advisory['recommendation']}"
        run = ApplicationRun("completed",request,session.result,session.weather,trace,saved_dir,report,summary)
    except Exception as exc:
        error = _safe_error(exc)
        state = "blocked" if isinstance(exc, (ValueError, FileNotFoundError, ImportError)) else "failed"
        trace.events.append({"step":"application","status":state,"detail":error})
        if saved_dir is None:
            saved_dir = Path(config.output_root) / f"{state}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:10]}"
            saved_dir.mkdir(parents=True,exist_ok=False)
        run = ApplicationRun(state,request,None,session.weather if session else fetched_weather,trace,saved_dir,report,
                             "Forecast not completed. " + error,error)
    _write(run.run_dir/"request.json",config.to_dict())
    _write(run.run_dir/"report.json",run.report)
    if "operations" in run.report:
        _write(run.run_dir/"operations.json",run.report["operations"])
    if run.weather is not None:
        weather_data = weather_bundle_dict(run.weather)
        weather_data["source_uri"] = _safe_source_uri(run.weather.source_uri)
        _write(run.run_dir/"weather.json",weather_data)
        if config.weather_source == "noaa_gfs" and re.fullmatch(r"[A-Za-z0-9_-]+",run.weather.bundle_id):
            evidence = Path(config.cache_dir)/"manifests"/(run.weather.bundle_id+".json")
            if evidence.is_file():
                shutil.copyfile(evidence,run.run_dir/"weather_source_manifest.json")
    (run.run_dir/"trace.jsonl").write_text("".join(json.dumps(json_safe(e),ensure_ascii=False)+"\n" for e in trace.events),encoding="utf-8")
    (run.run_dir/"summary.md").write_text(run.summary+"\n",encoding="utf-8")
    _write(run.run_dir/"response.json",run.to_dict())  # completion marker written last
    return run
