"""A focused Streamlit interface: choose inputs, forecast, read and download results."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from wind_forecast.agent.application import run_forecast
from wind_forecast.agent.input_data import load_scada
from wind_forecast.agent.presentation import (
    TURBINE_NAMES,
    artifact_zip,
    forecast_chart,
    forecast_frame,
    hourly_table,
)
from wind_forecast.agent.settings import RunConfig, runtime_settings

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


@st.cache_data(show_spinner=False, max_entries=6)
def prepare_dataset(raw, turbine, zone, interval, delay):
    return load_scada(
        raw, turbine, source_timezone=zone, interval_label=interval, reporting_delay_minutes=delay
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
    key = runtime.get("api_key", "")
    return message.replace(key, "[redacted]") if key else message


runtime = runtime_settings()
team_ready = all(runtime.get(key) for key in ("model_factory", "model_path", "model_metadata_path"))
ai_ready = bool(runtime["api_key"] and runtime["model"])
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
    if not real_data:
        st.caption(
            "Demo uses simulated measurements and weather. No files, API key, or internet needed."
        )

    when, hour, length, machines = st.columns((1.4, 1, 1, 1.4))
    issue_date = when.date_input(
        "Forecast date (UTC)",
        date(2026, 2, 1),
        key="issue_date",
        help="The day the forecast is issued. February 1, 2026 is a useful case example.",
    )
    issue_hour = hour.selectbox(
        "Time (UTC)", range(24), format_func=lambda h: f"{h:02d}:00", key="issue_hour"
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
    model_choice, policy = "Empirical baseline", "frozen_jan31"
    zone_labels = {
        "UTC": "UTC",
        "Etc/GMT-5": "UTC+05:00 (fixed)",
        "Etc/GMT-6": "UTC+06:00 (fixed)",
        "Asia/Almaty": "Asia/Almaty (historical clock changes)",
    }
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
            if team_ready:
                model_choice = b.selectbox(
                    "Prediction model", ("Team model", "Empirical baseline"), key="model_choice"
                )
            else:
                b.caption(
                    "Prediction model: **empirical baseline**. A trained team model can be connected by the project owner."
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
                format_func=lambda p: (
                    "Through January 31, 2026 (case default)"
                    if p == "frozen_jan31"
                    else "All measurements available at forecast time"
                ),
            )
        use_ai = st.checkbox(
            "Use OpenAI agent",
            value=False,
            disabled=not ai_ready,
            key="use_ai",
            help="Coordinates the Python workflow and writes an explanation. Uses paid OpenAI API requests.",
        )
        if not ai_ready:
            st.caption(
                "Optional: set OPENAI_API_KEY and OPENAI_MODEL in .env to enable the agent. Forecasts work without it."
            )

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
    if real_data:
        st.caption(
            "Model: "
            + (
                "Empirical baseline · learns average power at each wind speed."
                if baseline
                else "Configured team model."
            )
        )

    problems = []
    if real_data:
        if any(uploads[t] is None and not local_paths[t].is_file() for t in turbines):
            problems.append("Add a measurement CSV for each selected turbine.")
        if not acknowledged:
            problems.append("Confirm the timestamp assumptions above.")
        if weather_choice == "Upload weather file" and weather_file is None:
            problems.append("Add a weather JSON file in Optional settings.")
    signature = fingerprint(
        {
            "source": source,
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
            if not baseline
            else None,
            "ai": bool(use_ai and ai_ready),
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
    )
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

if generate:
    st.session_state.pop("forecast_run", None)
    st.session_state.pop("forecast_start_error", None)
    try:
        with st.spinner("Preparing measurements, fetching weather, and predicting power…"):
            datasets, paths = None, {}
            if real_data:
                datasets = {}
                for turbine in turbines:
                    raw = (
                        uploads[turbine].getvalue()
                        if uploads[turbine] is not None
                        else local_paths[turbine].read_bytes()
                    )
                    datasets[turbine] = prepare_dataset(raw, turbine, zone, interval, delay)
                    if uploads[turbine] is None:
                        paths[turbine] = str(local_paths[turbine])
            weather_path = (
                upload_weather(weather_file.getvalue())
                if real_data
                and weather_file is not None
                and weather_choice == "Upload weather file"
                else ""
            )
            config = RunConfig(
                issue_time=datetime.combine(issue_date, datetime.min.time(), tzinfo=UTC).replace(
                    hour=issue_hour
                ),
                turbine_ids=turbines,
                horizon_hours=horizon,
                mode="historical" if real_data else "fixture",
                data_paths=paths,
                source_timezone=zone,
                interval_label=interval,
                reporting_delay_minutes=delay,
                assumptions_confirmed=acknowledged,
                observation_policy=policy,
                weather_source=("bundles" if weather_path else "noaa_gfs") if real_data else "mock",
                weather_path=weather_path,
                wind_height_m=wind_height,
                model_factory=runtime["model_factory"] if not baseline else "",
                model_path=runtime["model_path"] if not baseline else "",
                model_metadata_path=runtime["model_metadata_path"] if not baseline else "",
                controller="openai" if use_ai and ai_ready else "deterministic",
                output_root=str(ROOT / "runs/application"),
                cache_dir=str(ROOT / "data/cache/weather"),
            )
            st.session_state["forecast_run"] = run_forecast(config, datasets=datasets)
            st.session_state["forecast_signature"] = signature
    except Exception as exc:  # noqa: BLE001 -- show input/plugin failures at the UI boundary.
        st.session_state["forecast_start_error"] = error_text(exc)

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
            "Settings have changed. These results belong to the previous request; generate again to update them."
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
                "Measurements prepared → Weather retrieved → Inputs checked → Power predicted → Results saved"
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
