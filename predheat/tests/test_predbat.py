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


# ---------------------------------------------------------------- sharing PredHeat's plan with Predbat


def _acting_platform(tmp_path, **kw):
    settings, p = _platform(tmp_path, **kw)
    now = p.clock.now()
    p.telemetry.live_states = _evening_battery(now)
    p._refresh_predbat()
    p._memo.clear()
    return settings, p, now


def _publish(settings, p):
    with session_scope(settings.database_url) as s:
        return asyncio.run(p._publish_predbat_feed(s))


def test_nothing_is_added_while_predheat_is_not_controlling(tmp_path):
    settings, p, now = _acting_platform(tmp_path)  # default: monitor mode, control off
    assert _publish(settings, p).startswith("published 0.00")
    pub = p.controller.adapter.published["sensor.predheat_heat_pump_plan"]
    assert pub["attributes"]["status"] == "not_acting" and all(x["energy"] == 0.0 for x in pub["attributes"]["extra"])
    assert pub["attributes"]["planned"][-1]["energy"] > 0  # the full plan is still there for reference


def test_when_controlling_only_heating_above_recent_days_is_added(tmp_path):
    settings, p, now = _acting_platform(tmp_path, control_enabled=True, operating_mode="balanced", control_climate_entity="climate.zone")
    with session_scope(settings.database_url) as s:
        feed = p.predbat_feed(s, now)
    assert feed["status"] == "ok" and feed["slots"]
    for x in feed["slots"]:
        assert x["extra_kwh"] == round(max(0.0, x["planned_kwh"] - x["usual_kwh"]), 3)
    assert feed["apps_yaml"] == "load_forecast:\n  - sensor.predheat_heat_pump_plan$extra"
    _publish(settings, p)
    extra = p.controller.adapter.published["sensor.predheat_heat_pump_plan"]["attributes"]["extra"]
    # Predbat's format: incrementing kWh keyed by "%Y-%m-%dT%H:%M:%S%z" timestamps, half an hour apart
    stamps = [datetime.strptime(x["last_updated"], "%Y-%m-%dT%H:%M:%S%z") for x in extra]
    assert all(b - a == timedelta(minutes=30) for a, b in zip(stamps, stamps[1:]))
    energies = [x["energy"] for x in extra]
    assert energies[0] == 0.0 and energies == sorted(energies)
    assert abs(energies[-1] - sum(x["extra_kwh"] for x in feed["slots"])) < 0.01


def test_trial_ordinary_days_add_nothing(tmp_path):
    settings, p, now = _acting_platform(tmp_path, control_enabled=True, operating_mode="balanced", control_climate_entity="climate.zone", trial_mode=True)
    with session_scope(settings.database_url) as s:
        feed = p.predbat_feed(s, now)
    assert any(x["ordinary_day"] for x in feed["slots"]) and all(x["extra_kwh"] == 0.0 for x in feed["slots"] if x["ordinary_day"])


def test_not_published_without_predbat_or_with_sharing_off(tmp_path):
    settings, p = _platform(tmp_path)
    assert _publish(settings, p) == "Predbat not installed" and not p.controller.adapter.published
    p.telemetry.live_states = _evening_battery(p.clock.now())
    p._refresh_predbat()
    settings.publish_for_predbat = False
    assert _publish(settings, p) == "sharing is off" and not p.controller.adapter.published


def test_only_predheats_own_sensors_can_be_published():
    import pytest

    from predheat.integrations.homeassistant.control import RecordingControlAdapter

    with pytest.raises(ValueError):
        asyncio.run(RecordingControlAdapter().publish_state("sensor.predbat_anything", "1", {}))
    with pytest.raises(ValueError):
        asyncio.run(RecordingControlAdapter().publish_state("climate.zone", "1", {}))


def test_hot_water_is_shared_only_when_predheat_moves_the_cylinder_reheat(tmp_path):
    settings, p, now = _acting_platform(tmp_path, control_enabled=True, operating_mode="balanced", control_climate_entity="climate.zone")
    with session_scope(settings.database_url) as s:
        without = p.predbat_feed(s, now)
    assert not without["includes_hot_water"] and all(x["hot_water_kwh"] == 0.0 for x in without["slots"])
    settings.control_dhw_entity = "water_heater.cylinder"
    from predheat.domain import Measurement, Provenance, Quality, Signal
    from predheat.service import repository as repo

    with session_scope(settings.database_url) as s:  # the simulator reports no cylinder target; give it 55 degC
        repo.upsert_measurements(s, p.property_id, [Measurement(signal=Signal.DHW_SETPOINT, ts=now, value=55.0, quality=Quality.SENSOR,
                                                                provenance=Provenance.SIMULATOR, source_ref="test")])
    p._memo.clear()
    with session_scope(settings.database_url) as s:
        plan = p.shadow_plan(s, now)
        feed = p.predbat_feed(s, now)
    reheat = [x for x in plan["predicted"] if x["dhw_elec_kwh"] > 0]
    assert reheat, "the simulated cylinder needs a reheat"
    assert feed["includes_hot_water"] and sum(x["hot_water_kwh"] for x in feed["slots"]) > 0
    for x in feed["slots"]:
        assert x["extra_kwh"] == round(max(0.0, x["planned_kwh"] - x["usual_kwh"]), 3)


