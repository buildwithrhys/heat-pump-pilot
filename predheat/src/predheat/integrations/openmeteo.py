"""Open-Meteo: hourly temperature and solar irradiance, forecast and the recent past. No key needed.

Used for the solar gain term of the house model (irradiance, W/m2) and as a
weather forecast fallback when the Home Assistant weather entity is missing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ..domain import Measurement, Provenance, Quality, Signal, WeatherPoint
from .base import ProviderHealth

URL = "https://api.open-meteo.com/v1/forecast"


class OpenMeteoProvider:
    name = "open_meteo"

    def __init__(self, latitude: float, longitude: float, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20.0):
        self.latitude, self.longitude = latitude, longitude
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._last_ok: datetime | None = None
        self._last_error: str | None = None

    async def _fetch(self, past_days: int = 1, forecast_days: int = 3) -> dict[str, Any]:
        r = await self._http.get(URL, params={"latitude": self.latitude, "longitude": self.longitude, "hourly": "temperature_2m,shortwave_radiation,cloud_cover,wind_speed_10m",
                                              "past_days": past_days, "forecast_days": forecast_days, "timezone": "UTC", "wind_speed_unit": "ms"})
        r.raise_for_status()
        self._last_ok = datetime.now(tz=timezone.utc)
        return r.json()

    async def forecast(self, hours: int = 48) -> list[WeatherPoint]:
        now = datetime.now(tz=timezone.utc)
        doc = await self._fetch(past_days=0, forecast_days=3)
        h = doc["hourly"]
        out = []
        for t, temp, cloud, wind in zip(h["time"], h["temperature_2m"], h.get("cloud_cover", []) or [None] * len(h["time"]), h.get("wind_speed_10m", []) or [None] * len(h["time"])):
            at = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
            if at < now or len(out) >= hours:
                continue
            out.append(WeatherPoint(issued_at=now, valid_at=at, temperature_c=float(temp), cloud_cover_pct=cloud, wind_speed_ms=wind, provenance=Provenance.OPEN_METEO))
        return out

    async def observation(self) -> WeatherPoint | None:
        return None

    async def irradiance_history(self, past_days: int = 1) -> list[Measurement]:
        """Hourly shortwave radiation (W/m2) for the recent past, as SOLAR_IRRADIANCE measurements."""
        now = datetime.now(tz=timezone.utc)
        doc = await self._fetch(past_days=past_days, forecast_days=1)
        h = doc["hourly"]
        out = []
        for t, ghi in zip(h["time"], h.get("shortwave_radiation", [])):
            if ghi is None:
                continue
            at = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
            if at > now:
                continue
            out.append(Measurement(signal=Signal.SOLAR_IRRADIANCE, ts=at, value=float(ghi), quality=Quality.MODEL_DERIVED, provenance=Provenance.OPEN_METEO, source_ref="open-meteo:shortwave_radiation"))
        return out

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, self._last_error is None, self._last_ok, self._last_error)
