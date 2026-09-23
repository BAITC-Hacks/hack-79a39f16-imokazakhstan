"""Streamlit application for fixture, historical, and live wind-power forecasts."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from wind_forecast.agent.application import run_forecast
from wind_forecast.agent.input_data import load_scada
from wind_forecast.agent.settings import RunConfig, runtime_settings


st.set_page_config(page_title="WindScope · HackAlem", page_icon="🌬️", layout="wide")
st.markdown(
    """<style>
    .block-container {max-width: 1250px; padding-top: 2.2rem;}
    .wind-eyebrow {font-size: .78rem; font-weight: 700; letter-spacing: .16em;
        color: #29a78d; margin-bottom: .35rem;}
    .wind-intro {font-size: 1.08rem; opacity: .75; margin-top: -.5rem;}
    </style>""",
    unsafe_allow_html=True,
)


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


@st.cache_data(show_spinner=False, max_entries=6)
def prepare_dataset(raw: bytes, turbine: str, zone: str, interval: str, delay: int):
    return load_scada(
        raw,
        turbine,
        source_timezone=zone,
        interval_label=interval,
        reporting_delay_minutes=delay,
    )


def uploaded_weather_path(raw: bytes) -> str:
    """Store JSON data under a content hash; uploaded filenames are never paths."""
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("The weather JSON exceeds the 25 MB upload limit.")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Upload a valid UTF-8 weather bundle JSON object.") from exc
    if not isinstance(payload, dict):
        raise ValueError("The weather bundle must be a JSON object with metadata and rows.")
    # Keep the original JSON so the canonical adapter can reject duplicate keys
    # and nonfinite numbers without silently normalizing the uploaded content.
    path = ROOT / "data" / "cache" / "uploads" / f"{hashlib.sha256(raw).hexdigest()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    return str(path)


def artifact_zip(run, payload: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(run.run_dir.iterdir()):
            allowed = path.suffix in {".csv", ".json", ".jsonl", ".md"}
            if path.is_file() and not path.is_symlink() and allowed:
                archive.writestr(path.name, path.read_bytes())
        archive.writestr("application_response.json", json_bytes(payload))
    return buffer.getvalue()


def safe_error(error: object, runtime: dict) -> str:
    message = str(error)
    api_key = runtime.get("api_key", "")
    return message.replace(api_key, "[redacted]") if api_key else message


runtime = runtime_settings()
team_model_ready = all(
    runtime.get(field) for field in ("model_factory", "model_path", "model_metadata_path")
)
openai_ready = bool(runtime.get("api_key") and runtime.get("model"))

with st.sidebar:
    st.markdown("### Forecast request")
    mode = st.radio(
        "Mode",
        ("fixture", "historical", "live"),
        format_func=lambda value: {
            "fixture": "Synthetic demo", "historical": "Historical replay", "live": "Live forecast",
        }[value],
    )
    issue_date = st.date_input("Issue date (UTC)", value=date(2026, 2, 1))
    issue_hour = st.selectbox(
        "Issue hour (UTC)", range(24), format_func=lambda hour: f"{hour:02d}:00",
    )
    horizon = st.radio("Hours ahead", (24, 48), index=1, horizontal=True)
    turbines = st.multiselect("Turbines", ("T1", "T2"), default=("T1", "T2"))
    if mode == "live":
        st.caption("Set the issue date and hour to the current UTC hour for a live run.")
    st.divider()
    controller = st.selectbox(
        "Workflow controller", ("deterministic", "openai"),
        format_func=lambda value: "Local workflow" if value == "deterministic" else "OpenAI agent",
    )
    if controller == "openai":
        if openai_ready:
            st.caption("OpenAI is configured. Each run uses a bounded number of paid API requests.")
        else:
            st.caption("OpenAI is not configured. The local workflow will handle this run.")
    else:
        st.caption("The local workflow performs every retrieval, audit, prediction, and save step.")
    st.divider()
    st.caption("Each run is saved as a new version. Previous results remain available on disk.")
    guide_path = ROOT / "docs" / "application_io.md"
    if guide_path.exists():
        st.download_button("Application & teammate guide", guide_path.read_bytes(),
                           file_name="application_io.md", key="sidebar_guide")

st.markdown('<div class="wind-eyebrow">HACKALEM / WIND ENERGY</div>', unsafe_allow_html=True)
st.title("WindScope")
st.markdown(
    '<p class="wind-intro">Hourly forecasts, traceable inputs, and an inspectable AI workflow.</p>',
    unsafe_allow_html=True,
)
if mode == "fixture":
    st.warning("Synthetic demo: weather, measurements, and predictions are simulated.")
else:
    st.info(
        "Forecasts use normalized active power for each turbine. "
        "The saved report records data assumptions, weather evidence, and model limitations."
    )

source_timezone = "UTC"
interval_label = "start"
reporting_delay = 0
assumptions_acknowledged = False
observation_policy = "frozen_jan31"
weather_source = "mock"
weather_path = ""
weather_upload = None
wind_height = 100
model_choice = "baseline"
input_choice = "local"
uploads = {}
local_paths = {"T1": ROOT / "data/raw/turbine_1.csv", "T2": ROOT / "data/raw/turbine_2.csv"}

if mode != "fixture":
    inputs, weather_settings = st.columns((1.15, 1), gap="large")
    with inputs:
        with st.container(border=True):
            st.subheader("1 · Measurements")
            input_choice = st.radio(
                "Measurement source", ("local", "upload"), horizontal=True,
                format_func=lambda value: "Project files" if value == "local" else "Upload CSVs",
            )
            if input_choice == "local":
                for turbine in turbines:
                    present = local_paths[turbine].is_file()
                    st.caption(
                        f"{'✓' if present else '○'} {turbine} · "
                        f"data/raw/{local_paths[turbine].name} · "
                        f"{'available' if present else 'missing'}"
                    )
            else:
                for turbine in turbines:
                    uploads[turbine] = st.file_uploader(
                        f"{turbine} organizer CSV", type=["csv"], key=f"scada_upload_{turbine}",
                    )
            st.caption("Original organizer CSVs are converted from 10 minute readings to complete hours.")
            source_timezone = st.selectbox(
                "Source timestamp timezone", ("UTC", "Asia/Almaty", "Etc/GMT-5", "Etc/GMT-6"),
                format_func=lambda value: {
                    "UTC": "UTC", "Asia/Almaty": "Asia/Almaty (historical clock changes)",
                    "Etc/GMT-5": "UTC+05:00 (fixed)", "Etc/GMT-6": "UTC+06:00 (fixed)",
                }[value],
                help="Choose the convention used by the CSV export. The file does not declare it.",
            )
            interval_label = st.selectbox(
                "A source timestamp marks the", ("start", "end"),
                format_func=lambda value: f"{value.capitalize()} of a 10 minute interval",
            )
            reporting_delay = int(st.number_input(
                "Reporting delay (minutes)", min_value=0, max_value=1440, value=0, step=10,
            ))
            observation_policy = st.selectbox(
                "Observations available to the model", ("frozen_jan31", "available"),
                index=1 if mode == "live" else 0,
                format_func=lambda value: (
                    "Freeze at January 31, 2026" if value == "frozen_jan31"
                    else "All observations available at issue time"
                ),
                help="Use the frozen policy for the case unless the organizers permit rolling observations.",
            )
            assumptions_acknowledged = st.checkbox(
                "I acknowledge these explicit timezone, interval, and availability assumptions."
            )
            st.caption("This acknowledgement does not mean the organizers have confirmed the assumptions.")
    with weather_settings:
        with st.container(border=True):
            st.subheader("2 · Weather forecast")
            weather_source = st.radio(
                "Weather source", ("noaa_gfs", "bundles"),
                format_func=lambda value: (
                    "NOAA GFS archive" if value == "noaa_gfs" else "Teammate weather bundle"
                ),
            )
            if weather_source == "noaa_gfs":
                wind_height = st.selectbox("Forecast wind height", (100, 10), format_func=lambda h: f"{h} m")
                st.caption(
                    "Downloads public forecast fields and caches them locally. "
                    "A first 24 hour run can transfer about 67 MB and take several minutes."
                )
                st.caption("Case locations: T1 43.645150, 78.535604 · T2 43.643198, 78.538828. "
                           "Both may share the same coarse weather grid cell.")
            else:
                bundle_choice = st.radio("Bundle input", ("Upload JSON", "Project path"), horizontal=True)
                if bundle_choice == "Upload JSON":
                    weather_upload = st.file_uploader("Canonical weather bundle", type=["json"])
                else:
                    weather_path = st.text_input(
                        "Weather JSON file or directory", value="data/processed/weather",
                    ).strip()
                st.caption("The bundle must include hourly rows and evidence of availability at issue time.")
        with st.container(border=True):
            st.subheader("3 · Numerical model")
            model_choice = st.radio(
                "Predictor", ("baseline", "team"),
                format_func=lambda value: (
                    "Empirical wind-to-power baseline" if value == "baseline" else "Teammate model"
                ),
            )
            if model_choice == "team":
                if team_model_ready:
                    st.success("Teammate model configuration is available.")
                else:
                    st.warning("Configure the teammate model factory, artifact, and metadata in the environment.")
            else:
                st.caption("Fits a numerical wind-to-power baseline using eligible measurements only.")

problems = []
if not turbines:
    problems.append("Select at least one turbine.")
if mode != "fixture":
    if not assumptions_acknowledged:
        problems.append("Acknowledge the selected data assumptions.")
    if input_choice == "local":
        missing = [turbine for turbine in turbines if not local_paths[turbine].is_file()]
        if missing:
            problems.append(f"Add project CSVs for {', '.join(missing)} or choose Upload CSVs.")
    elif any(uploads.get(turbine) is None for turbine in turbines):
        problems.append("Upload a CSV for each selected turbine.")
    if weather_source == "bundles" and not weather_path and weather_upload is None:
        problems.append("Upload a weather bundle or provide its project path.")
    if model_choice == "team" and not team_model_ready:
        problems.append("Configure the teammate model before selecting it.")

st.divider()
run_label = "Run synthetic forecast" if mode == "fixture" else "Generate forecast"
if st.session_state.get("application_run") is not None:
    run_label = "Recalculate · save a new version"
run_clicked = st.button(run_label, type="primary", disabled=bool(problems), use_container_width=True)
if problems:
    st.caption(" · ".join(problems))

if run_clicked:
    st.session_state.pop("application_run", None)
    try:
        with st.spinner("Preparing inputs, checking eligibility, and generating the forecast…"):
            datasets = None
            data_paths = {}
            if mode != "fixture":
                datasets = {}
                for turbine in turbines:
                    raw = (
                        uploads[turbine].getvalue() if input_choice == "upload"
                        else local_paths[turbine].read_bytes()
                    )
                    datasets[turbine] = prepare_dataset(
                        raw, turbine, source_timezone, interval_label, reporting_delay,
                    )
                    if input_choice == "local":
                        data_paths[turbine] = str(local_paths[turbine])
            if weather_source == "bundles":
                if weather_upload is not None:
                    weather_path = uploaded_weather_path(weather_upload.getvalue())
                elif not Path(weather_path).is_absolute():
                    weather_path = str(ROOT / weather_path)
            config = RunConfig(
                issue_time=datetime(
                    issue_date.year, issue_date.month, issue_date.day, issue_hour, tzinfo=timezone.utc,
                ),
                turbine_ids=tuple(turbines),
                horizon_hours=horizon,
                mode=mode,
                data_paths=data_paths,
                source_timezone=source_timezone,
                interval_label=interval_label,
                reporting_delay_minutes=reporting_delay,
                assumptions_confirmed=assumptions_acknowledged,
                observation_policy=observation_policy,
                weather_source=weather_source,
                weather_path=weather_path,
                wind_height_m=wind_height,
                model_factory=runtime["model_factory"] if model_choice == "team" else "",
                model_path=runtime["model_path"] if model_choice == "team" else "",
                model_metadata_path=runtime["model_metadata_path"] if model_choice == "team" else "",
                controller=controller,
                output_root=str(ROOT / "runs" / "application"),
                cache_dir=str(ROOT / "data" / "cache" / "weather"),
            )
            st.session_state["application_run"] = run_forecast(config, datasets=datasets)
    except Exception as exc:
        st.error(f"The run could not start: {safe_error(exc, runtime)}")

run = st.session_state.get("application_run")
if run is None:
    if not problems:
        st.info("Your request is ready. Generate a forecast to inspect the hourly result and its provenance.")
    with st.expander("What does the application return?"):
        st.markdown(
            "One prediction per turbine and forecast hour, plus a saved report, data provenance, "
            "and a workflow trace. Predictions use normalized active power. "
            "Every recalculation creates a separate run folder."
        )
        st.markdown("For batch historical replay and the Python interface, see the application guide.")
        guide = ROOT / "docs" / "application_io.md"
        if guide.exists():
            st.download_button("Download the application guide", guide.read_bytes(), file_name=guide.name)
    st.stop()

st.subheader("Latest run")
st.caption(
    f"Issue {run.request.issue_time:%Y-%m-%d %H:%M} UTC · "
    f"{run.request.horizon_hours} hours · {', '.join(run.request.turbine_ids)} · "
    f"{run.request.mode}"
)
if run.state == "completed":
    st.success("Forecast completed and saved.")
elif run.state == "blocked":
    st.warning(f"Forecast blocked: {safe_error(run.error or 'Input requirements were not met.', runtime)}")
else:
    st.error(f"Forecast failed: {safe_error(run.error or 'See the run report for details.', runtime)}")

result = run.result
if result is not None and result.is_synthetic:
    st.warning("This saved run is synthetic. Its predictions are demonstration output.")
metrics = st.columns(4)
metrics[0].metric("Run state", run.state.capitalize())
metrics[1].metric("Forecast hours", run.request.horizon_hours)
metrics[2].metric("Turbines", len(run.request.turbine_ids))
metrics[3].metric("Prediction rows", len(result.rows) if result is not None else 0)

forecast_tab, report_tab, provenance_tab, trace_tab = st.tabs(
    ["Forecast", "Report", "Data & provenance", "Workflow trace"]
)
with forecast_tab:
    if result is None or not result.rows:
        st.info("This run produced no forecast. Review the report and trace to resolve the issue.")
    else:
        frame = pd.DataFrame([
            {
                "turbine_id": row.turbine_id,
                "valid_time": row.valid_time,
                "issue_time": row.issue_time,
                "lead_hours": row.lead_hours,
                "prediction": row.prediction,
                "p10": row.p10,
                "p50": row.p50,
                "p90": row.p90,
            }
            for row in result.rows
        ]).sort_values(["valid_time", "turbine_id"])
        chart = frame[["valid_time", "turbine_id", "prediction"]].copy()
        chart["valid_time"] = pd.to_datetime(chart["valid_time"], utc=True).dt.tz_localize(None)
        st.markdown("**Normalized active power by turbine**")
        st.line_chart(chart, x="valid_time", y="prediction", color="turbine_id")
        st.caption(
            "Horizontal axis: forecast valid time in UTC. Each point is an hourly prediction. "
            "Values retain the source normalization; no conversion to MW or MWh is applied."
        )
        st.dataframe(frame, hide_index=True, use_container_width=True)
        for warning in result.warnings:
            st.caption(f"• {warning}")
with report_tab:
    if run.summary:
        st.markdown(run.summary)
    if run.report:
        with st.expander("Full structured report", expanded=run.state != "completed"):
            st.json(run.report)
with provenance_tab:
    st.markdown("**Request and result identity**")
    st.json({
        "request_id": run.request.request_id,
        "issue_time": run.request.issue_time.isoformat(),
        "mode": run.request.mode,
        "observation_policy": run.request.observation_policy,
        "model_id": result.model_id if result is not None else None,
        "input_hash": result.input_hash if result is not None else None,
        "synthetic": result.is_synthetic if result is not None else None,
    })
    if run.weather is not None:
        weather = run.weather
        st.markdown("**Weather evidence**")
        st.json({
            "bundle_id": weather.bundle_id,
            "provider": weather.provider,
            "weather_model": weather.weather_model,
            "run_init_time": weather.run_init_time.isoformat(),
            "available_at": weather.available_at.isoformat(),
            "retrieved_at": weather.retrieved_at.isoformat(),
            "provenance_status": weather.provenance_status,
            "availability_basis": weather.availability_basis,
            "source_hash": weather.source_hash,
            "row_count": len(weather.rows),
        })
    st.caption("The structured report includes measurement preparation and the recorded data assumptions.")
with trace_tab:
    if run.trace.events:
        st.json(run.trace.events)
    else:
        st.info("No workflow events were recorded.")

st.divider()
st.markdown("**Download this run**")
payload = run.to_dict()
downloads = st.columns(3)
forecast_path = run.run_dir / "forecast.csv"
if forecast_path.is_file():
    downloads[0].download_button(
        "Forecast CSV", forecast_path.read_bytes(),
        file_name=f"{run.request.request_id}-forecast.csv", mime="text/csv", use_container_width=True,
    )
else:
    downloads[0].caption("No forecast CSV for this run.")
downloads[1].download_button(
    "Run response JSON", json_bytes(payload),
    file_name=f"{run.request.request_id}-response.json", mime="application/json", use_container_width=True,
)
if run.run_dir.is_dir():
    downloads[2].download_button(
        "All run artifacts ZIP", artifact_zip(run, payload),
        file_name=f"{run.request.request_id}.zip", mime="application/zip", use_container_width=True,
    )
try:
    saved_path = run.run_dir.relative_to(ROOT)
except ValueError:
    saved_path = run.run_dir
st.caption(f"Saved run: {saved_path}")
