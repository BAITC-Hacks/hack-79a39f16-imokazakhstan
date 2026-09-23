"""Minimal Streamlit view for the offline synthetic fixture."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from wind_forecast.agent.runner import ForecastWorkflow
from wind_forecast.contracts import ForecastRequest
from wind_forecast.demo_data import sample_observations
from wind_forecast.models.power_curve import EmpiricalPowerCurve
from wind_forecast.weather.mock import MockWeatherProvider

st.set_page_config(page_title="HackAlem Wind Forecast", layout="wide")
st.title("Agentic wind-power forecasting")
st.warning("Fixture mode: inputs and predictions are synthetic; do not submit as real results.")

issue_time = datetime(2026, 1, 31, tzinfo=timezone.utc)
request = ForecastRequest("streamlit-fixture", issue_time, ("T1", "T2"), 48, "fixture")
observations = sample_observations()
predictor = EmpiricalPowerCurve().fit(observations)
workflow = ForecastWorkflow(MockWeatherProvider(), predictor)

if st.button("Run 48-hour fixture forecast", type="primary"):
    result, trace = workflow.run(request, observations)
    st.metric("Forecast rows", len(result.rows))
    st.caption(f"Model: {result.model_id} · Weather: {result.weather_bundle_id}")
    st.line_chart(
        {
            turbine: [row.prediction for row in result.rows if row.turbine_id == turbine]
            for turbine in request.turbine_ids
        }
    )
    st.dataframe(
        [
            {
                "turbine_id": row.turbine_id,
                "valid_time": row.valid_time.isoformat(),
                "lead_hours": row.lead_hours,
                "prediction": row.prediction,
            }
            for row in result.rows
        ],
        use_container_width=True,
    )
    st.subheader("Workflow trace")
    st.json(trace.events)
    st.download_button(
        "Download fixture CSV",
        data=(
            "turbine_id,issue_time,valid_time,lead_hours,prediction\n"
            + "\n".join(
                f"{row.turbine_id},{row.issue_time.isoformat()},{row.valid_time.isoformat()},"
                f"{row.lead_hours},{row.prediction}"
                for row in result.rows
            )
        ),
        file_name="synthetic_forecast.csv",
        mime="text/csv",
    )