# ---------------------------------------------------------------- a real install's shape, and stale dashboard pages


def _raw(entity_id, state, attributes=None, updated="2026-10-27T15:55:02.123456+00:00"):
    return {"entity_id": entity_id, "state": state, "attributes": attributes or {}, "last_changed": updated, "last_updated": updated}


def test_detects_predbat_as_home_assistant_reports_it():
    """Shaped like a real install: Predbat's own Predheat switch off, a Hypervolt sensor with predbat in its name,
    plan entities with Predbat's timestamp keys, and over a hundred other predbat.* entities."""
    from predheat.integrations.homeassistant.client import _parse_state

    res = {"2026-10-27T16:00:00+0000": 0.0, "2026-10-27T16:05:00+0000": 0.4}
    raws = [
        _raw("predbat.grid_power_best", "0.0", {"results": res}),
        _raw("predbat.battery_power_best", "3.526", {"results": res}),
        _raw("predbat.soc_kw_best", "0.6", {"results": res, "value_per_kwh_now": 8.1, "soc_max": 9.5}, updated="2026-10-27T15:58:00+00:00"),
        _raw("predbat.rates", "24.5", {"results": res}),
        _raw("switch.predbat_predheat_enable", "off"),
        _raw("sensor.predbat_hypervolt_home_3_pro_power", "0"),
    ] + [_raw(f"predbat.other_{i}", "1") for i in range(130)]
    states = {r["entity_id"]: _parse_state(r) for r in raws}
    plan = read_plan(states)
    assert plan is not None and plan.prefix == "predbat"
    assert plan.updated_at == datetime(2026, 10, 27, 15, 58, tzinfo=UTC)  # the freshest plan entity
    assert plan.fresh(datetime(2026, 10, 27, 16, 10, tzinfo=UTC))


def test_system_health_always_reports_predbat(tmp_path):
    settings, p = _platform(tmp_path)
    s = p.system_health([])
    assert s["predbat"]["installed"] is False and "not found" in s["predbat"]["reason"]
    p.telemetry.live_states = _evening_battery(p.clock.now())
    p._refresh_predbat()
    assert p.system_health([])["predbat"]["installed"] is True


def test_dashboard_page_is_not_cached_and_api_names_the_current_build(tmp_path):
    from fastapi.testclient import TestClient

    from predheat.api.app import create_app

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<script type="module" src="./assets/index-AbC123_x.js"></script>')
    (dist / "assets" / "index-AbC123_x.js").write_text("console.log(1)")
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'd.sqlite'}", sim_days=2, data_dir=tmp_path, frontend_dir=dist)
    p = build_platform(settings)
    asyncio.run(p.bootstrap(0))
    c = TestClient(create_app(settings, p, poll=False))
    assert c.get("/").headers["cache-control"] == "no-cache"
    assert "immutable" in c.get("/assets/index-AbC123_x.js").headers["cache-control"]
    h = c.get("/api/health").headers
    assert h["x-predheat-bundle"] == "index-AbC123_x.js" and h["x-predheat-version"]


def test_while_waiting_predbat_still_gets_a_valid_flat_series(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'w.sqlite'}", sim_days=2, data_dir=tmp_path)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())  # two days: the house model is still learning
    p.telemetry.live_states = _evening_battery(p.clock.now())
    p._refresh_predbat()
    with session_scope(settings.database_url) as s:
        assert p.predbat_feed(s)["status"] == "waiting"
    _publish(settings, p)
    a = p.controller.adapter.published["sensor.predheat_heat_pump_plan"]["attributes"]
    assert a["status"] == "waiting" and len(a["extra"]) == 97 and all(x["energy"] == 0.0 for x in a["extra"])
    datetime.strptime(a["extra"][0]["last_updated"], "%Y-%m-%dT%H:%M:%S%z")
