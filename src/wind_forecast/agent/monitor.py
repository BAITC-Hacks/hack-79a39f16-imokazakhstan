"""Persistent change detection and automatic forecast recalculation.

One nonblocking poll works in a Streamlit fragment or a CLI service. The lock
covers probing, forecasting and persistence so two workers cannot spend money
on the same monitor concurrently. Only successful forecasts consume a signature.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from wind_forecast.agent.application import ApplicationRun, run_forecast
from wind_forecast.agent.integrations import BundleWeatherProvider, weather_bundle_dict
from wind_forecast.agent.settings import RunConfig, parse_time


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _file_identity(raw_path: str) -> dict:
    path = Path(raw_path).expanduser()
    identity = {"path": str(path.resolve())}
    if not path.exists():
        return {**identity, "state": "missing"}
    if not path.is_file():
        return {**identity, "state": "not_a_file"}
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size, after.st_mtime_ns, after.st_ino
    ):
        raise ValueError("An input file changed during the check; retry after writing finishes")
    return {**identity, "state": "available", "sha256": digest.hexdigest()}


def default_weather_probe(config: RunConfig) -> dict:
    """Read eligible source identity, without predicting or calling an LLM.

NOAA uses small source listings. Local bundles are selected by the same strict
eligibility rules as the forecast run. Custom providers must expose probe.
"""
    if config.weather_source == "mock":
        return {"provider": "synthetic_mock", "version": 1}
    if config.weather_source == "noaa_gfs":
        from wind_forecast.agent.noaa_archive import NoaaArchiveProvider
        provider = NoaaArchiveProvider(
            config.coordinates, config.cache_dir, wind_height_m=config.wind_height_m
        )
        return provider.probe(config.request()).to_dict()
    if config.weather_source == "bundles":
        bundle = BundleWeatherProvider(config.weather_path).fetch(config.request())
        payload = weather_bundle_dict(bundle)
        # Re-reading or copying an identical bundle does not constitute new weather.
        payload.pop("retrieved_at", None)
        return {"provider": bundle.provider, "bundle_id": bundle.bundle_id,
                "available_at": bundle.available_at.isoformat(),
                "content_sha256": _digest(payload)}
    module, name = config.weather_factory.split(":", 1)
    provider = getattr(importlib.import_module(module), name)(config=config)
    if not callable(getattr(provider, "probe", None)):
        raise TypeError("Automatic updates require the custom weather provider to expose probe(request)")
    snapshot = provider.probe(config.request())
    return snapshot.to_dict() if hasattr(snapshot, "to_dict") else snapshot


class _Busy(Exception):
    pass


@contextmanager
def _lock(path: Path):
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise _Busy from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise _Busy from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass
class MonitorPoll:
    state: str
    reasons: tuple[str, ...] = ()
    run: ApplicationRun | None = None
    last_success: dict | None = None
    next_check_at: datetime | None = None
    error: str | None = None
    input_signature: str | None = None

    def to_dict(self) -> dict:
        return {"state": self.state, "reasons": list(self.reasons), "error": self.error,
                "input_signature": self.input_signature,
                "next_check_at": self.next_check_at.isoformat() if self.next_check_at else None,
                "run_dir": str(self.run.run_dir) if self.run else None,
                "last_success_run_dir": self.last_success.get("run_dir") if self.last_success else None}


class ForecastMonitor:
    """A restartable single-monitor worker with durable last-good forecast state.

