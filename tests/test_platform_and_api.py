"""End-to-end in simulated mode: bootstrap, persist, report, serve."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from predheat.api import create_app
from predheat.config import DataMode, Settings
from predheat.service import build_platform


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "sim.sqlite"
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{db}", sim_days=4, sim_seed=11)
    p = build_platform(settings)
    import asyncio

    asyncio.run(p.bootstrap())
    return p


def test_bootstrap_persists_history_and_tariff(platform):
    assert platform.property_id is not None
    assert platform.last_poll.get("tariff_code", "").startswith("E-1R-INTELLI")
    health = platform.system_health([])
    assert health["record_counts"]["measurements"] > 500
    assert health["record_counts"]["rate_intervals"] >= 48 * 3
    assert health["missing_for_thermal_model"] == []
    assert health["control_enabled"] is False


def test_costs_reconcile_and_are_labelled_simulated(platform):
    today = platform.clock.now().date()
    from datetime import timedelta

    c = platform.energy_costs(today - timedelta(days=2), today)
    assert c["reconciliation"]["reconciled"]
    assert c["space_heating"]["kwh_electric"] > 0
    assert c["space_heating"]["quality"] == "simulated"
    assert c["space_heating"]["cop"] and 2.0 < c["space_heating"]["cop"] < 5.5
    assert c["heat_pump_total"]["cost_pence"] >= c["space_heating"]["cost_pence"]
    assert c["standing_charge_pence"] > 0


def test_timeline_has_prices_weather_and_solar(platform):
    t = platform.timeline(48)
    assert len(t["slots"]) == 96
    assert t["slot_summary"]["guaranteed_off_peak"] == 24
    assert len(t["weather"]) == 48
    assert len(t["solar"]) == 96
    assert t["optimisation"]["status"] == "not_available"


def test_api_endpoints(platform):
    app = create_app(platform.settings, platform=platform, poll=False)
    with TestClient(app) as c:
        assert c.get("/api/health").json()["operating_mode"] == "monitor"
        ov = c.get("/api/overview").json()
        assert ov["indoor_temperature"]["value"] is not None
        assert ov["today"]["reconciliation"]["reconciled"]
        assert c.get("/api/system").json()["providers"]
        assert c.get("/api/costs?days=3").json()["daily"]
        assert c.get("/api/discovery").json()["capabilities"]
        assert c.get("/api/ground-truth").json()["ua_w_per_k"] == 180.0
        r = c.post("/api/control/setpoint")
        assert r.status_code == 403
