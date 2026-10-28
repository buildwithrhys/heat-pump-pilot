"""Rule-based shadow planner."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from heat_pump_pilot.config import DataMode, OperatingMode, Settings
from heat_pump_pilot.optimiser import build_shadow_plan
from heat_pump_pilot.pricing import PriceClass, SlotPrice
from heat_pump_pilot.service import build_platform

UTC = timezone.utc


def go_tariff_day(start: datetime, cheap_p=7.0, peak_p=30.0) -> list[SlotPrice]:
    """Intelligent Go style: 23:30 to 05:30 cheap, everything else peak. 26 hours of slots."""
    out = []
    t = start
    for _ in range(52):
        hm = t.hour * 60 + t.minute
        cheap = hm >= 23 * 60 + 30 or hm < 5 * 60 + 30
        out.append(SlotPrice(t, t + timedelta(minutes=30), cheap_p if cheap else peak_p,
                             PriceClass.GUARANTEED_OFF_PEAK if cheap else PriceClass.PEAK))
        t += timedelta(minutes=30)
    return out


def test_go_tariff_plan_heats_water_at_end_of_cheap_window_and_preheats_before_peak():
    now = datetime(2026, 1, 10, 20, 0, tzinfo=UTC)
    plan = build_shadow_plan(now, go_tariff_day(now), dhw_tank_c=41.0, dhw_setpoint_c=50.0,
                             outdoor_forecast=[(now + timedelta(hours=h), 4.0) for h in range(26)])
    assert plan["status"] == "indicative"
    kinds = [a["kind"] for a in plan["actions"]]
    assert kinds == ["preheat_space", "heat_hot_water", "coast_space"]
    dhw = next(a for a in plan["actions"] if a["kind"] == "heat_hot_water")
    assert dhw["start"].endswith("T04:30:00+00:00") and dhw["end"].endswith("T05:30:00+00:00")
    assert "41 °C" in dhw["reason"] and dhw["saving_p_per_kwh"] > 10
    pre = next(a for a in plan["actions"] if a["kind"] == "preheat_space")
    assert pre["end"] <= dhw["start"]  # does not clash with hot water
    assert pre["rate_p"] == 7.0
    coast = next(a for a in plan["actions"] if a["kind"] == "coast_space")
    assert coast["start"].endswith("T05:30:00+00:00") and coast["end"].endswith("T08:30:00+00:00")
    assert plan["next_action"]["kind"] == "preheat_space" and plan["next_action"]["state"] == "upcoming"


def test_mild_weather_skips_preheat_but_keeps_hot_water():
    now = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
    plan = build_shadow_plan(now, go_tariff_day(now), outdoor_forecast=[(now + timedelta(hours=h), 19.0) for h in range(26)])
    assert [a["kind"] for a in plan["actions"]] == ["heat_hot_water"]
    assert any("warmer than" in a for a in plan["assumptions"])


def test_flat_tariff_runs_normally():
    now = datetime(2026, 1, 10, 20, 0, tzinfo=UTC)
    plan = build_shadow_plan(now, go_tariff_day(now, cheap_p=24.0, peak_p=25.0))
    assert plan["actions"][0]["kind"] == "run_normally"
    assert plan["next_action"]["kind"] == "run_normally"


def test_no_prices_is_not_available():
    now = datetime(2026, 1, 10, 20, 0, tzinfo=UTC)
    plan = build_shadow_plan(now, [])
    assert plan["status"] == "not_available" and plan["next_action"]["kind"] == "none"


def test_shadow_mode_records_each_plan(tmp_path):
    import asyncio

    from heat_pump_pilot.db import session_scope
    from heat_pump_pilot.db import models as m

    settings = Settings(data_mode=DataMode.SIMULATED, operating_mode=OperatingMode.SHADOW,
                        database_url=f"sqlite:///{tmp_path / 's.sqlite'}", sim_days=3, sim_seed=5)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())  # bootstrap includes one poll, so one plan is already recorded
    with session_scope(settings.database_url) as s:
        before = s.query(m.OptimisationRun).count()
    r = asyncio.run(p.poll_once())
    assert r.get("shadow_run_id")
    with session_scope(settings.database_url) as s:
        assert s.query(m.OptimisationRun).count() == before + 1
        assert all(run.slots for run in s.query(m.OptimisationRun).all())
    ov = p.overview()
    assert ov["plan"]["status"] in ("indicative", "model_based")
    assert ov["next_action"]["kind"] != "none"
    assert p.timeline(24)["optimisation"]["actions"]


def test_monitor_mode_shows_plan_but_records_nothing(tmp_path):
    import asyncio

    from heat_pump_pilot.db import session_scope
    from heat_pump_pilot.db import models as m

    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'm.sqlite'}", sim_days=3, sim_seed=5)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    asyncio.run(p.poll_once())
    with session_scope(settings.database_url) as s:
        assert s.query(m.OptimisationRun).count() == 0
    assert p.overview()["plan"]["status"] in ("indicative", "model_based")
