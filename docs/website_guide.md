# Using the WindScope website

The page has two sections: **Set up your forecast** and **Read your forecast**.
You choose the inputs, click one button, and see a chart and downloads below.

## Try it immediately

Leave **Demo data** selected and click **Run demo forecast**. The default produces
24 hourly predictions for each of two turbines. Change **Predict next** to
48 hours for a longer horizon. Demo data is simulated and is labeled throughout.
No upload, API key, or internet is required after installation.

## Use actual measurements

1. Select **Your measurements**.
2. Open **Measurement files**. Upload the organizer's turbine 1 CSV into
   **Turbine 1** and turbine 2 CSV into **Turbine 2**. If project copies exist,
   the page uses them automatically; an upload replaces the corresponding copy
   for this run. Selecting one turbine requires only its file.
3. Choose **Forecast date (UTC)** and **Time (UTC)**. This is the issue time:
   the moment you are simulating making the forecast. Use **February 1, 2026,
   00:00 UTC** for the supplied case example.
4. Choose **24 or 48 hours** and the turbines.
5. Check the CSV timestamp assumptions shown above the button. Adjust them in
   **Optional settings** and tick **Use these timestamp assumptions**. UTC,
   interval starts, and zero delay are example defaults, not organizer-confirmed
   facts. Changing these settings requires acknowledging them again.
6. Click **Generate forecast** and wait for the weather retrieval and prediction.

The first automatic NOAA weather retrieval may take several minutes. The app
keeps a local cache to reduce later downloads. It never substitutes fabricated
weather when real weather is unavailable. If a run fails, the page explains the
reason and provides its report when the application created one.

## Understand the results

The forecast appears in **Read your forecast**, immediately below the controls.
The issue time and horizon printed above the chart identify the saved run.

**Summary cards** show the average predicted normalized power of each turbine
across the selected horizon, its peak, and the UTC hour ending at that peak.
These describe predictions; they are not accuracy measurements.

**The chart** uses blue for Turbine 1 and orange for Turbine 2. Different line
styles help distinguish overlapping predictions. Hover over a point to see the
predicted hourly interval and exact value. Both turbines share the same vertical
scale. Values outside the usual source range are kept visible rather than
clipped. A shaded 10th–90th percentile band appears only when the model supplies
those quantiles.

The horizontal axis is **UTC hour ending**. For example, a point at February 1,
01:00 predicts the interval from 00:00 to 01:00. The vertical axis is **normalized
active power**, in the units of the supplied CSV. Higher values mean greater
output. Conversion to MW requires the normalization definition and capacity.

Open **View hourly values** to see a compact table with one column per turbine.
There are 24 or 48 rows; a two-turbine CSV has 48 or 96 records because each
record represents one turbine and one hour.

Changing an input leaves the previous forecast visible with a notice. Click
**Generate forecast** again to calculate the new request. Each run is saved as
a separate version.

## Download and inspect

- **Download forecast CSV** saves the numerical output, including issue time,
  valid time, turbine ID, lead, prediction, and any model quantiles.
- **Download full report (ZIP)** saves the forecast and its request, report,
  weather evidence, model identity, summary, and workflow trace.
- **How this forecast was made** shows a short explanation of the model,
  eligible measurements, weather availability, data preparation, and limitations.
- **AI explanation** appears when optional OpenAI orchestration completes.

All run files are also saved under `runs/application/`. If actual targets exist
for a historical forecast window, diagnostic accuracy appears inside the details.
The supplied CSVs contain no February targets, so February forecasts cannot yet
be scored. Demo metrics, if available, are synthetic diagnostics only.

## Optional settings

Most reviewers can leave this panel closed after reviewing the timestamp assumptions.

| Setting | When to change it |
|---|---|
| CSV timezone, interval label, reporting delay | Match the organizer's export and measurement availability |
| Weather input | Upload a complete `weather.json` instead of using the NOAA archive |
| Forecast wind height | Match the weather feature used by the numerical model |
| Prediction model | Choose a configured teammate model or the empirical baseline |
| Measurement history | Permit later observations only when they genuinely existed at the issue time |
| Use OpenAI agent | Request API orchestration and an explanation when the host has configured credentials |

The host configures model artifacts and API keys in `.env` or environment
variables. Model and AI options become usable when the required configuration
exists. The local controller makes no OpenAI calls; automatic weather retrieval
still needs internet. A matching local weather JSON allows an offline real-data
run. The numerical model always calculates the power values.

Live mode, batch replay, custom Python providers, and detailed configuration
remain available through the [Python/CLI guide](application_io.md). The website
focuses on one forecast at a time. For setup commands, see the [README](../README.md).
