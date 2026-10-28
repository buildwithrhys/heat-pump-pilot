"""Failing-first tests for the 26 October data review. Each test names the item it covers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from heat_pump_pilot.analytics import counter_to_intervals
from heat_pump_pilot.domain import Measurement, Provenance, Quality, Signal
from heat_pump_pilot.pricing import DEFAULT_TEMPLATES, PriceClass, SlotPrice, build_rate_book, classify_slots
from heat_pump_pilot.domain import RateInterval

UTC = timezone.utc


def _v(sig: Signal, ts: datetime, value: float, src: str = "sensor.vaillant") -> Measurement:
    return Measurement(signal=sig, ts=ts, value=value, quality=Quality.MANUFACTURER_ESTIMATED,
                       provenance=Provenance.VAILLANT_MYPYLLANT, source_ref=src)


def _imp(ts: datetime, value: float, src: str = "sensor.octopus_total") -> Measurement:
    return Measurement(signal=Signal.GRID_IMPORT_KWH, ts=ts, value=value, quality=Quality.METERED,
                       provenance=Provenance.OCTOPUS_HA, source_ref=src)


def _daily_series(sig: Signal, hourly: list[float], day: datetime, catch_up_hours: set[int]) -> list[Measurement]:
    """myVAILLANT-style daily total: hour h is published at h+1:30, except hours in
    ``catch_up_hours`` which are published together in a burst three minutes apart."""
    arrivals = []
    burst_at = day + timedelta(hours=max(catch_up_hours) + 2, minutes=30) if catch_up_hours else None
    k = 0
    for h, kwh in enumerate(hourly):
        if h in catch_up_hours:
            arrivals.append((burst_at + timedelta(minutes=3 * k), kwh))
            k += 1
        else:
            arrivals.append((day + timedelta(hours=h + 1, minutes=30), kwh))
    arrivals.sort()
    rows, total = [_v(sig, day + timedelta(minutes=5), 0.0)], 0.0
    for t, kwh in arrivals:
        total += kwh
        rows.append(_v(sig, t, round(total, 3)))
    return rows


# ---------------------------------------------------------------- item 1: space heating heat lost
def test_item1_catch_up_lumps_of_heat_are_not_discarded():
    day = datetime(2026, 10, 26, tzinfo=UTC)  # GMT day, local midnight = 00:00 UTC
    heat = [1.4, 1.4, 1.5, 1.6, 1.7, 1.9, 2.4, 2.6, 2.3, 2.2, 2.0, 1.9, 1.7, 1.6, 1.6, 1.7, 1.9, 2.2, 2.4, 2.4, 2.2, 2.0]
    elec = [round(h * 9.2 / 43.3, 3) for h in heat]
    hs = _daily_series(Signal.HP_HEAT_HEATING_KWH, heat, day, {8, 9, 10, 11})
    es = _daily_series(Signal.HP_ELEC_HEATING_KWH, elec, day, {8, 9, 10, 11})
    got_h = sum(i.kwh for i in counter_to_intervals(hs, "heat"))
    got_e = sum(i.kwh for i in counter_to_intervals(es, "elec"))
    assert abs(got_h - sum(heat)) / sum(heat) < 0.01, (got_h, sum(heat))
    assert abs(got_e - sum(elec)) / sum(elec) < 0.01, (got_e, sum(elec))


def test_item1_daily_reset_with_a_late_first_update_counts_the_new_day():
    d = datetime(2026, 10, 26, tzinfo=UTC)
    rows = [_v(Signal.HP_HEAT_HEATING_KWH, d - timedelta(minutes=10), 43.3),   # yesterday's total
            _v(Signal.HP_HEAT_HEATING_KWH, d + timedelta(hours=3, minutes=30), 23.0),  # first update after midnight
            _v(Signal.HP_HEAT_HEATING_KWH, d + timedelta(hours=4, minutes=30), 29.0)]
    ints = counter_to_intervals(rows, "heat")
    today = sum(i.kwh for i in ints if i.start >= d)
    assert abs(today - 29.0) < 0.01, today


def test_item1_restatement_does_not_double_count():
    t = datetime(2026, 10, 26, 10, tzinfo=UTC)
    vals = [54000.0, 54000.2, 54000.4, 53995.4, 54000.6, 54000.8]  # one reading restated 5 kWh low, then back
    rows = [_imp(t + timedelta(minutes=20 * i), v) for i, v in enumerate(vals)]
    assert abs(sum(i.kwh for i in counter_to_intervals(rows, "imp")) - 0.8) < 1e-6


def test_item1_energy_balance_flags_inconsistent_days_and_never_reports_cop_below_one():
    from heat_pump_pilot.analytics.energy_accounting import energy_balance

    ok = energy_balance(elec_kwh=9.2, heat_kwh=43.3, env_kwh=35.1)
    assert ok["status"] == "ok" and abs(ok["cop"] - 4.71) < 0.01
    bad = energy_balance(elec_kwh=9.2, heat_kwh=0.4, env_kwh=35.1)
    assert bad["status"] == "inconsistent" and bad["cop"] is None
    low = energy_balance(elec_kwh=33.6, heat_kwh=5.0, env_kwh=None)
    assert low["status"] == "implausible" and low["cop"] is None


# ---------------------------------------------------------------- item 2: whole home import inflated
def test_item2_step_across_a_gap_is_not_counted_as_consumption():
    t = datetime(2026, 10, 24, tzinfo=UTC)
    rows = [_imp(t + timedelta(hours=h), 54000.0 + 0.5 * h, "sensor.octopus_total#statistics") for h in range(0, 5)]
    rows += [_imp(t + timedelta(hours=h), 54300.0 + 0.5 * h, "sensor.octopus_total#statistics") for h in range(18, 26)]  # 13 h hole, +300 step
    discards: list = []
    total = sum(i.kwh for i in counter_to_intervals(rows, "imp", discards=discards))
    assert total < 6.0, total
    assert discards and discards[0]["reason"].startswith("gap")


# ---------------------------------------------------------------- item 3: tariff replay reconciles
def _iog_book(start: datetime, days: int, cheap: float = 7.0, peak: float = 36.1) -> object:
    from heat_pump_pilot.timeutils import LONDON

    rates = []
    t = start
    while t < start + timedelta(days=days):
        lt = t.astimezone(LONDON)
        m = lt.hour * 60 + lt.minute
        rate = cheap if (m >= 23 * 60 + 30 or m < 5 * 60 + 30) else peak
        rates.append(RateInterval(start=t, end=t + timedelta(minutes=30), rate_p_per_kwh=rate, tariff_code="E-1R-INTELLI-VAR-24-10-29-K"))
        t += timedelta(minutes=30)
    return build_rate_book(rates, "E-1R-INTELLI-VAR-24-10-29-K")


def test_item3_cheap_window_is_local_time_in_bst_and_gmt():
    go = next(t for t in DEFAULT_TEMPLATES if t.code == "INTELLI-VAR")
    assert go.rate_at(datetime(2026, 10, 20, 22, 30, tzinfo=UTC)) == go.windows[0].rate_p  # 23:30 BST
    assert go.rate_at(datetime(2026, 10, 20, 22, 0, tzinfo=UTC)) == go.default_rate_p      # 23:00 BST
    assert go.rate_at(datetime(2026, 10, 27, 23, 0, tzinfo=UTC)) == go.default_rate_p      # 23:00 GMT
    assert go.rate_at(datetime(2026, 10, 27, 23, 30, tzinfo=UTC)) == go.windows[0].rate_p  # 23:30 GMT


def test_item3_what_you_paid_excludes_unpriced_slots_and_reports_coverage():
    from heat_pump_pilot.pricing.tariffs import price_actual

    start = datetime(2026, 10, 20, tzinfo=UTC)
    book = _iog_book(start + timedelta(days=4), 2)  # prices only for the last two of six days
    triples = [(start + timedelta(minutes=30 * i), start + timedelta(minutes=30 * (i + 1)), 1.0) for i in range(48 * 6)]
    a = price_actual(book, triples)
    assert abs(a["coverage_pct"] - 33.3) < 0.2
    assert a["kwh_priced"] == 96.0
    assert 7.0 < a["effective_p_per_kwh"] < 36.1  # cost over priced kWh only, not over all kWh
    assert len(a["priced_triples"]) == 96


def test_item3_current_tariff_replay_matches_actual():
    from heat_pump_pilot.pricing.tariffs import price_actual, reconcile_template

    start = datetime(2026, 10, 20, tzinfo=UTC)
    book = _iog_book(start, 7)
    triples = [(start + timedelta(minutes=30 * i), start + timedelta(minutes=30 * (i + 1)), 0.6 + (i % 7) * 0.1) for i in range(48 * 7)]
    actual = price_actual(book, triples)
    go = next(t for t in DEFAULT_TEMPLATES if t.code == "INTELLI-VAR")
    import dataclasses

    exact = dataclasses.replace(go, default_rate_p=36.1, windows=[dataclasses.replace(go.windows[0], rate_p=7.0)])
    rec = reconcile_template(exact, actual)
    assert abs(rec["difference_pct"]) < 1.0, rec


# ---------------------------------------------------------------- item 4: annual estimate region
def test_item4_climate_band_comes_from_tariff_region():
    from heat_pump_pilot.pricing.climate import band_for

    assert band_for(region="K", latitude=52.6)["label"].startswith("south west and south Wales")
    assert band_for(region="P", latitude=None)["label"] == "Scotland"
    assert band_for(region=None, latitude=51.4)["label"].startswith("south")


# ---------------------------------------------------------------- item 5: plan text and price windows
def _slots_with_planned_dispatch(day: datetime) -> list[SlotPrice]:
    """GMT day: cheap 23:30 to 05:30; an EV dispatch is planned 21:00 to 23:30 (not confirmed)."""
    out = []
    t = day + timedelta(hours=18)
    for _ in range(36):
        m = t.hour * 60 + t.minute
        if m >= 23 * 60 + 30 or m < 5 * 60 + 30:
            out.append(SlotPrice(t, t + timedelta(minutes=30), 7.0, PriceClass.GUARANTEED_OFF_PEAK))
        elif 21 * 60 <= m < 23 * 60 + 30:
            out.append(SlotPrice(t, t + timedelta(minutes=30), 7.0, PriceClass.PLANNED_DISPATCH, assumed=True))
        else:
            out.append(SlotPrice(t, t + timedelta(minutes=30), 36.1, PriceClass.PEAK))
        t += timedelta(minutes=30)
    return out


def test_item5_planned_dispatch_is_not_treated_as_cheap_and_hot_water_ends_at_0530():
    from heat_pump_pilot.optimiser import build_shadow_plan

    day = datetime(2026, 10, 27, tzinfo=UTC)
    now = day + timedelta(hours=18)
    plan = build_shadow_plan(now, _slots_with_planned_dispatch(day), outdoor_forecast=[(now + timedelta(hours=h), 4.0) for h in range(20)])
    hw = next(a for a in plan["actions"] if a["kind"] == "heat_hot_water")
    assert hw["end"] == (day + timedelta(days=1, hours=5, minutes=30)).isoformat(), hw
    assert "23:30 to 05:30" in hw["reason"], hw["reason"]


def test_item5_coast_has_no_saving_claim_and_each_action_says_what_its_saving_is_against():
    from heat_pump_pilot.optimiser import build_shadow_plan

    day = datetime(2026, 10, 27, tzinfo=UTC)
    now = day + timedelta(hours=18)
    plan = build_shadow_plan(now, _slots_with_planned_dispatch(day), outdoor_forecast=[(now + timedelta(hours=h), 4.0) for h in range(20)])
    for a in plan["actions"]:
        if a["kind"] == "coast_space":
            assert a["saving_p_per_kwh"] is None
        if a["saving_p_per_kwh"] is not None:
            assert a["saving_basis"] in ("vs_dear_period", "vs_average")


def test_item5_future_intelligent_adjusted_rate_is_planned_not_confirmed():
    t0 = datetime(2026, 10, 27, 18, tzinfo=UTC)
    rates = [RateInterval(start=t0 + timedelta(minutes=30 * i), end=t0 + timedelta(minutes=30 * (i + 1)), rate_p_per_kwh=7.0 if i in (6, 7) else 36.1,
                          tariff_code="X", is_intelligent_adjusted=i in (6, 7)) for i in range(12)]
    book = build_rate_book(rates, "X")
    slots = classify_slots(t0, t0 + timedelta(hours=6), book, [], now=t0 + timedelta(hours=1))
    assert slots[6].price_class == PriceClass.PLANNED_DISPATCH and slots[6].assumed


# ---------------------------------------------------------------- item 6: days without data
def test_item6_days_without_data_are_flagged_and_counted_in_coverage(tmp_path):
    import asyncio

    from heat_pump_pilot.config import DataMode, Settings
    from heat_pump_pilot.service import build_platform

    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'c.sqlite'}", sim_days=3, sim_seed=2, data_dir=tmp_path)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    from heat_pump_pilot.timeutils import LONDON

    today = p.clock.now().astimezone(LONDON).date()
    rows = p.daily_series(today - timedelta(days=7), today + timedelta(days=1))
    assert any(r["has_data"] is False for r in rows) and any(r["has_data"] for r in rows)
    summary = p.energy_costs(today - timedelta(days=7), today + timedelta(days=1))
    assert summary["coverage"]["days"] == 8 and 0 < summary["coverage"]["days_with_data"] < 8


# ---------------------------------------------------------------- diagnostics and migration
def test_diagnostics_report_shows_old_and_new_side_by_side(tmp_path):
    import asyncio

    from fastapi.testclient import TestClient

    from heat_pump_pilot.api import create_app
    from heat_pump_pilot.config import DataMode, Settings
    from heat_pump_pilot.service import build_platform

    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'd.sqlite'}", sim_days=4, sim_seed=2, data_dir=tmp_path)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    app = create_app(settings, platform=p, poll=False)
    with TestClient(app) as c:
        rep = c.get("/api/diagnostics/report?days=7&raw_days=2").json()
        assert set(rep["signals"]) >= {"grid_import_kwh", "hp_heat_heating_kwh", "hp_env_heating_kwh"}
        sig = rep["signals"]["hp_heat_heating_kwh"]
        assert sig["raw"] and sig["derived_half_hours"] and "total_old" in sig and "total_new" in sig
        assert rep["daily"] and "space_heating_balance" in rep["daily"][-1] and "import_slots_with_data_pct" in rep["daily"][-1]
        assert "last_7_days" in rep["summary"]
        r = c.get("/api/diagnostics/report?days=7&download=true")
        assert r.headers["content-disposition"].startswith("attachment")


def test_state_statistics_rows_are_dropped_once_and_a_full_refetch_is_requested(tmp_path):
    from heat_pump_pilot.db import init_db, session_scope
    from heat_pump_pilot.db import models as mdl
    from heat_pump_pilot.service import repository as repo

    url = f"sqlite:///{tmp_path / 'm.sqlite'}"
    init_db(url)
    t = datetime(2026, 10, 25, 10, tzinfo=UTC)
    with session_scope(url) as s:
        prop = repo.ensure_property(s, "p", "Europe/London")
        repo.upsert_measurements(s, prop.id, [
            _v(Signal.HP_HEAT_HEATING_KWH, t, 20.0, "sensor.heat#statistics"),
            _v(Signal.HP_HEAT_HEATING_KWH, t + timedelta(hours=1), 900.0, "sensor.heat#statistics_sum"),
            _v(Signal.HP_HEAT_HEATING_KWH, t + timedelta(minutes=7), 20.3, "sensor.heat")])
        assert repo.drop_state_statistics(s, prop.id) == 1
        assert repo.drop_state_statistics(s, prop.id) == 0
        assert s.get(mdl.Property, prop.id).extra.get("force_full_backfill") is True
        assert {r.source_ref for r in s.query(mdl.SensorMeasurement).all()} == {"sensor.heat#statistics_sum", "sensor.heat"}
