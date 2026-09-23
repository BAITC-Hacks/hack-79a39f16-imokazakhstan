"""Presentation helpers; charts preserve forecast values and UTC hour-end timestamps."""

from __future__ import annotations

import io
import zipfile
from datetime import timedelta

import altair as alt
import pandas as pd

TURBINE_NAMES = {"T1": "Turbine 1", "T2": "Turbine 2"}
COLORS = {"T1": "#2364c4", "T2": "#ca6815"}


def forecast_frame(result):
    return pd.DataFrame(
        [
            {
                "turbine_id": row.turbine_id,
                "turbine": TURBINE_NAMES.get(row.turbine_id, row.turbine_id),
                "valid_time": row.valid_time,
                "hour_ending": row.valid_time.strftime("%d %b %Y, %H:%M UTC"),
                "interval": f"{row.valid_time - timedelta(hours=1):%d %b %H:%M}–{row.valid_time:%H:%M} UTC",
                "prediction": row.prediction,
                "p10": row.p10,
                "p90": row.p90,
            }
            for row in result.rows
        ]
    ).sort_values(["valid_time", "turbine_id"])


def forecast_chart(frame, horizon_hours):
    ids = sorted(frame.turbine_id.unique())
    names = [TURBINE_NAMES.get(t, t) for t in ids]
    colors = [COLORS.get(t, "#53756e") for t in ids]
    values = frame[["prediction", "p10", "p90"]].stack().dropna()
    low, high = min(0.0, float(values.min())), max(1.0, float(values.max()))
    base = alt.Chart(frame).encode(
        x=alt.X(
            "valid_time:T",
            title="Hour ending (UTC)",
            scale=alt.Scale(type="utc"),
            axis=alt.Axis(
                labelExpr="utcFormat(datum.value, '%d %b %H:%M')",
                tickCount=7 if horizon_hours == 24 else 9,
                labelAngle=0,
                labelOverlap=True,
                grid=False,
            ),
        ),
        color=alt.Color(
            "turbine:N",
            title=None,
            scale=alt.Scale(domain=names, range=colors),
            legend=alt.Legend(orient="top", symbolStrokeWidth=3),
        ),
    )
    line = base.mark_line(strokeWidth=2.8, point={"filled": True, "size": 30}).encode(
        y=alt.Y(
            "prediction:Q",
            title="Normalized active power",
            scale=alt.Scale(domain=[low, high], nice=False),
            axis=alt.Axis(format=".1f", tickCount=6),
        ),
        strokeDash=alt.StrokeDash(
            "turbine:N",
            legend=None,
            scale=alt.Scale(domain=names, range=[[1, 0], [6, 3]][: len(ids)]),
        ),
        tooltip=[
            alt.Tooltip("turbine:N", title="Turbine"),
            alt.Tooltip("interval:N", title="Predicted hour"),
            alt.Tooltip("prediction:Q", title="Normalized power", format=".3f"),
        ],
    )
    quantiles = frame.dropna(subset=["p10", "p90"])
    chart = line
    if not quantiles.empty:
        band = base.mark_area(opacity=0.12).encode(
            y=alt.Y("p10:Q", title="Normalized active power"), y2="p90:Q"
        )
        chart = band + line
    return (
        chart.properties(height=340)
        .configure_view(strokeWidth=0)
        .configure_axis(
            labelFontSize=11,
            titleFontSize=12,
            titlePadding=14,
            gridColor="#e5eaf1",
            domainColor="#d7dfe8",
        )
        .configure_legend(labelFontSize=12, padding=8)
    )


def hourly_table(frame):
    table = frame.pivot(index="valid_time", columns="turbine", values="prediction")
    table.index = table.index.strftime("%d %b %Y, %H:%M UTC")
    table.index.name = "Hour ending (UTC)"
    return table.reset_index()


def artifact_zip(run):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(run.run_dir.iterdir()):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix in {".csv", ".json", ".jsonl", ".md"}
            ):
                archive.writestr(path.name, path.read_bytes())
    return buffer.getvalue()
