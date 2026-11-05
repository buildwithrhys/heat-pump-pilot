"""The weather entity is found, not assumed to be called weather.weather_forecast."""

from __future__ import annotations

import pytest

from heat_pump_pilot.integrations.homeassistant.client import HAState
from heat_pump_pilot.integrations.homeassistant.providers import HAWeatherProvider, pick_weather_entity


def _w(eid: str, features: int = 3, state: str = "cloudy") -> HAState:
    return HAState(entity_id=eid, state=state, attributes={"temperature": 7.5, "supported_features": features})


class WeatherClient:
    def __init__(self, states):
        self._states = states
        self.asked: list[str] = []

    async def states(self):
        return list(self._states)

    async def weather_forecast(self, entity_id, kind="hourly"):
        self.asked.append(entity_id)
        if not any(s.entity_id == entity_id for s in self._states):
            raise RuntimeError("HA ws error: Service call requested response data but did not match any entities")
        return [{"datetime": "2026-11-05T12:00:00+00:00", "temperature": 7.0, "wind_speed": 10, "wind_speed_unit": "km/h"}]


def test_pilot_name_still_preferred(pilot_states):
    assert pick_weather_entity(pilot_states) == "weather.weather_forecast"


def test_any_weather_entity_with_hourly_forecasts_is_used():
    states = [_w("weather.forecast_home", features=1), _w("weather.openweathermap", features=3), _w("sensor.openweathermap_weather")]
    assert pick_weather_entity(states) == "weather.openweathermap"  # hourly beats daily-only; sensors never count


def test_unavailable_entity_is_last_resort():
    assert pick_weather_entity([_w("weather.a", state="unavailable"), _w("weather.b", features=1)]) == "weather.b"


def test_explicit_choice_wins_when_it_exists():
    states = [_w("weather.openweathermap"), _w("weather.forecast_home")]
    assert pick_weather_entity(states, "weather.forecast_home") == "weather.forecast_home"
    assert pick_weather_entity(states, "weather.gone") == "weather.forecast_home"  # sorted by name among equals


async def test_forecast_uses_the_entity_that_exists():
    client = WeatherClient([_w("weather.openweathermap")])
    prov = HAWeatherProvider(client)
    pts = await prov.forecast(48)
    assert client.asked == ["weather.openweathermap"]
    assert len(pts) == 1 and pts[0].wind_speed_ms == pytest.approx(10 / 3.6)
    assert (await prov.observation()).temperature_c == 7.5
    assert (await prov.health()).ok


async def test_no_weather_entity_says_what_to_do():
    prov = HAWeatherProvider(WeatherClient([]))
    with pytest.raises(RuntimeError, match="No weather entity"):
        await prov.forecast(48)
    health = await prov.health()
    assert not health.ok and "weather integration" in health.last_error
