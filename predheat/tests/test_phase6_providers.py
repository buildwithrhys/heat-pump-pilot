"""Octopus API client, Open-Meteo provider, scenarios and the annual forecast."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx

from predheat.config import DataMode, Settings
from predheat.db import session_scope
from predheat.integrations.openmeteo import OpenMeteoProvider
from predheat.pricing import DEFAULT_TEMPLATES, scenario_intervals
from predheat.pricing.climate import annual_degree_days, typical_year
from predheat.pricing.octopus_api import OctopusAPI, expand_to_half_hours
from predheat.service import build_platform

UTC = timezone.utc


def _octopus_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/v1/products/") and path.endswith("/standard-unit-rates/"):
        rows = [{"valid_from": "2026-10-20T00:00:00Z", "valid_to": "2026-10-20T04:00:00Z", "value_inc_vat": 7.0},
                {"valid_from": "2026-10-20T04:00:00Z", "valid_to": None, "value_inc_vat": 28.0}]
        return httpx.Response(200, json={"results": rows, "next": None})
    if path.endswith("/standing-charges/"):
        return httpx.Response(200, json={"results": [{"value_inc_vat": 52.3}]})
    if path.startswith("/v1/products/"):
        return httpx.Response(200, json={"results": [{"code": "AGILE-24-10-01", "direction": "IMPORT"}, {"code": "AGILE-23-12-06", "direction": "IMPORT"},
                                                     {"code": "COSY-22-12-08", "direction": "IMPORT"}], "next": None})
    return httpx.Response(404)


def test_octopus_api_rates_and_products():
    api = OctopusAPI(transport=httpx.MockTransport(_octopus_handler))
    assert asyncio.run(api.latest_product_code("AGILE")) == "AGILE-24-10-01"
    start, end = datetime(2026, 10, 20, tzinfo=UTC), datetime(2026, 10, 20, 6, tzinfo=UTC)
    rates = asyncio.run(api.unit_rates("AGILE-24-10-01", "K", start, end))
    assert len(rates) == 2 and rates[0].rate_p_per_kwh == 7.0 and rates[0].tariff_code == "E-1R-AGILE-24-10-01-K"
    half = expand_to_half_hours(rates, start, end)
    assert len(half) == 12 and sum(1 for h in half if h.rate_p_per_kwh == 7.0) == 8
    assert asyncio.run(api.standing_charge("AGILE-24-10-01", "K")) == 52.3


def _meteo_handler(request: httpx.Request) -> httpx.Response:
    base = datetime(2026, 10, 25, tzinfo=UTC)
    times = [(base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(48)]
    return httpx.Response(200, json={"hourly": {"time": times, "temperature_2m": [8.0 + (i % 24) / 4 for i in range(48)],
                                                 "shortwave_radiation": [max(0, 300 * ((i % 24) - 6) * (18 - (i % 24)) / 36) for i in range(48)],
                                                 "cloud_cover": [50] * 48, "wind_speed_10m": [3.0] * 48}})


def test_open_meteo_irradiance_and_forecast():
    prov = OpenMeteoProvider(51.5, -3.2, transport=httpx.MockTransport(_meteo_handler))
    hist = asyncio.run(prov.irradiance_history(1))
    assert hist and all(m.signal.value == "solar_irradiance" for m in hist) and max(m.value for m in hist) > 100
    fc = asyncio.run(prov.forecast(48))
    assert isinstance(fc, list)


def test_scenarios_add_ev_in_cheap_slots_and_shift_battery_from_dear_slots():
    go = next(t for t in DEFAULT_TEMPLATES if t.code == "INTELLI-VAR")
    day = datetime(2026, 1, 10, tzinfo=UTC)
    ints = [(day + timedelta(minutes=30 * i), day + timedelta(minutes=30 * (i + 1)), 0.5) for i in range(48)]
    ev = scenario_intervals(go, ints, ev_kwh_per_day=7.0)
    assert abs(sum(k for _, _, k in ev) - (24.0 + 7.0)) < 1e-6
    cheap_added = sum(k - 0.5 for (st, _e, k) in ev if go.rate_at(st) == 7.0)
    assert abs(cheap_added - 7.0) < 1e-6
    bat = scenario_intervals(go, ints, battery_kwh_per_day=5.0)
    assert abs(sum(k for _, _, k in bat) - 24.0) < 1e-6  # moved, not added
    assert sum(k for (st, _e, k) in bat if go.rate_at(st) == 7.0) > sum(k for (st, _e, k) in ints if go.rate_at(st) == 7.0)


def test_climate_and_annual_profile(tmp_path):
    year = typical_year(51.48)
    assert year["label"].startswith("south west") and 1500 < annual_degree_days(year) < 2600
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'a.sqlite'}", sim_days=14, sim_seed=4, data_dir=tmp_path)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    with session_scope(settings.database_url) as s:
        cmp_ = p.compare_tariffs(s, days=10, ev_kwh_per_day=5.0)
    assert cmp_["status"] == "ok" and cmp_["scenario"]["ev_kwh_per_day"] == 5.0
    assert cmp_["annual"]["status"] in ("ok", "waiting")
    if cmp_["annual"]["status"] == "ok":
        assert cmp_["annual"]["annual_kwh"] > 500
