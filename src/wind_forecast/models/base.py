from typing import Protocol

from wind_forecast.contracts import ForecastRequest, ForecastResult, Observation, WeatherBundle


class Predictor(Protocol):
    model_id: str

    def predict(
        self,
        request: ForecastRequest,
        observations: list[Observation],
        weather: WeatherBundle,
    ) -> ForecastResult: ...
