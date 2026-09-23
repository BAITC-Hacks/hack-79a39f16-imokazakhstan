from typing import Protocol

from wind_forecast.contracts import ForecastRequest, WeatherBundle


class WeatherProvider(Protocol):
    def fetch(self, request: ForecastRequest) -> WeatherBundle: ...
