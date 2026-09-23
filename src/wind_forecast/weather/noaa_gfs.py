"""Person 1: implement a provenance-aware adapter for an approved original GFS archive."""

from wind_forecast.contracts import ForecastRequest, WeatherBundle


class NoaaGfsProvider:
    def fetch(self, request: ForecastRequest) -> WeatherBundle:
        raise NotImplementedError(
            "Implement after verifying source coverage, run initialization, public release time, "
            "coordinates, variables, and archive access. Never substitute reanalysis."
        )
