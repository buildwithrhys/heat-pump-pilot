"""48-hour horizon, cost backtest, hot water control, trial days."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from predheat.config import DataMode, OperatingMode, Settings
from predheat.control import Controller
from predheat.db import session_scope
from predheat.integrations.homeassistant.control import RecordingControlAdapter
from predheat.optimiser import MPCInputs, build_mpc_plan
from predheat.service import build_platform

UTC = timezone.utc


def test_plan_runs_to_48_hours_when_prices_allow():
    now = datetime(2026, 1, 10, 1, 0, tzinfo=UTC)
    starts = [now + timedelta(minutes=30 * i) for i in range(96)]
    inp = MPCInputs(starts=starts, rate_p=[7.0 if (i % 48) < 12 else 30.0 for i in range(96)], t_out=[3.0] * 96, ua_w_per_k=180.0,
                    capacity_kwh_per_k=6.0, internal_gain_kw=0.3, cop_fn=lambda t: 3.0, hp_capacity_kw=7.0, t_in0=20.5, setpoint_c=21.0)
    plan = build_mpc_plan(inp, now)
    assert plan["horizon_hours"] == 48 and len(plan["predicted"]) == 96
    assert all("cost_p_baseline" in p for p in plan["predicted"])
    assert "48 hours" in plan["reason"]


def test_platform_plan_uses_up_to_48_hours_and_backtests(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, operating_mode=OperatingMode.SHADOW, database_url=f"sqlite:///{tmp_path / 'b.sqlite'}",
                        sim_days=10, sim_seed=4, data_dir=tmp_path)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    with session_scope(settings.database_url) as s:
        plan = p.shadow_plan(s)
    assert plan["status"] == "model_based" and 48 < len(plan["predicted"]) <= 96
    for _ in range(50):
        p.clock.advance(timedelta(minutes=30))
        asyncio.run(p.poll_once())
    with session_scope(settings.database_url) as s:
        acc = p.forecast_accuracy(s)
    assert acc["status"] == "ok" and acc["cost_backtest"]["status"] == "ok"
    assert acc["cost_backtest"]["slots"] > 20 and acc["cost_backtest"]["actual_pence"] > 0


def _ctrl(tmp_path, **kw):
    settings = Settings(data_mode=DataMode.SIMULATED, operating_mode=OperatingMode.BALANCED, control_enabled=True, control_climate_entity="climate.zone",
                        database_url=f"sqlite:///{tmp_path / 'c.sqlite'}", sim_days=2, data_dir=tmp_path, **kw)
    p = build_platform(settings)
    asyncio.run(p.bootstrap(0))
    adapter = RecordingControlAdapter(initial=21.0)
    adapter.targets["water_heater.cyl"] = 50.0
    return settings, Controller(settings, adapter, property_id=p.property_id), adapter


def test_hot_water_target_raised_during_planned_reheat_and_restored(tmp_path):
    settings, c, adapter = _ctrl(tmp_path, control_dhw_entity="water_heater.cyl")
    now = datetime(2026, 1, 10, 4, 0, tzinfo=UTC)
    plan = {"status": "model_based", "model": {"setpoint_c": 21.0, "tolerance_c": 1.0},
            "predicted": [{"start": (now - timedelta(minutes=5)).isoformat(), "mode": "hold"}],
            "actions": [{"kind": "heat_hot_water", "start": (now - timedelta(minutes=5)).isoformat(), "end": (now + timedelta(minutes=55)).isoformat()}]}
    with session_scope(settings.database_url) as s:
        asyncio.run(c.step(s, now, plan, 20.8, timedelta(minutes=5), 0.8))
    assert ("water_heater.cyl", 55.0) in adapter.calls and c.dhw_holding
    later = now + timedelta(hours=1)
    plan2 = {**plan, "predicted": [{"start": (later - timedelta(minutes=5)).isoformat(), "mode": "hold"}]}
    with session_scope(settings.database_url) as s:
        asyncio.run(c.step(s, later, plan2, 20.8, timedelta(minutes=5), 0.8))
    assert adapter.calls[-1] == ("water_heater.cyl", 50.0) and not c.dhw_holding


def test_trial_mode_baseline_days_do_nothing(tmp_path):
    settings, c, adapter = _ctrl(tmp_path, trial_mode=True)
    baseline_day = next(d for d in range(1, 10) if Controller.trial_day_is_baseline(datetime(2026, 1, d, 12, tzinfo=UTC)))
    now = datetime(2026, 1, baseline_day, 12, 0, tzinfo=UTC)
    plan = {"status": "model_based", "model": {"setpoint_c": 21.0, "tolerance_c": 1.0}, "predicted": [{"start": (now - timedelta(minutes=5)).isoformat(), "mode": "boost"}]}
    with session_scope(settings.database_url) as s:
        d = asyncio.run(c.step(s, now, plan, 20.8, timedelta(minutes=5), 0.8))
    assert "trial" in d.reason and not adapter.calls
    opt_day = baseline_day + 1
    now2 = datetime(2026, 1, opt_day, 12, 0, tzinfo=UTC)
    plan2 = {**plan, "predicted": [{"start": (now2 - timedelta(minutes=5)).isoformat(), "mode": "boost"}]}
    with session_scope(settings.database_url) as s:
        d2 = asyncio.run(c.step(s, now2, plan2, 20.8, timedelta(minutes=5), 0.8))
    assert d2.acted


def test_trial_report_groups_days(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 't.sqlite'}", sim_days=16, sim_seed=4, data_dir=tmp_path)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    with session_scope(settings.database_url) as s:
        r = p.trial_report(s, days=14)
    assert r["baseline"]["n"] >= 3 and r["optimised"]["n"] >= 3
    assert all(d["degree_days"] > 0 for d in r["days"])
