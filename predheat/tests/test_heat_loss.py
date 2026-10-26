"""Heat loss estimate from stored history (simulated home has a known answer)."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from predheat.api import create_app
from predheat.config import DataMode, Settings
from predheat.db import session_scope
from predheat.db import models as m
from predheat.service import build_platform


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
    assert est["design_kw_at_minus_2"] > 0 and est["time_constant_h"] > 0
    assert est["samples"] >= 48
    ov = p.overview()
    assert ov["heat_loss"]["status"] == "ok"
    app = create_app(settings, platform=p, poll=False)
    with TestClient(app) as c:
        assert c.get("/api/heat-loss?days=5").json()["status"] in ("ok", "not_identified", "insufficient_data")


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
    assert est["status"] in ("insufficient_data", "not_identified", "ok")
    assert "reason" in est or est["status"] == "ok"
