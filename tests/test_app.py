"""UI regression checks for complete forecasts, stale results, and input guards."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from wind_forecast.agent.application import run_forecast

ROOT = Path(__file__).resolve().parents[1]


class ForecastPageTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = temporary.name
        wrapper = lambda config, **kwargs: run_forecast(
            replace(config, output_root=self.output), **kwargs
        )
        self.patch = patch("wind_forecast.agent.application.run_forecast", side_effect=wrapper)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.page = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        self.assertFalse(self.page.exception)

    def test_demo_48_hours_downloads_and_stale_result_notice(self):
        self.page.selectbox(key="horizon").select(48).run()
        self.page.button(key="generate").click().run()
        self.assertFalse(self.page.exception)
        run = self.page.session_state["forecast_run"]
        self.assertEqual(run.state, "completed")
        self.assertEqual(len(run.result.rows), 96)
        self.assertTrue(run.result.is_synthetic)
        self.assertEqual(len(self.page.dataframe[0].value), 48)
        self.assertTrue((run.run_dir / "forecast.csv").is_file())
        self.page.selectbox(key="horizon").select(24).run()
        self.assertFalse(self.page.exception)
        self.assertTrue(any("previous request" in w.value for w in self.page.warning))
        self.assertEqual(self.page.session_state["forecast_run"].request.horizon_hours, 48)

    def test_single_turbine_demo(self):
        self.page.selectbox(key="turbines").select("Turbine 2").run()
        self.page.button(key="generate").click().run()
        self.assertFalse(self.page.exception)
        result = self.page.session_state["forecast_run"].result
        self.assertEqual(len(result.rows), 24)
        self.assertEqual({r.turbine_id for r in result.rows}, {"T2"})
        self.assertEqual(
            list(self.page.dataframe[0].value.columns), ["Hour ending (UTC)", "Turbine 2"]
        )

    def test_real_inputs_require_acknowledgement_and_weather_upload(self):
        self.page.radio(key="data_source").set_value("Your measurements").run()
        self.assertFalse(self.page.exception)
        self.assertTrue(self.page.button(key="generate").disabled)
        ack = next(c for c in self.page.checkbox if c.label == "Use these timestamp assumptions")
        ack.check().run()
        self.page.selectbox(key="model_choice").select("Gradient boosting ML").run()
        self.page.selectbox(key="weather_choice").select("Upload weather file").run()
        self.assertFalse(self.page.exception)
        self.assertTrue(self.page.button(key="generate").disabled)
        self.assertTrue(any("Add a weather JSON" in c.value for c in self.page.caption))
        self.page.selectbox(key="source_zone").select("UTC").run()
        ack = next(c for c in self.page.checkbox if c.label == "Use these timestamp assumptions")
        self.assertFalse(ack.value)


if __name__ == "__main__":
    main()
