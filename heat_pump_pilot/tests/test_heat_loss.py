"""Heat loss estimate from stored history (simulated home has a known answer)."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from heat_pump_pilot.api import create_app
from heat_pump_pilot.config import DataMode, Settings
from heat_pump_pilot.db import session_scope
from heat_pump_pilot.db import models as m
from heat_pump_pilot.service import build_platform


def test_heat_loss_recovers_simulator_ground_truth(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'h.sqlite'}", sim_days=10, sim_seed=3)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    with session_scope(settings.database_url) as s:
        est = p.thermal_estimate(s, days=10)
    assert est["status"] == "ok", est
    truth = p.simulated_home.ground_truth()["ua_w_per_k"]
    assert abs(est["heat_loss_w_per_k"] - truth) / truth < 0.2
    assert est["ci_low_w_per_k"] < est["heat_loss_w_per_k"] < est["ci_high_w_per_k"]
    assert est["design_kw"] > 0 and est["design_outdoor_c"] <= 0 and est["cooling_rate_c_per_h_at_5c"] is not None
    assert est["samples"] >= 48 and est["days_with_data"] >= 7
    ov = p.overview()
    assert ov["heat_loss"]["status"] == "ok"
    app = create_app(settings, platform=p, poll=False)
    with TestClient(app) as c:
        assert c.get("/api/heat-loss?days=5").json()["status"] in ("ok", "waiting")


def test_building_model_is_persisted_once_per_window(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'b.sqlite'}", sim_days=6, sim_seed=3)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    r1 = asyncio.run(p.poll_once())
    r2 = asyncio.run(p.poll_once())
    assert r1["building_model_id"] == r2["building_model_id"]
    with session_scope(settings.database_url) as s:
        assert s.query(m.BuildingModelVersion).count() == 1


def test_insufficient_data_is_reported_not_raised(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'i.sqlite'}", sim_days=2, sim_seed=3)
    p = build_platform(settings)
    asyncio.run(p.bootstrap(0))
    with session_scope(settings.database_url) as s:
        est = p.thermal_estimate(s, days=14)
    assert est["status"] in ("waiting", "ok")
    if est["status"] == "waiting":
        assert "So far:" in est["reason"] and "heat_loss_w_per_k" not in est and "confidence" not in est["reason"]
