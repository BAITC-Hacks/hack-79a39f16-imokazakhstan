"""A focused Streamlit interface: choose inputs, forecast, read and download results."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from wind_forecast.agent.application import run_forecast
from wind_forecast.agent.presentation import (
    TURBINE_NAMES,
    artifact_zip,
    forecast_chart,
    forecast_frame,
    hourly_table,
)
from wind_forecast.agent.settings import RunConfig, parse_time, runtime_settings

st.set_option("client.toolbarMode", "viewer")
st.set_page_config(page_title="WindScope · Wind power forecast", page_icon="🌬️", layout="wide")
st.markdown(
    """<style>
.block-container {max-width: 1120px; padding-top: 4rem; padding-bottom: 2rem;}
h1 {letter-spacing: -.045em; font-weight: 750 !important;}
h2, h3 {letter-spacing: -.02em;}
.eyebrow {color: #138775; font-size: .76rem; letter-spacing: .18em; font-weight: 700;}
[data-testid="stMetricValue"] {font-size: 2rem;}
[data-testid="stMetricLabel"] {font-size: .9rem;}
[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 14px;}
</style>""",
    unsafe_allow_html=True,
)


def upload_weather(raw):
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("Weather file exceeds 25 MB. Upload a single forecast bundle.")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Choose a valid weather.json file from a forecast export.") from exc
    if not isinstance(payload, dict):
        raise TypeError("The weather file must contain a complete forecast bundle.")
    path = ROOT / "data/cache/uploads" / f"{hashlib.sha256(raw).hexdigest()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    return str(path)


def file_identity(path):
    if path and Path(path).is_file():
        stat = Path(path).stat()
        return (str(path), stat.st_size, stat.st_mtime_ns)
    return None


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def error_text(error):
    message = str(error)
    for name in ("api_key", "typesafe_api_key"):
        key = runtime.get(name, "")
        if key:
            message = message.replace(key, "[redacted]")
    return message


runtime = runtime_settings()
team_ready = all(runtime.get(key) for key in ("model_factory", "model_path", "model_metadata_path"))
ai_ready = bool(runtime["api_key"] and runtime["model"])
jev_ready = bool(runtime.get("typesafe_api_key"))
local_paths = {"T1": ROOT / "data/raw/turbine_1.csv", "T2": ROOT / "data/raw/turbine_2.csv"}

st.markdown('<div class="eyebrow">WINDSCOPE / HACKALEM</div>', unsafe_allow_html=True)
st.title("Wind power forecast")
st.write("Predict hourly power for two wind turbines, up to 48 hours ahead.")

with st.container(border=True):
    st.subheader("1. Set up your forecast")
    source = st.radio(
        "Data source",
        ("Demo data", "Your measurements"),
        horizontal=True,
        key="data_source",
        help="Start with the demo, or use the organizer's measurement CSVs.",
    )
    real_data = source == "Your measurements"
    live = False
    if real_data:
        live = st.radio(
            "Forecast timing", ("Historical date", "Latest available (live)"),
            horizontal=True, key="forecast_timing",
            help="Historical uses only weather available at the selected issue. Live advances with the current UTC hour.",
        ) == "Latest available (live)"
    if not real_data:
        st.caption(
            "Demo uses simulated measurements and weather. No files, API key, or internet needed."
        )

    when, hour, length, machines = st.columns((1.4, 1, 1, 1.4))
    issue_date = when.date_input(
        "Forecast date (UTC)",
        date(2026, 2, 1),
        key="issue_date",
        disabled=live,
        help="The day the forecast is issued. February 1, 2026 is a useful case example.",
    )
    issue_hour = hour.selectbox(
        "Time (UTC)", range(24), format_func=lambda h: f"{h:02d}:00", key="issue_hour", disabled=live
    )
    horizon = length.selectbox(
        "Predict next", (24, 48), format_func=lambda h: f"{h} hours", key="horizon"
    )
    turbine_choice = machines.selectbox(
        "Turbines", ("Both turbines", "Turbine 1", "Turbine 2"), key="turbines"
    )
    turbines = {"Both turbines": ("T1", "T2"), "Turbine 1": ("T1",), "Turbine 2": ("T2",)}[
        turbine_choice
    ]
    forecast_issue = (datetime.now(UTC).replace(minute=0, second=0, microsecond=0) if live
                      else datetime.combine(issue_date, datetime.min.time(), tzinfo=UTC).replace(hour=issue_hour))
    uploads = {}
    if real_data:
        missing_local = [t for t in turbines if not local_paths[t].is_file()]
        with st.expander("Measurement files", expanded=bool(missing_local)):
            st.caption("One organizer CSV per turbine. Upload a file to replace its project copy.")
            for column, turbine in zip(st.columns(len(turbines)), turbines):
                with column:
                    uploads[turbine] = st.file_uploader(
                        TURBINE_NAMES[turbine], type="csv", key=f"csv_{turbine}"
                    )
                    if uploads[turbine] is None and local_paths[turbine].is_file():
                        st.caption(f"Using project file: {local_paths[turbine].name}")
        ready = sum(uploads[t] is not None or local_paths[t].is_file() for t in turbines)
        st.caption(f"Measurements ready: {ready} of {len(turbines)} turbines.")

    zone, interval, delay = "UTC", "start", 0
    weather_choice, weather_file, wind_height = "NOAA archive (automatic)", None, 100
    model_choice, policy = "Gradient boosting ML", "available" if live else "frozen_jan31"
    zone_labels = {
        "UTC": "UTC",
        "Etc/GMT-5": "UTC+05:00 (fixed)",
        "Etc/GMT-6": "UTC+06:00 (fixed)",
        "Asia/Almaty": "Asia/Almaty (historical clock changes)",
    }
    operational_notes, note_problem = [], ""
    with st.expander("Optional settings"):
        if real_data:
            st.markdown("**Measurement timestamps**")
            a, b, c = st.columns((1.5, 1.3, 1))
            zone = a.selectbox(
                "CSV timezone",
                tuple(zone_labels),
                format_func=zone_labels.get,
                key="source_zone",
                help="Use the CSV export's timezone; it is not written in the supplied files.",
            )
            interval = b.selectbox(
                "Timestamp marks",
                ("start", "end"),
                format_func=lambda x: f"Interval {x}",
                key="interval",
            )
            delay = int(
                c.number_input(
                    "Reporting delay (min)",
                    min_value=0,
                    max_value=1440,
                    value=0,
                    step=10,
                    key="delay",
                )
            )
            st.markdown("**Weather and prediction model**")
            a, b = st.columns(2)
            weather_choice = a.selectbox(
                "Weather input",
                ("NOAA archive (automatic)", "Upload weather file"),
                key="weather_choice",
            )
            model_choice = b.selectbox(
                "Prediction model",
                ("Gradient boosting ML", "Empirical baseline", "Team model") if team_ready
                else ("Gradient boosting ML", "Empirical baseline"), key="model_choice",
                help="ML trains on your eligible measurement history and reports chronological validation.",
            )
            if weather_choice == "Upload weather file":
                weather_file = st.file_uploader(
                    "Forecast weather JSON",
                    type="json",
                    key="weather_file",
                    help="Use weather.json from a saved run or Person 1's export. It must cover the selected time and turbines.",
                )
            else:
                wind_height = st.selectbox(
                    "Forecast wind height",
                    (100, 10),
                    format_func=lambda h: f"{h} m",
                    key="wind_height",
                )
            policy = st.selectbox(
                "Measurement history",
                ("frozen_jan31", "available"),
                key="policy",
                index=1 if live else 0,
                disabled=live,
                format_func=lambda p: (
                    "Through January 31, 2026 (case default)"
                    if p == "frozen_jan31"
                    else "All measurements available at forecast time"
                ),
            )
        use_ai = st.checkbox(
            "Use OpenAI agent",
            value=ai_ready and real_data,
            disabled=not ai_ready,
            key="use_ai",
            help="Calls weather, preparation, training, prediction, analysis and saving tools. Uses paid API requests only for a new run.",
        )
        if not ai_ready:
            st.caption(
                "Optional: set OPENAI_API_KEY and OPENAI_MODEL in .env to enable the agent. Forecasts work without it."
            )
        use_jev = st.checkbox(
            "Jev: review the next 3 hours", key="use_jev", value=False,
            disabled=not (jev_ready and real_data),
            help="Reviews operational concerns and suggests an immediate next step after the power forecast.",
        ) and real_data and jev_ready
        if not jev_ready:
            st.caption("Optional: set TYPESAFE_API_KEY in .env to enable Jev operational reviews.")
        elif not real_data:
            st.caption("Jev reviews are available with Your measurements. The demo stays offline.")
        if use_jev:
            st.caption("Sends a small forecast summary and your optional note to TypeSafe. A separate TypeSafe API request is used per new run.")
            note = st.text_area(
                "Operator note (optional)", key="operator_note", max_chars=600,
                placeholder="Example: T1 is scheduled for maintenance during the next two hours.",
                help="Write in English or Russian. Include timing, affected turbines and whether the issue is active or resolved.",
            )
            if note.strip():
                note_scope = st.multiselect("Note applies to", turbines, default=list(turbines),
                                            format_func=TURBINE_NAMES.get, key="note_scope")
                a, b = st.columns(2)
                note_at = a.text_input("Note available at (UTC)", value=forecast_issue.isoformat(), key="note_at")
                note_until = b.text_input("Note valid until (UTC)",
                    value=(forecast_issue + timedelta(hours=3)).isoformat(), key="note_until")
                st.caption("Use actual availability and expiry times. Historical notes must have been known by the forecast issue time; these times are your declaration.")
                try:
                    available, until = parse_time(note_at), parse_time(note_until)
                    if not note_scope or not available <= forecast_issue < until:
                        raise ValueError("note_window")
                    operational_notes = [{"text": note.strip(), "turbine_ids": note_scope,
                        "available_at": available.isoformat(), "valid_until": until.isoformat()}]
                except (ValueError, TypeError):
                    note_problem = "Choose note turbines and valid UTC times: available by the forecast issue, expiring after it."

    acknowledged = False
    if real_data:
        st.caption(
            f"CSV assumptions: {zone_labels[zone]} · interval {interval} · {delay} min reporting delay. Change these in Optional settings."
        )
        acknowledged = st.checkbox(
            "Use these timestamp assumptions",
            key=f"ack_{fingerprint((zone, interval, delay))[:12]}",
            help="This records your chosen interpretation. Confirm the conventions with the organizers before a scored submission.",
        )
    baseline = model_choice == "Empirical baseline"
    team_model = model_choice == "Team model"
    if live:
        policy = "available"
    if real_data:
        st.caption(
            "Model: "
            + (
                "Empirical baseline · learns average power at each wind speed."
                if baseline
                else "Configured team model." if team_model
                else "Gradient boosting ML · learns from wind, temperature and measured power."
            )
        )

    problems = []
    if note_problem:
        problems.append(note_problem)
    if real_data:
        if any(uploads[t] is None and not local_paths[t].is_file() for t in turbines):
            problems.append("Add a measurement CSV for each selected turbine.")
        if not acknowledged:
            problems.append("Confirm the timestamp assumptions above.")
        if weather_choice == "Upload weather file" and weather_file is None:
            problems.append("Add a weather JSON file in Optional settings.")
    signature_inputs = {
            "source": source,
            "live": live,
            "date": issue_date,
            "hour": issue_hour,
            "horizon": horizon,
            "turbines": turbines,
            "zone": zone,
            "interval": interval,
            "delay": delay,
            "policy": policy,
            "weather": weather_choice,
            "height": wind_height,
            "model": model_choice,
            "model_files": (
                file_identity(runtime["model_path"]),
                file_identity(runtime["model_metadata_path"]),
            )
            if team_model
            else None,
            "ai": bool(use_ai and ai_ready),
            "jev": use_jev,
            "jev_model": runtime.get("typesafe_model", "jev-1.13.0") if use_jev else None,
            "operational_notes": operational_notes,
            "sources": {
                t: hashlib.sha256(uploads[t].getvalue()).hexdigest()
                if uploads[t] is not None
                else file_identity(local_paths[t])
                for t in turbines
            }
            if real_data
            else None,
            "weather_file": hashlib.sha256(weather_file.getvalue()).hexdigest()
            if weather_file is not None
            else None,
        }
    signature = fingerprint(signature_inputs)
    if st.session_state.pop("forecast_refresh_pending", False):
        st.session_state["forecast_signature"] = signature
    control_inputs = dict(signature_inputs)
    control_inputs["sources"] = {
        t: signature_inputs["sources"][t] if uploads[t] is not None else str(local_paths[t])
        for t in turbines
    } if real_data else None
    control_inputs["model_files"] = (runtime["model_path"], runtime["model_metadata_path"]) if team_model else None
    control_signature = fingerprint(control_inputs)
    auto_update = real_data and st.checkbox(
        "Keep forecast updated automatically", key="auto_update",
        help="Checks every 5 minutes for changed project CSVs, model artifacts and eligible weather. Live also advances each UTC hour.",
    )
    if auto_update:
        st.caption("Updates run while this page stays open. The background command in the website guide works after you close the page.")
    generate = st.button(
        "Generate forecast" if real_data else "Run demo forecast",
        type="primary",
        disabled=bool(problems),
        width="stretch",
        key="generate",
    )
    if problems:
        st.caption(" ".join(problems))
    elif real_data and weather_choice == "NOAA archive (automatic)":
        st.caption(
            "The first weather download can take a few minutes; subsequent runs use a local cache."
        )
    result_notice = st.empty()

if st.session_state.get("monitor_config") and (
    not auto_update or control_signature != st.session_state.get("monitor_signature")
):
    st.session_state.pop("monitor_config", None)
    st.session_state.pop("monitor_poll", None)

if generate:
    st.session_state.pop("forecast_run", None)
    st.session_state.pop("forecast_start_error", None)
    try:
        with st.spinner("Preparing measurements, fetching weather, and predicting power…"):
            paths = {}
            if real_data:
                for turbine in turbines:
                    if uploads[turbine] is not None:
                        raw = uploads[turbine].getvalue()
                        path = ROOT / "data/cache/uploads" / f"{turbine}-{hashlib.sha256(raw).hexdigest()}.csv"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        if not path.exists():
                            path.write_bytes(raw)
                        paths[turbine] = str(path)
                    else:
                        paths[turbine] = str(local_paths[turbine])
            weather_path = (
                upload_weather(weather_file.getvalue())
                if real_data
                and weather_file is not None
                and weather_choice == "Upload weather file"
                else ""
            )
            config = RunConfig(
                issue_time=forecast_issue,
                turbine_ids=turbines,
                horizon_hours=horizon,
                mode=("live" if live else "historical") if real_data else "fixture",
                data_paths=paths,
                source_timezone=zone,
                interval_label=interval,
                reporting_delay_minutes=delay,
                assumptions_confirmed=acknowledged,
                observation_policy=policy,
                weather_source=("bundles" if weather_path else "noaa_gfs") if real_data else "mock",
                weather_path=weather_path,
                wind_height_m=wind_height,
                model_kind="empirical" if baseline or not real_data else "gradient_boosting",
                model_factory=runtime["model_factory"] if team_model else "",
                model_path=runtime["model_path"] if team_model else "",
                model_metadata_path=runtime["model_metadata_path"] if team_model else "",
                controller="openai" if use_ai and ai_ready else "deterministic",
                jev_enabled=use_jev,
                jev_model=runtime.get("typesafe_model", "jev-1.13.0") if use_jev else "jev-1.13.0",
                operational_notes=tuple(operational_notes),
                output_root=str(ROOT / "runs/application"),
                cache_dir=str(ROOT / "data/cache/weather"),
            )
            if auto_update:
                from wind_forecast.agent.monitor import ForecastMonitor
                state_dir = ROOT / "runs/monitors" / uuid4().hex
                monitor = ForecastMonitor(state_dir, rolling_live=live)
                poll = monitor.poll(config)
                st.session_state["monitor_config"] = config.to_dict()
                st.session_state["monitor_dir"] = str(state_dir)
                st.session_state["monitor_signature"] = control_signature
                st.session_state["monitor_live"] = live
                st.session_state["monitor_poll"] = poll.to_dict()
                if poll.run:
                    st.session_state["forecast_run"] = poll.run
                elif poll.error:
                    st.session_state["forecast_start_error"] = poll.error
            else:
                st.session_state["forecast_run"] = run_forecast(config)
            st.session_state["forecast_signature"] = signature
    except Exception as exc:  # noqa: BLE001 -- show input/plugin failures at the UI boundary.
        st.session_state["forecast_start_error"] = error_text(exc)

if st.session_state.get("monitor_config"):
    @st.fragment(run_every=30)
    def update_forecast():
        from wind_forecast.agent.monitor import ForecastMonitor
        monitor = ForecastMonitor(st.session_state["monitor_dir"],
                                  rolling_live=st.session_state["monitor_live"])
        poll = monitor.poll(st.session_state["monitor_config"])
        if poll.state not in {"waiting", "busy"}:
            st.session_state["monitor_poll"] = poll.to_dict()
        if poll.run and poll.run.state == "completed":
            st.session_state["forecast_run"] = poll.run
            if not {"inputs_changed_during_run", "inputs_recheck_failed"}.intersection(poll.reasons):
                st.session_state["forecast_refresh_pending"] = True
            st.session_state.pop("forecast_start_error", None)
            st.rerun()
        status = st.session_state.get("monitor_poll", {})
        if status.get("error"):
            st.warning("Automatic update could not complete: " + error_text(status["error"]))
            st.caption("The last successful forecast is kept. The agent will retry at the next check.")
        else:
            st.caption("Automatic updates active · checking input changes every 5 minutes.")
        if status.get("reasons"):
            st.caption("Latest trigger: " + ", ".join(status["reasons"]))
            if {"inputs_changed_during_run", "inputs_recheck_failed"}.intersection(status["reasons"]):
                st.warning("Inputs changed during calculation or could not be rechecked. Another calculation is scheduled.")
        if poll.next_check_at:
            st.caption(f"Next check: {poll.next_check_at:%H:%M:%S} UTC")
    update_forecast()

st.subheader("2. Read your forecast")
run = st.session_state.get("forecast_run")
start_error = st.session_state.get("forecast_start_error")
if start_error:
    st.error(f"Forecast could not start. {start_error}")
    st.caption("Check the selected files and timestamp settings, then generate again.")
elif run is None:
    st.info("Your chart and hourly predictions will appear here after you generate a forecast.")
else:
    if run.state == "completed" and signature == st.session_state.get("forecast_signature"):
        result_notice.markdown("[Forecast ready — view results ↓](#2-read-your-forecast)")
    if signature != st.session_state.get("forecast_signature"):
        st.warning(
            "Inputs or settings have changed. These results belong to the previous request; generate again or wait for the automatic update."
        )
    st.caption(
        f"Issued {run.request.issue_time:%d %b %Y, %H:%M} UTC · Next {run.request.horizon_hours} hours · "
        + ", ".join(TURBINE_NAMES[t] for t in run.request.turbine_ids)
    )
    if run.state != "completed" or run.result is None:
        st.error(
            "Forecast could not complete. " + error_text(run.error or "Review the run report.")
        )
        st.caption("Check input coverage and the weather source, then generate again.")
    else:
        result = run.result
        frame = forecast_frame(result)
        if result.is_synthetic:
            st.info(
                "Demo result · All measurements, weather, and predictions in this run are simulated."
            )
        for column, turbine in zip(
            st.columns(len(run.request.turbine_ids)), run.request.turbine_ids
        ):
            with column, st.container(border=True):
                rows = frame[frame.turbine_id == turbine]
                peak = rows.loc[rows.prediction.idxmax()]
                st.metric(
                    f"{TURBINE_NAMES[turbine]} · average normalized power",
                    f"{rows.prediction.mean():.3f}",
                )
                st.caption(
                    f"Peak {peak.prediction:.3f} · hour ending {peak.valid_time:%d %b, %H:%M} UTC"
                )
        st.altair_chart(forecast_chart(frame, run.request.horizon_hours), width="stretch")
        st.caption(
            "Each point predicts the preceding hour. Higher values mean more output. "
            "Units follow the supplied normalization; converting to MW requires its definition and turbine capacity."
        )
        if frame.p10.notna().any():
            st.caption("Shaded bands show the model's 10th–90th percentile range.")
        if (
            not result.is_synthetic
            and run.report.get("evaluation", {}).get("state") == "no_ground_truth"
        ):
            st.caption(
                "Accuracy is not yet available: there are no measured targets for this forecast window."
            )
        with st.expander("View hourly values"):
            st.dataframe(
                hourly_table(frame),
                hide_index=True,
                width="stretch",
                column_config={
                    TURBINE_NAMES[t]: st.column_config.NumberColumn(format="%.3f")
                    for t in run.request.turbine_ids
                },
            )
        with st.expander("Forecast analysis and model validation"):
            analysis = run.report.get("forecast", {})
            findings = analysis.get("findings", [])
            if findings:
                for finding in findings:
                    st.write(finding)
            else:
                st.write("All requested hours are present. Forecast values passed the numerical checks.")
            validation = run.report.get("model_validation", {})
            if validation.get("by_turbine"):
                st.caption("Chronological validation uses held-out measured wind and temperature. It does not measure 24–48 hour weather-forecast accuracy.")
                metrics = []
                for turbine, diagnostic in validation["by_turbine"].items():
                    check = diagnostic.get("validation", {})
                    for name, label in (("ml", "Gradient boosting"), ("empirical_baseline", "Empirical baseline")):
                        if check.get(name):
                            metrics.append({"Turbine": TURBINE_NAMES.get(turbine, turbine), "Model": label, **check[name]})
                if metrics:
                    st.dataframe(metrics, hide_index=True, width="stretch")
            lineage = run.report.get("lineage", {})
            if lineage.get("supersedes"):
                st.caption(f"Updated forecast: {lineage.get('changed_prediction_count', 0)} values changed from the previous version.")
        review = run.report.get("operations")
        if review:
            with st.container(border=True):
                st.subheader("Jev · Next 3 hours")
                if review["state"] == "completed":
                    context = review["input"]["state"]
                    st.caption(f"As of {context['issue_time']} · through {context['window_end']} · {review['model']}")
                    for turbine, advisory in review["by_turbine"].items():
                        st.markdown(f"**{TURBINE_NAMES[turbine]} · {advisory['condition_label']}**")
                        if advisory["status"] == "needs_review":
                            st.warning(advisory["recommendation"])
                        else:
                            st.info(advisory["recommendation"])
                        for flag in advisory["computed_flags"]:
                            st.write(flag)
                        if advisory["uncertain"]:
                            st.caption("Jev's interpretation is uncertain. Review the evidence before acting.")
                    with st.expander("Review evidence and probabilities"):
                        st.caption(review["message"] + " Routing thresholds are provisional and have not been calibrated on this site's incidents.")
                        for turbine, advisory in review["by_turbine"].items():
                            st.write(f"{TURBINE_NAMES[turbine]}: probability that review is warranted {advisory['attention_probability']:.0%}; action confidence {advisory['action_confidence']:.0%}.")
                            evidence = context["turbines"][turbine]
                            st.dataframe([{
                                "Hour ending (UTC)": row["hour_ending"],
                                "ML power (normalized)": row["predicted_power"],
                                "Forecast wind (m/s)": row["forecast_wind_ms"],
                                "Forecast temperature (°C)": row["forecast_temperature_c"],
                            } for row in evidence["next_three_hours"]], hide_index=True, width="stretch")
                            for note in evidence["operator_notes"]:
                                st.text(f"{note['id']} (operator supplied): {note['text']}")
                            if not evidence["operator_notes"]:
                                st.caption("No eligible operator notes were supplied for this turbine.")
                        excluded = context["excluded_notes"]
                        if any(excluded.values()):
                            st.caption("Excluded notes: " + ", ".join(f"{key}: {value}" for key, value in excluded.items()))
                else:
                    st.info(review["message"])
        if run.report.get("controller", {}).get("completed"):
            with st.expander("AI explanation"):
                st.markdown(run.summary)

    download_columns = st.columns(2)
    csv_path = run.run_dir / "forecast.csv"
    if run.state == "completed" and csv_path.is_file():
        download_columns[0].download_button(
            "Download forecast CSV",
            csv_path.read_bytes(),
            file_name=f"wind-forecast-{run.request.issue_time:%Y%m%d-%H%M}.csv",
            mime="text/csv",
            width="stretch",
            key="download_csv",
        )
    if run.run_dir.is_dir():
        download_columns[1].download_button(
            "Download full report (ZIP)",
            artifact_zip(run),
            file_name=f"{run.request.request_id}.zip",
            mime="application/zip",
            width="stretch",
        )
    with st.expander("How this forecast was made"):
        weather = run.weather
        if run.state == "completed":
            st.caption(
                "Weather retrieved → Data prepared → Model trained → Inputs checked → Power predicted → Results analysed"
                + (" → Jev operational review" if run.report.get("operations") else "")
                + " → Forecast saved"
            )
        controller_names = {
            "deterministic": "Python workflow",
            "openai": "OpenAI agent",
            "openai_with_deterministic_fallback": "OpenAI agent + Python fallback",
        }
        execution = run.report.get("controller", {}).get("used", "Not reached")
        assumptions = run.report.get("assumptions", {})
        details = {
            "Prediction model": run.result.model_id if run.result else "Not reached",
            "Eligible measurement hours": run.report.get("observations", {}).get("eligible", 0),
            "Weather source": weather.provider if weather else "Unavailable",
            "Weather available at": weather.available_at.strftime("%d %b %Y, %H:%M UTC")
            if weather
            else "Unavailable",
            "Execution": controller_names.get(execution, execution),
        }
        if run.request.mode != "fixture":
            details["CSV timestamps"] = (
                f"{assumptions.get('source_timezone')} · interval {assumptions.get('interval_label')} · {assumptions.get('reporting_delay_minutes')} min delay"
            )
        st.table([{"Item": k, "Value": str(v)} for k, v in details.items()])
        if weather and weather.provider.startswith("NOAA GFS"):
            st.markdown("Weather: [NOAA GFS public forecast archive](https://registry.opendata.aws/noaa-gfs-bdp-pds/)")
        steps = [{"Step": e["step"].replace("_", " "), "Status": e["status"]}
                 for e in run.trace.events if e.get("step") and e.get("status") != "started"]
        if steps:
            st.table(steps)
        if run.result:
            for warning in run.result.warnings:
                st.caption(warning)
        if run.report.get("controller", {}).get("fallback_reason"):
            st.caption("The Python workflow completed after the AI controller was unavailable.")
        for turbine, dataset in run.report.get("datasets", {}).items():
            if "dropped_incomplete_hours" in dataset:
                st.caption(
                    f"{TURBINE_NAMES.get(turbine, turbine)}: {dataset['hourly_rows']:,} complete hours; "
                    f"{dataset['dropped_incomplete_hours']:,} incomplete hours omitted."
                )
        evaluation = run.report.get("evaluation", {})
        if evaluation.get("by_turbine"):
            st.write(
                "Comparison with held-out measurements"
                + (" (synthetic demo only)" if run.result and run.result.is_synthetic else "")
            )
            st.table(
                [
                    {"Turbine": TURBINE_NAMES.get(t, t), **m}
                    for t, m in evaluation["by_turbine"].items()
                ]
            )
        st.caption(f"Saved locally in {run.run_dir}")

st.divider()
help_text, help_button = st.columns((3, 1))
help_text.caption("WindScope · Hourly wind-power forecasting · HackAlem")
guide = ROOT / "docs/website_guide.md"
if guide.is_file():
    help_button.download_button(
        "Website guide", guide.read_bytes(), file_name="website_guide.md", mime="text/markdown"
    )
