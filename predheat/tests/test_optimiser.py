"""COP model and the model-based optimiser."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np

from predheat.config import DataMode, OperatingMode, Settings
from predheat.db import session_scope
from predheat.model import fit_cop_model, prior_cop_model
from predheat.optimiser import MPCInputs, build_mpc_plan, optimise, thermostat_baseline
from predheat.service import build_platform

UTC = timezone.utc


def test_cop_fit_recovers_a_linear_relationship():
    rng = np.random.default_rng(1)
    t = rng.uniform(-3, 14, 200)
    cop = 2.6 + 0.08 * t + rng.normal(0, 0.15, 200)
    elec = rng.uniform(0.5, 2.0, 200)
    heat = cop * elec
    model = fit_cop_model(t, elec, heat)
    assert abs(model.cop_at(7.0) - (2.6 + 0.56)) < 0.15
    assert 0.05 < model.b < 0.11
    assert model.confidence > 0.6 and model.n_samples == 200


def test_cop_prior_when_no_data():
    model = fit_cop_model([], [], [])
    assert model.cop_at(7.0) == prior_cop_model().cop_at(7.0)
    assert model.notes


def _inputs(now: datetime, cheap_until_h: int = 4) -> MPCInputs:
    starts = [now + timedelta(minutes=30 * i) for i in range(48)]
    rate = [7.0 if i < cheap_until_h * 2 else 30.0 for i in range(48)]
    return MPCInputs(starts=starts, rate_p=rate, t_out=[3.0] * 48, ua_w_per_k=180.0, capacity_kwh_per_k=6.0,
                     internal_gain_kw=0.3, cop_fn=lambda t: 3.0, hp_capacity_kw=7.0, t_in0=20.5, setpoint_c=21.0,
                     tolerance_c=1.0, dhw_tank_c=42.0, dhw_setpoint_c=50.0)


def test_optimiser_preheats_in_cheap_window_and_beats_thermostat():
    now = datetime(2026, 1, 10, 1, 0, tzinfo=UTC)
    inp = _inputs(now)
    res = optimise(inp)
    assert res["status"] == "model_based"
    temps = [s["t_in"] for s in res["slots"]]
    assert min(temps) >= 19.9  # stays inside the band (with grid rounding)
    assert max(temps[:8]) > 21.3  # warms up while it is cheap
    assert temps[-1] >= 20.9  # ends no colder than the setpoint
    assert res["cost_p"] < res["baseline_cost_p"]
    _, _, base_cost = thermostat_baseline(inp)
    assert abs(base_cost - res["baseline_cost_p"]) < 0.2


def test_mpc_plan_has_explained_actions_and_hot_water():
    now = datetime(2026, 1, 10, 1, 0, tzinfo=UTC)
    plan = build_mpc_plan(_inputs(now), now)
    kinds = {a["kind"] for a in plan["actions"]}
    assert "preheat_space" in kinds and "heat_hot_water" in kinds
    assert plan["cost"]["saving_p"] > 0
    assert len(plan["predicted"]) == 48
    assert all("t_in_baseline" in p for p in plan["predicted"])
    hw = next(a for a in plan["actions"] if a["kind"] == "heat_hot_water")
    assert hw["start"] < plan["predicted"][8]["start"]  # in the cheap window
    assert plan["next_action"]["kind"] != "none"


def test_platform_switches_to_model_based_plan_and_scores_accuracy(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, operating_mode=OperatingMode.SHADOW,
                        database_url=f"sqlite:///{tmp_path / 'o.sqlite'}", sim_days=10, sim_seed=4)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    with session_scope(settings.database_url) as s:
        plan = p.shadow_plan(s)
        hp = p.heat_pump_estimate(s)
    assert plan["status"] == "model_based", plan.get("assumptions")
    assert plan["cost"]["baseline_p"] > 0
    assert hp["status"] == "ok" and 2.0 < hp["cop_at_7"] < 5.0
    # walk the simulated clock forward a day in half-hour polls so the earlier plans can be scored
    for _ in range(50):
        p.clock.advance(timedelta(minutes=30))
        asyncio.run(p.poll_once())
    with session_scope(settings.database_url) as s:
        acc = p.forecast_accuracy(s)
    assert acc["status"] == "ok", acc
    assert acc["points"] > 20
    assert acc["rmse_baseline_c"] < 1.5
    ov = p.overview()
    assert ov["heat_pump"]["cop_at_7"] > 1.5
    assert ov["today"]["gas_equivalent"]["cost_pence"] >= 0
