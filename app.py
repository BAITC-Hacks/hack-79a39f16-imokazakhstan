"""Streamlit view for the offline synthetic wind-forecast fixture."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from wind_forecast.agent.fixture import run_fixture
from wind_forecast.contracts import ForecastRequest


st.set_page_config(page_title="HackAlem Wind Forecast", layout="wide")
st.title("Wind-power forecast")
st.warning("Synthetic fixture: inputs and predictions are fabricated and are not scored results.")

with st.sidebar:
    st.header("Forecast request")
    mode = st.radio("Mode", ("Fixture", "Historical"), index=0)
    issue_date = st.date_input(
        "Issue date (UTC)",
        value=date(2026, 1, 31),
        min_value=date(2026, 1, 2),
        max_value=date(2026, 2, 28),
    )
    issue_hour = st.selectbox(
        "Issue hour (UTC)", range(24), index=0, format_func=lambda hour: f"{hour:02d}:00"
    )
    turbine_ids = st.multiselect("Turbines", ("T1", "T2"), default=("T1", "T2"))
    horizon_hours = st.selectbox(
        "Forecast horizon", (48, 24), format_func=lambda hours: f"{hours} hours"
    )

if mode == "Historical":
    st.info(
        "Historical mode needs verified archived weather and turbine observations from Person 1 "
        "and a fitted model from Person 2. It is not available in this fixture demo."
    )
    st.stop()

if not turbine_ids:
    st.info("Select at least one turbine to run the fixture forecast.")
    st.stop()

if st.button("Run synthetic forecast", type="primary"):
    st.session_state.pop("fixture_run", None)
    issue_time = datetime(
        issue_date.year,
        issue_date.month,
        issue_date.day,
        issue_hour,
        tzinfo=timezone.utc,
    )
    request = ForecastRequest(
        request_id=f"fixture-{issue_time:%Y%m%dT%H%MZ}-{uuid4().hex[:8]}",
        issue_time=issue_time,
        turbine_ids=tuple(turbine_ids),
        horizon_hours=horizon_hours,
        mode="fixture",
    )
    try:
        st.session_state["fixture_run"] = run_fixture(request, ROOT / "runs" / "fixture")
    except Exception as exc:
        st.error(f"The fixture forecast could not complete: {exc}")
        st.stop()

run = st.session_state.get("fixture_run")
if run is None:
    st.info("Choose a request in the sidebar, then run the synthetic forecast.")
    st.stop()

result = run.workflow.result
weather = run.workflow.weather
st.subheader("Latest completed fixture run")
st.caption(
    f"Issue: {run.request.issue_time.isoformat()} · "
    f"Turbines: {', '.join(run.request.turbine_ids)} · "
    f"Run ID: {run.request.request_id}"
)
st.caption(f"Saved in {run.artifacts.run_dir.relative_to(ROOT)}")

metric_columns = st.columns(3)
metric_columns[0].metric("Forecast rows", len(result.rows))
metric_columns[1].metric("Turbines", len(run.request.turbine_ids))
metric_columns[2].metric("Hours ahead", run.request.horizon_hours)

st.subheader("Power forecast")
st.line_chart(
    {
        turbine_id: [row.prediction for row in result.rows if row.turbine_id == turbine_id]
        for turbine_id in run.request.turbine_ids
    }
)
st.dataframe(
    [
        {
            "turbine_id": row.turbine_id,
            "issue_time": row.issue_time.isoformat(),
            "valid_time": row.valid_time.isoformat(),
            "lead_hours": row.lead_hours,
            "prediction": row.prediction,
        }
        for row in result.rows
    ],
)

st.subheader("Input provenance")
st.json(
    {
        "mode": run.request.mode,
        "synthetic": result.is_synthetic,
        "model_id": result.model_id,
        "observation_count": run.observation_count,
        "latest_observation_available_at": run.latest_observation_at.isoformat(),
        "weather_bundle_id": weather.bundle_id,
        "weather_provider": weather.provider,
        "weather_model": weather.weather_model,
        "weather_provenance": weather.provenance_status,
        "weather_run_init_time": weather.run_init_time.isoformat(),
        "weather_available_at": weather.available_at.isoformat(),
        "weather_availability_basis": weather.availability_basis,
        "weather_source": weather.source_uri,
        "warnings": result.warnings,
    }
)

st.subheader("Workflow trace")
st.dataframe(run.workflow.trace.events)

download_columns = st.columns(3)
download_columns[0].download_button(
    "Download forecast CSV",
    data=run.artifacts.forecast_csv.read_bytes(),
    file_name=f"{run.artifacts.run_dir.name}-forecast.csv",
    mime="text/csv",
)
download_columns[1].download_button(
    "Download manifest JSON",
    data=run.artifacts.manifest_json.read_bytes(),
    file_name=f"{run.artifacts.run_dir.name}-manifest.json",
    mime="application/json",
)
download_columns[2].download_button(
    "Download trace JSONL",
    data=run.artifacts.trace_jsonl.read_bytes(),
    file_name=f"{run.artifacts.run_dir.name}-trace.jsonl",
    mime="application/x-ndjson",
)
