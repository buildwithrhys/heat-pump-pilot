"""Reading Predbat's published plan (read only) and the marginal cost of extra heating it implies."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from predheat.config import DataMode, Settings
from predheat.db import session_scope
from predheat.integrations.homeassistant.client import HAState
from predheat.integrations.predbat import Series, find_predbat, marginal_cost, read_plan
from predheat.service import build_platform

UTC = timezone.utc
T0 = datetime(2026, 10, 27, 0, 0, tzinfo=UTC)


def _key(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S%z")  # Predbat's own format, e.g. 2026-10-27T17:00:00+0000


def _series(fn, start=T0, hours=48) -> dict[str, float]:
    return {_key(start + timedelta(minutes=5 * i)): fn(start + timedelta(minutes=5 * i)) for i in range(hours * 12)}


def predbat_states(grid, battery, soc, export_p=15.0, import_p=28.0, value=9.0, prefix="predbat", updated=T0, start=T0) -> dict[str, HAState]:
    def st(name, results, **extra):
        eid = f"{prefix}.{name}"
        return eid, HAState(eid, "0", {"results": _series(results, start), **extra}, last_updated=updated)

    return dict([
        st("grid_power_best", grid), st("battery_power_best", battery),
        st("soc_kw_best", soc, value_per_kwh_now=value, soc_max=9.5),
        st("rates", lambda t: import_p), st("rates_export", lambda t: export_p),
        (f"{prefix}.status", HAState(f"{prefix}.status", "Idle", {}, last_updated=updated)),
    ])


def test_finds_predbat_under_any_prefix_and_not_otherwise():
    assert find_predbat(predbat_states(lambda t: 0, lambda t: 0, lambda t: 5))["grid_power_best"] == "predbat.grid_power_best"
    assert find_predbat(predbat_states(lambda t: 0, lambda t: 0, lambda t: 5, prefix="predbat_house"))["rates"] == "predbat_house.rates"
    assert find_predbat({"sensor.other": HAState("sensor.other", "1")}) is None
    assert read_plan({}) is None


def test_series_is_a_step_function_over_predbat_timestamps():
    s = Series.parse({"2026-10-27T17:00:00+0000": 2.0, "2026-10-27T17:30:00+0000": -1.0, "rubbish": 4})
    assert s.at(T0 + timedelta(hours=17, minutes=10)) == 2.0
    assert s.at(T0 + timedelta(hours=18)) == -1.0
    assert s.at(T0) is None


def _plan(**kw):
    return read_plan(predbat_states(**kw))


def test_exported_solar_is_priced_at_the_export_rate():
    p = _plan(grid=lambda t: -3.0, battery=lambda t: 0.0, soc=lambda t: 9.5)
    m = marginal_cost(p, T0 + timedelta(hours=12), T0 + timedelta(hours=12, minutes=30), 28.0, 1.5)
    assert m["source"] == "solar" and m["rate_p"] == 15.0


def test_partial_export_blends_with_the_grid():
    p = _plan(grid=lambda t: -0.5, battery=lambda t: 0.0, soc=lambda t: 0.5)  # battery empty, so the rest is grid
    m = marginal_cost(p, T0 + timedelta(hours=12), T0 + timedelta(hours=12, minutes=30), 28.0, 1.5)
    assert m["rate_p"] == round((0.5 * 15 + 1.0 * 28) / 1.5, 2) and m["source"] == "grid"


def test_battery_covering_the_house_prices_at_predbats_value_of_stored_energy():
    # discharging 1 kW in the evening; Predbat plans up to 3 kW elsewhere, and plenty left in the battery
    batt = lambda t: 3.0 if t.hour == 20 else 1.0 if 16 <= t.hour < 19 else 0.0
    p = _plan(grid=lambda t: 0.0, battery=batt, soc=lambda t: 2.0 if t.hour == 23 else 8.0)
    m = marginal_cost(p, T0 + timedelta(hours=17), T0 + timedelta(hours=17, minutes=30), 28.0, 1.5)
    assert m["source"] == "battery" and m["rate_p"] == 9.0


def test_battery_at_its_planned_floor_leaves_the_grid():
    p = _plan(grid=lambda t: 0.0, battery=lambda t: 1.0, soc=lambda t: 2.0)
    m = marginal_cost(p, T0 + timedelta(hours=17), T0 + timedelta(hours=17, minutes=30), 28.0, 1.5)
    assert m["source"] == "grid" and m["rate_p"] == 28.0


def test_importing_means_the_grid_price_stands_and_never_more_than_grid():
    p = _plan(grid=lambda t: 2.0, battery=lambda t: -2.0, soc=lambda t: 5.0)
    m = marginal_cost(p, T0 + timedelta(hours=2), T0 + timedelta(hours=2, minutes=30), 7.0, 1.5)
    assert m["source"] == "grid" and m["rate_p"] == 7.0
    p2 = _plan(grid=lambda t: -3.0, battery=lambda t: 0.0, soc=lambda t: 9.5, export_p=25.0)
    assert marginal_cost(p2, T0 + timedelta(hours=2), T0 + timedelta(hours=2, minutes=30), 7.0, 1.5)["rate_p"] == 7.0


def test_outside_the_plan_nothing_changes():
    p = _plan(grid=lambda t: -3.0, battery=lambda t: 0.0, soc=lambda t: 9.5)
    assert marginal_cost(p, T0 + timedelta(hours=60), T0 + timedelta(hours=60, minutes=30), 28.0, 1.5) is None


# ---------------------------------------------------------------- platform


def _platform(tmp_path, **settings_kw):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'p.sqlite'}", sim_days=16, data_dir=tmp_path, **settings_kw)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    return settings, p


def _evening_battery(now):
    # the battery covers the house from 16:00 to 19:00 local each day, with room to spare
    batt = lambda t: 3.0 if t.hour == 21 else 1.0 if 16 <= t.hour < 19 else 0.0
    return predbat_states(grid=lambda t: 0.0 if 16 <= t.hour < 19 else 0.5, battery=batt, soc=lambda t: 3.0 if t.hour == 23 else 8.0,
                          updated=now, start=now.replace(minute=0) - timedelta(hours=1))


def test_plan_uses_predbat_when_installed_and_fresh(tmp_path):
    settings, p = _platform(tmp_path)
    now = p.clock.now()
    p.telemetry.live_states = _evening_battery(now)
    assert p._refresh_predbat() == "found"
    st = p.predbat_status(now)
    assert st["in_use"] and st["battery_value_p"] == 9.0
    p._memo.clear()
    with session_scope(settings.database_url) as s:
        plan = p.shadow_plan(s, now)
    assert plan["status"] == "model_based", plan.get("assumptions")
    assert plan["energy_source"]["from"] == "predbat" and plan["energy_source"]["battery_half_hours"] > 0
    from_battery = [x for x in plan["predicted"] if x.get("source") == "battery"]
    assert from_battery and all(x["rate_p"] <= x["grid_rate_p"] for x in plan["predicted"] if "grid_rate_p" in x)
    assert any(x["rate_p"] < x["grid_rate_p"] for x in from_battery)
    assert "Predbat" in plan["assumptions"][0]
    tl = p.timeline(48)
    assert tl["predbat"]["in_use"] and any("heating_rate_p" in x for x in tl["slots"])


def test_stale_plan_or_setting_off_falls_back_to_the_grid_tariff(tmp_path):
    settings, p = _platform(tmp_path)
    now = p.clock.now()
    p.telemetry.live_states = _evening_battery(now - timedelta(hours=2))
    p._refresh_predbat()
    st = p.predbat_status(now)
    assert st["installed"] and not st["in_use"] and "30 minutes" in st["reason"]
    p.telemetry.live_states = _evening_battery(now)
    p._refresh_predbat()
    settings.use_predbat = False
    st = p.predbat_status(now)
    assert not st["in_use"] and "Settings" in st["reason"]
    p._memo.clear()
    with session_scope(settings.database_url) as s:
        plan = p.shadow_plan(s, now)
    assert "energy_source" not in plan and not any("source" in x for x in plan.get("predicted", []))


def test_without_predbat_nothing_changes(tmp_path):
    settings, p = _platform(tmp_path)
    assert p._refresh_predbat() == "not read"  # the simulator has no Home Assistant states
    st = p.predbat_status()
    assert not st["installed"] and "not found" in st["reason"]


def test_setting_is_stored_and_applied(tmp_path):
    from predheat.service.settings_store import SettingsStore

    settings = Settings(data_mode=DataMode.SIMULATED, data_dir=tmp_path)
    store = SettingsStore.load(tmp_path)
    assert store.values["use_predbat"] is True
    store.update({"use_predbat": False}, settings)
    assert settings.use_predbat is False and SettingsStore.load(tmp_path).values["use_predbat"] is False
