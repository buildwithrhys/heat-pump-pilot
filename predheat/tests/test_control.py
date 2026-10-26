"""Guard chain for controlled live optimisation. Uses the recording adapter; nothing is written anywhere."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from predheat.config import DataMode, OperatingMode, Settings
from predheat.control import Controller
from predheat.db import session_scope
from predheat.db import models as m
from predheat.integrations.homeassistant.control import RecordingControlAdapter
from predheat.service import build_platform

UTC = timezone.utc


def plan_with(mode: str, now: datetime) -> dict:
    return {"status": "model_based", "model": {"setpoint_c": 21.0, "tolerance_c": 1.0},
            "predicted": [{"start": (now - timedelta(minutes=5)).isoformat(), "mode": mode}]}


def make(mode=OperatingMode.BALANCED, enabled=True, entity="climate.zone", tmp_path=None):
    settings = Settings(data_mode=DataMode.SIMULATED, operating_mode=mode, control_enabled=enabled, control_climate_entity=entity,
                        database_url=f"sqlite:///{tmp_path / 'c.sqlite'}", sim_days=2)
    p = build_platform(settings)
    asyncio.run(p.bootstrap(0))
    adapter = RecordingControlAdapter(initial=21.0)
    c = Controller(settings, adapter, property_id=p.property_id)
    return settings, c, adapter


def run(c, settings, plan, indoor=20.8, age=timedelta(minutes=5), conf=0.8, now=None):
    now = now or datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    with session_scope(settings.database_url) as s:
        d = asyncio.run(c.step(s, now, plan, indoor, age, conf))
    return d


def test_disabled_controller_never_writes(tmp_path):
    settings, c, adapter = make(enabled=False, tmp_path=tmp_path)
    d = run(c, settings, plan_with("boost", datetime(2026, 1, 10, 12, 0, tzinfo=UTC)))
    assert d.status == "blocked" and not adapter.calls


def test_monitor_mode_blocks_even_when_enabled(tmp_path):
    settings, c, adapter = make(mode=OperatingMode.MONITOR, tmp_path=tmp_path)
    assert not c.configured()[0]
    d = run(c, settings, plan_with("boost", datetime(2026, 1, 10, 12, 0, tzinfo=UTC)))
    assert d.status == "blocked" and not adapter.calls


def test_boost_raises_setpoint_by_offset_and_logs(tmp_path):
    settings, c, adapter = make(tmp_path=tmp_path)
    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    d = run(c, settings, plan_with("boost", now), now=now)
    assert d.acted and adapter.calls == [("climate.zone", 22.0)]
    assert c.holding and c.baseline_setpoint_c == 21.0
    with session_scope(settings.database_url) as s:
        rows = s.query(m.ControlExecutionLog).all()
        assert rows and rows[-1].status == "executed" and rows[-1].requested_value == "22.0" and rows[-1].previous_value == "21.0"
    # coast after the minimum interval lowers it
    later = now + timedelta(minutes=30)
    d2 = run(c, settings, plan_with("coast", later), now=later)
    assert d2.acted and adapter.calls[-1] == ("climate.zone", 20.0)


def test_max_savings_uses_larger_offset(tmp_path):
    settings, c, adapter = make(mode=OperatingMode.MAX_SAVINGS, tmp_path=tmp_path)
    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    run(c, settings, plan_with("boost", now), now=now)
    assert adapter.calls == [("climate.zone", 22.5)]


def test_manual_change_releases_and_backs_off(tmp_path):
    settings, c, adapter = make(tmp_path=tmp_path)
    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    run(c, settings, plan_with("boost", now), now=now)
    adapter.targets["climate.zone"] = 19.5  # occupant turns it down
    d = run(c, settings, plan_with("boost", now), now=now + timedelta(minutes=30))
    assert d.status == "released" and not c.holding and c.override_until is not None
    d2 = run(c, settings, plan_with("boost", now), now=now + timedelta(hours=1))
    assert d2.status == "blocked" and "override" in d2.reason
    assert len(adapter.calls) == 1  # nothing written after the manual change


def test_stale_reading_hands_the_setpoint_back(tmp_path):
    settings, c, adapter = make(tmp_path=tmp_path)
    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    run(c, settings, plan_with("boost", now), now=now)
    d = run(c, settings, plan_with("boost", now), age=timedelta(hours=2), now=now + timedelta(minutes=30))
    assert d.status == "released" and adapter.calls[-1] == ("climate.zone", 21.0)
    assert not c.holding


def test_low_confidence_and_cold_house_block(tmp_path):
    settings, c, adapter = make(tmp_path=tmp_path)
    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    assert run(c, settings, plan_with("boost", now), conf=0.2, now=now).status == "blocked"
    assert run(c, settings, plan_with("coast", now), indoor=19.0, now=now).status == "blocked"
    assert not adapter.calls


def test_platform_runs_controller_in_poll_and_releases_on_demand(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, operating_mode=OperatingMode.BALANCED, control_enabled=True,
                        control_climate_entity="climate.sim", database_url=f"sqlite:///{tmp_path / 'p.sqlite'}", sim_days=10, sim_seed=4)
    p = build_platform(settings)
    assert p.controller is not None
    asyncio.run(p.bootstrap())
    r = asyncio.run(p.poll_once())
    assert "control" in r
    st = p.control_status()
    assert st["configured"] and st["entity"] == "climate.sim"
    asyncio.run(p.release_control("test"))
    assert not p.controller.holding


def test_addon_forces_control_off_without_entity():
    from predheat.addon import env_from_options

    assert env_from_options({"control_enabled": True}, None)["PREDHEAT_CONTROL_ENABLED"] == "false"
    env = env_from_options({"control_enabled": True, "control_climate_entity": "climate.x"}, None)
    assert env["PREDHEAT_CONTROL_ENABLED"] == "true" and env["PREDHEAT_CONTROL_CLIMATE_ENTITY"] == "climate.x"
