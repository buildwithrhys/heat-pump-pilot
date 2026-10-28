"""Heating curve check: the analysis, and the user-confirmed change and undo from the page."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from heat_pump_pilot.config import DataMode, Settings
from heat_pump_pilot.db import models as m
from heat_pump_pilot.db import session_scope
from heat_pump_pilot.integrations.homeassistant.client import HAState
from heat_pump_pilot.model.curve import HourRow, analyse_curve
from heat_pump_pilot.service import build_platform

UTC = timezone.utc
UA, GAIN, K, CURVE = 250.0, 0.3, 0.12, 0.8


def _hours(t_in_offset=lambda t: 0.0, op=None, n=14 * 24):
    """A house that loses 250 W/K with 0.3 kW of internal gains, radiators of K = 0.12 kW/K^1.3,
    and a controller asking for flow = 21 + 0.8 * 1.6 * (21 - outdoor). The hourly mean flow is
    what the radiators actually needed (the heat pump cycles below its target)."""
    rows = []
    for i in range(n):
        t_out = -2.0 + 14.0 * ((i * 7) % 97) / 96.0
        q = UA / 1000.0 * (21.0 - t_out) - GAIN
        flow = 21.0 + (q / K) ** (1 / 1.3) + 2.5
        rows.append(HourRow(t_out=t_out, t_in=21.0 + t_in_offset(t_out), t_set=21.0, flow=flow, flow_set=21.0 + CURVE * 1.6 * (21.0 - t_out),
                            ret=flow - 5.0, heat_kwh=q, dhw_kwh=0.0, elec_kwh=q / 3.5, op_hours=op))
    return rows


def test_curve_too_high_is_found_and_a_lower_curve_suggested():
    r = analyse_curve(_hours(), UA, GAIN, CURVE)
    assert r["status"] == "ok"
    assert abs(r["emitter"]["k_kw_per_k13"] - K) / K < 0.05
    assert r["direction"] == "lower" and r["suggested_curve"] < CURVE
    assert r["saving_pct"] > 0
    curve = next(x for x in r["settings"] if x["key"] == "heating_curve")
    assert curve["now"] == CURVE and curve["suggested"] == r["suggested_curve"]
    assert all(row["flow_suggested"] < row["flow_now"] for row in r["bands"])
    assert r["coverage"]["cold_end_checked"]


def test_room_below_setpoint_with_heat_pump_flat_out_means_raise():
    rows = _hours(t_in_offset=lambda t: -1.2 if t < 4 else 0.0, op=0.95)
    r = analyse_curve(rows, UA, GAIN, CURVE)
    assert r["direction"] == "raise" and r["suggested_curve"] > CURVE
    assert any("not hot enough" in n for n in r["notes"])


def test_too_few_heating_hours_waits_and_says_what_for():
    r = analyse_curve(_hours(n=6), UA, GAIN, CURVE)
    assert r["status"] == "waiting" and r["running_hours"] == 6
    assert "12" in r["reason"]


def test_minimum_flow_and_cutoff_suggestions():
    r = analyse_curve(_hours(), UA, GAIN, CURVE, min_flow_c=35.0, cutoff_c=21.0)
    by = {x["key"]: x for x in r["settings"]}
    assert by["cutoff"]["suggested"] == 20  # 21 - 0.3 kW / 0.25 kW/K
    assert by["min_flow"]["suggested"] is not None and by["min_flow"]["suggested"] < 35


# ---------------------------------------------------------------- change and undo from the page


def _platform(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'h.sqlite'}", sim_days=2, data_dir=tmp_path)
    p = build_platform(settings)
    asyncio.run(p.bootstrap(0))
    p.telemetry.last_states = {
        "number.home_circuit_0_heating_curve": HAState("number.home_circuit_0_heating_curve", "0.8", {"min": 0.1, "max": 4.0, "step": 0.05}, platform="mypyllant"),
        "number.home_circuit_0_min_flow_temperature_setpoint": HAState("number.home_circuit_0_min_flow_temperature_setpoint", "30", {"min": 15, "max": 80, "step": 1},
                                                                       platform="mypyllant"),
    }
    p.controller.adapter.targets["number.home_circuit_0_heating_curve"] = 0.8
    return settings, p


def test_change_writes_once_logs_and_can_be_undone(tmp_path):
    settings, p = _platform(tmp_path)
    adapter = p.controller.adapter
    with session_scope(settings.database_url) as s:
        out = asyncio.run(p.apply_curve_setting(s, "heating_curve", 0.7))
        assert out["last_change"]["heating_curve"]["can_undo"]
    assert adapter.calls == [("number.home_circuit_0_heating_curve", 0.7)]
    with session_scope(settings.database_url) as s:
        rows = s.query(m.ControlExecutionLog).filter_by(action="number.set_value").all()
        assert len(rows) == 1 and rows[0].previous_value == "0.8" and rows[0].requested_value == "0.7"
        asyncio.run(p.undo_curve_setting(s, "heating_curve"))
    assert adapter.calls[-1] == ("number.home_circuit_0_heating_curve", 0.8)
    with session_scope(settings.database_url) as s:
        with pytest.raises(ValueError):
            asyncio.run(p.undo_curve_setting(s, "heating_curve"))  # an undo cannot itself be undone


def test_change_outside_limits_or_too_large_is_refused_and_nothing_written(tmp_path):
    settings, p = _platform(tmp_path)
    adapter = p.controller.adapter
    with session_scope(settings.database_url) as s:
        with pytest.raises(ValueError, match="at most"):
            asyncio.run(p.apply_curve_setting(s, "heating_curve", 1.5))
        with pytest.raises(ValueError, match="between"):
            asyncio.run(p.apply_curve_setting(s, "heating_curve", 0.05))
        with pytest.raises(ValueError, match="myVAILLANT"):
            asyncio.run(p.apply_curve_setting(s, "cutoff", 18))
        with pytest.raises(ValueError):
            asyncio.run(p.apply_curve_setting(s, "anything_else", 1))
    assert adapter.calls == []


def test_only_number_entities_can_be_set():
    from heat_pump_pilot.integrations.homeassistant.control import RecordingControlAdapter

    with pytest.raises(ValueError):
        asyncio.run(RecordingControlAdapter().set_number("climate.zone", 21))


def test_curve_check_endpoint_shape(tmp_path):
    from fastapi.testclient import TestClient

    from heat_pump_pilot.api.app import create_app

    settings, p = _platform(tmp_path)
    c = TestClient(create_app(settings, p))
    r = c.get("/api/heating-curve?days=7").json()
    assert r["status"] in ("ok", "waiting") and "writable" in r and "heating_curve" in r["writable"]
    assert c.post("/api/heating-curve/apply", json={"setting": "heating_curve", "value": 3.0}).status_code == 400


def test_after_a_change_the_check_restarts_from_the_change_and_an_undo_restores_it(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'r.sqlite'}", sim_days=16, data_dir=tmp_path)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    with session_scope(settings.database_url) as s:
        before = p.heating_curve_check(s)
        assert before["status"] == "ok" and before["direction"] == "lower"
        after = asyncio.run(p.apply_curve_setting(s, "heating_curve", before["suggested_curve"]))
        assert after["status"] == "waiting"  # no heating yet on the new setting
        assert after["since_change"]["to"] == before["suggested_curve"]
        assert "only at heating since then" in after["reason"]
        undone = asyncio.run(p.undo_curve_setting(s, "heating_curve"))
        assert undone["status"] == "ok" and undone["current_curve"] == before["current_curve"] and "since_change" not in undone