``weather_probe(config)`` returns a stable JSON value for eligible weather;
``runner(config)`` returns an ApplicationRun. Both can be injected for offline
checks. Do not share a state directory between different intended monitors.
"""

    def __init__(
        self, state_dir: str | Path, *, poll_interval_seconds: float = 300,
        rolling_live: bool = False, weather_probe: Callable | None = None,
        runner: Callable | None = None,
    ):
        if (isinstance(poll_interval_seconds, bool)
                or not isinstance(poll_interval_seconds, (float, int))
                or not math.isfinite(poll_interval_seconds)
                or not 30 <= poll_interval_seconds <= 86400):
            raise ValueError("poll_interval_seconds must be between 30 and 86400 seconds")
        if not isinstance(rolling_live, bool):
            raise TypeError("rolling_live must be a boolean")
        self.state_dir = Path(state_dir).expanduser()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "state.json"
        self.history_path = self.state_dir / "history.jsonl"
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.rolling_live = rolling_live
        self.weather_probe = weather_probe or default_weather_probe
        self.runner = runner or run_forecast

    def load_state(self) -> dict:
        if not self.state_path.exists():
            return {"version": 1, "last_success": None, "successful_signature": None}
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError("Monitor state is invalid or from an unsupported version")
        return state

    def _record(self, state: dict, event: dict) -> None:
        # The state is the authority; an append-only history supports review.
        state["last_event"] = event
        _atomic_json(self.state_path, state)
        with self.history_path.open("a", encoding="utf-8") as stream:
            stream.write(_json(event) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _components(self, config: RunConfig) -> dict:
        config_dict = config.to_dict()
        issue_time = config_dict.pop("issue_time")
        data = {} if config.mode == "fixture" else {
            turbine: _file_identity(config.data_paths[turbine])
            if config.data_paths.get(turbine) else {"state": "missing"}
            for turbine in config.turbine_ids
        }
        model = {key: _file_identity(value) for key, value in (
            ("artifact", config.model_path), ("metadata", config.model_metadata_path)
        ) if value}
        return {"configuration": _digest(config_dict), "issue_time": issue_time,
                "measurements": _digest(data), "model": _digest(model),
                "weather": _digest(self.weather_probe(config))}

    def poll(self, config: RunConfig | dict, *, now: datetime | None = None) -> MonitorPoll:
        """Check once; an unchanged input never calls the model or paid controller.

        Failed probes or forecasts leave the successful input signature intact,
        making the next due poll retry automatically. A not-yet-due check returns
        immediately. The caller schedules future polls, without sleeping here.
        """
        checked_at = parse_time(now or datetime.now(UTC))
        try:
            with _lock(self.state_dir / ".lock"):
                state = self.load_state()
                next_check = parse_time(state["next_check_at"]) if state.get("next_check_at") else None
                if next_check and checked_at < next_check:
                    return MonitorPoll("waiting", last_success=state.get("last_success"),
                                       next_check_at=next_check)
                next_check = checked_at + timedelta(seconds=self.poll_interval_seconds)
                state.update(last_checked_at=checked_at.isoformat(), next_check_at=next_check.isoformat())
                event = {"event_id": uuid4().hex, "checked_at": checked_at.isoformat(),
                         "next_check_at": next_check.isoformat()}
                reasons: tuple[str, ...] = ()
                signature = None
                try:
                    if isinstance(config, dict):
                        config = RunConfig.from_dict(config)
                    if self.rolling_live:
                        if config.mode != "live":
                            raise ValueError("rolling_live requires mode=live")
                        config = replace(config, issue_time=checked_at.replace(
                            minute=0, second=0, microsecond=0), observation_policy="available")
                    components = self._components(config)
                    signature = _digest(components)
                    previous = state.get("successful_components")
                    if previous is None:
                        reasons = ("retry_after_input_change",) if state.get("last_success") else ("initial_forecast",)
                    else:
                        labels = {"configuration": "configuration_changed", "issue_time": "issue_time_changed",
                                  "measurements": "measurements_changed", "model": "model_changed",
                                  "weather": "weather_updated"}
                        reasons = tuple(labels[key] for key, value in components.items()
                                        if previous.get(key) != value)
                    event.update(input_signature=signature, reasons=list(reasons),
                                 issue_time=config.issue_time.isoformat())
                    if signature == state.get("successful_signature"):
                        event["state"] = "unchanged"
                        self._record(state, event)
                        return MonitorPoll("unchanged", last_success=state.get("last_success"),
                                           next_check_at=next_check, input_signature=signature)
                    # If a process dies here the prior successful signature remains,
                    # so a subsequent worker can retry after the polling interval.
                    state["last_attempt"] = {**event, "state": "running"}
                    _atomic_json(self.state_path, state)
                    run = self.runner(config)
                    response = run.to_dict()
                    event.update(state=run.state, run_dir=str(run.run_dir),
                                 response_path=str(run.run_dir / "response.json"),
                                 request_id=run.request.request_id, error=run.error)
                    if run.state == "completed":
                        # A feed or artifact may be replaced while a forecast is
                        # running. Preserve the usable completed result, but do
                        # not claim the original snapshot has been consumed if
                        # a final check cannot confirm that it remained stable.
                        try:
                            stable = self._components(config) == components
                            recheck_reason = "inputs_changed_during_run"
                        except Exception as exc:  # noqa: BLE001 -- isolate external provider failures.
                            stable = False
                            recheck_reason = "inputs_recheck_failed"
                            event["recheck_error"] = f"{type(exc).__name__}: retry is scheduled"
                        event["inputs_stable"] = stable
                        state.update(last_success=response, last_success_at=checked_at.isoformat())
                        if stable:
                            state.update(successful_signature=signature, successful_components=components)
                        else:
                            # Even if the files later revert to the prior successful
                            # identity, the newly displayed run may have used moving
                            # inputs and must not suppress a clean repeat.
                            state.update(successful_signature=None, successful_components=None)
                            reasons += (recheck_reason,)
                            event["reasons"] = list(reasons)
                    state["last_attempt"] = event
                    self._record(state, event)
                    return MonitorPoll(run.state, reasons, run, state.get("last_success"),
                                       next_check, run.error, signature)
                except Exception as exc:  # noqa: BLE001 -- persistent worker must survive plugin failures.
                    # Provider/plugin exceptions can contain credentials or HTTP
                    # headers; log the type, without persisting arbitrary messages.
                    error = f"{type(exc).__name__}: update could not complete; retry is scheduled."
                    event.update(state="failed", reasons=list(reasons), error=error,
                                 input_signature=signature)
                    state["last_attempt"] = event
                    self._record(state, event)
                    return MonitorPoll("failed", reasons, last_success=state.get("last_success"),
                                       next_check_at=next_check, error=error,
                                       input_signature=signature)
        except _Busy:
            state = self.load_state()
            return MonitorPoll("busy", last_success=state.get("last_success"),
                               next_check_at=parse_time(state["next_check_at"])
                               if state.get("next_check_at") else None)
