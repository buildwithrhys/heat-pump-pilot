"""The standing charge is a fixed daily amount: never pro-rated, never part of a heating figure,
and in the gas comparison only the optional gas standing charge counts."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

from predheat.config import DataMode, Settings
from predheat.db import models as m
from predheat.db import session_scope
from predheat.domain import StandingCharge
from predheat.pricing import DEFAULT_TEMPLATES, replay, standing_charge_for_period
from predheat.service import build_platform
from predheat.timeutils import LONDON

UTC = timezone.utc


def _platform(tmp_path, **kw):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 's.sqlite'}", sim_days=16, data_dir=tmp_path, **kw)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    return settings, p


def _at(p, day, hh, mm):
    p.clock = SimpleNamespace(now=lambda: datetime.combine(day, time(hh, mm), tzinfo=LONDON).astimezone(UTC))
    p._memo.clear()


def test_full_daily_rate_whatever_the_time_of_day():
    sc = [StandingCharge(start=datetime(2026, 1, 1, tzinfo=UTC), end=None, pence_per_day=54.3619, tariff_code="T")]
    midnight = datetime(2026, 10, 27, 0, 0, tzinfo=LONDON)
    early = standing_charge_for_period(sc, midnight, midnight + timedelta(minutes=30))
    late = standing_charge_for_period(sc, midnight, midnight + timedelta(hours=23, minutes=30))
    assert early == late == 54.3619
    assert standing_charge_for_period(sc, midnight, midnight + timedelta(days=7)) == round(54.3619 * 7, 4)


def test_overview_today_is_the_same_at_0030_and_2330_apart_from_usage(tmp_path):
    settings, p = _platform(tmp_path)
    day = (p.clock.now().astimezone(LONDON) - timedelta(days=1)).date()
    _at(p, day, 0, 30)
    early = p.overview()["today"]
    _at(p, day, 23, 30)
    late = p.overview()["today"]
    assert early["standing_charge_pence"] == late["standing_charge_pence"] == round(late["standing_charge"]["p_per_day"], 2)
    assert late["standing_charge"]["days"] == 1 and late["standing_charge"]["label"] == "Standing charge (fixed, per day)"


def test_nothing_in_the_today_row_includes_the_standing_charge(tmp_path):
    settings, p = _platform(tmp_path)
    day = (p.clock.now().astimezone(LONDON) - timedelta(days=1)).date()
    _at(p, day, 23, 30)
    before = p.overview()["today"]
    with session_scope(settings.database_url) as s:
        for row in s.query(m.TariffStandingCharge).all():
            row.pence_per_day = row.pence_per_day * 10
    p._memo.clear()
    after = p.overview()["today"]
    assert abs(after["standing_charge_pence"] - before["standing_charge_pence"] * 10) < 0.1
    for key in ("space_heating", "hot_water", "heat_pump_total", "whole_home_import", "gas_equivalent"):
        assert after[key]["cost_pence"] == before[key]["cost_pence"], key
    assert after["space_heating"]["pence_per_kwh_heat"] == before["space_heating"]["pence_per_kwh_heat"]


def _day_costs(p, day):
    with session_scope(p.settings.database_url) as s:
        return p._energy_costs(s, day, day + timedelta(days=1))


def test_gas_standing_charge_zero_or_unset_changes_nothing(tmp_path):
    settings, p = _platform(tmp_path)
    day = (p.clock.now().astimezone(LONDON) - timedelta(days=2)).date()
    unset = _day_costs(p, day)["gas_equivalent"]
    settings.gas_standing_charge_p_per_day = 0.0
    p._memo.clear()
    zero = _day_costs(p, day)["gas_equivalent"]
    assert zero["cost_pence"] == unset["cost_pence"] and zero["saving_pence"] == unset["saving_pence"]
    assert not zero["standing_charge_included"] and "not included" in zero["note"]


def test_gas_standing_charge_adds_30p_per_day_to_the_gas_side_only(tmp_path):
    settings, p = _platform(tmp_path)
    day = (p.clock.now().astimezone(LONDON) - timedelta(days=2)).date()
    base = _day_costs(p, day)
    assert base["gas_equivalent"]["days_used"] == 1, "the simulated day's heat pump figures add up"
    settings.gas_standing_charge_p_per_day = 30.0
    p._memo.clear()
    withsc = _day_costs(p, day)
    g0, g1 = base["gas_equivalent"], withsc["gas_equivalent"]
    assert round(g1["cost_pence"] - g0["cost_pence"], 2) == 30.0
    assert round(g1["saving_pence"] - g0["saving_pence"], 2) == 30.0  # the heat pump side is unchanged
    assert withsc["heat_pump_total"]["cost_pence"] == base["heat_pump_total"]["cost_pence"]
    assert g1["standing_charge_included"] and "30p" in g1["note"]
    week = None
    with session_scope(settings.database_url) as s:
        week = p._energy_costs(s, day - timedelta(days=6), day + timedelta(days=1))["gas_equivalent"]
    assert week["standing_charge_pence"] == 30.0 * week["standing_charge_days"]


def test_optimiser_and_predbat_plan_ignore_the_standing_charge(tmp_path):
    settings, p = _platform(tmp_path, control_enabled=True, operating_mode="balanced", control_climate_entity="climate.zone")
    now = p.clock.now()
    with session_scope(settings.database_url) as s:
        plan0 = p.shadow_plan(s, now)
        feed0 = p.predbat_feed(s, now)
        for row in s.query(m.TariffStandingCharge).all():
            row.pence_per_day = 500.0
    p._memo.clear()
    with session_scope(settings.database_url) as s:
        plan1 = p.shadow_plan(s, now)
        feed1 = p.predbat_feed(s, now)
    assert plan0["status"] == "model_based"
    assert [x["rate_p"] for x in plan1["predicted"]] == [x["rate_p"] for x in plan0["predicted"]]
    assert plan1["cost"] == plan0["cost"]
    assert [x["extra_kwh"] for x in feed1["slots"]] == [x["extra_kwh"] for x in feed0["slots"]]


def test_tariff_replay_charges_whole_days():
    go = next(t for t in DEFAULT_TEMPLATES if t.code == "INTELLI-VAR")
    day = datetime(2026, 1, 10, tzinfo=UTC)
    morning = [(day + timedelta(minutes=30 * i), day + timedelta(minutes=30 * (i + 1)), 1.0) for i in range(4)]
    assert replay(go, morning)["standing_pence"] == round(go.standing_p_per_day, 1)  # two hours of use still pays one whole day
    two_days = morning + [(s + timedelta(days=1), e + timedelta(days=1), k) for s, e, k in morning]
    assert replay(go, two_days)["standing_pence"] == round(go.standing_p_per_day * 2, 1)
