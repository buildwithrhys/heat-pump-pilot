"""Mapping overrides, metered half-hourly import, compaction, grouped reports and CSV."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from heat_pump_pilot.api import create_app
from heat_pump_pilot.config import DataMode, Settings
from heat_pump_pilot.db import init_db, session_scope
from heat_pump_pilot.db import models as m
from heat_pump_pilot.domain import Measurement, Provenance, Quality, Signal
from heat_pump_pilot.integrations.homeassistant.client import HAState
from heat_pump_pilot.integrations.homeassistant.providers import HATelemetryProvider
from heat_pump_pilot.service import build_platform
from heat_pump_pilot.service import repository as repo

UTC = timezone.utc


class StubClient:
    """Serves the pilot fixture as live states; no network."""

    def __init__(self, states):
        self._states = states

    async def states(self):
        return list(self._states)

    async def states_with_registry(self):
        return list(self._states)

    async def config_entries(self):
        return []

    async def statistics(self, ids, start, end, period="hour"):
        return {}

    async def ping(self):
        return {}


def test_override_changes_mapping_and_report(pilot_states, tmp_path):
    prov = HATelemetryProvider(StubClient(pilot_states))
    report = asyncio.run(prov.discover())
    before = next(c for c in report.capabilities if c.signal == Signal.INDOOR_TEMPERATURE).source_ref
    assert before.endswith("zone_1_circuit_0_current_temperature")
    assert prov.last_candidates["indoor_temperature"]
    report2 = prov.set_overrides({"indoor_temperature": {"source_ref": "sensor.wiser_lts_temperature_lounge", "unit_scale": 1.0}}, report)
    cap = next(c for c in report2.capabilities if c.signal == Signal.INDOOR_TEMPERATURE)
    assert cap.source_ref == "sensor.wiser_lts_temperature_lounge" and cap.notes.startswith("chosen by you")
    assert any(ms.entity_id == "sensor.wiser_lts_temperature_lounge" for ms in prov.mapping if ms.signal == Signal.INDOOR_TEMPERATURE)
    # persisted overrides survive a reload
    url = f"sqlite:///{tmp_path / 'o.sqlite'}"
    init_db(url)
    with session_scope(url) as s:
        prop = repo.ensure_property(s, "p", "Europe/London")
        repo.set_mapping_override(s, prop.id, "indoor_temperature", "sensor.wiser_lts_temperature_lounge", 1.0, "home_assistant", "sensor")
        assert "indoor_temperature" in repo.mapping_overrides(s, prop.id)
        assert repo.clear_mapping_override(s, prop.id, "indoor_temperature")
        assert repo.mapping_overrides(s, prop.id) == {}


def test_metered_intervals_are_used_for_whole_home_import(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'm.sqlite'}", sim_days=3, sim_seed=8)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    from heat_pump_pilot.timeutils import LONDON, local_day_bounds

    today = p.clock.now().astimezone(LONDON).date()
    day = today - timedelta(days=1)
    start, end = local_day_bounds(day)
    n = int((end - start).total_seconds() // 1800)
    ints = [(start + timedelta(minutes=30 * i), start + timedelta(minutes=30 * (i + 1)), 0.5) for i in range(n)]
    with session_scope(settings.database_url) as s:
        assert repo.upsert_metered_intervals(s, p.property_id, ints) == n
        assert repo.upsert_metered_intervals(s, p.property_id, ints) == 0  # idempotent
    c = p.energy_costs(day, day + timedelta(days=1))
    assert c["whole_home_import"]["source"] == "smart_meter_half_hourly"
    assert abs(c["whole_home_import"]["kwh"] - 0.5 * n) < 0.01
    assert c["whole_home_import"]["quality"] == "measured"


def test_compaction_keeps_one_reading_per_hour(tmp_path):
    url = f"sqlite:///{tmp_path / 'c.sqlite'}"
    init_db(url)
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    with session_scope(url) as s:
        prop = repo.ensure_property(s, "p", "Europe/London")
        rows = []
        for i in range(24):  # five-minute readings for two hours, two signals
            ts = t0 + timedelta(minutes=5 * i)
            rows.append(Measurement(signal=Signal.INDOOR_TEMPERATURE, ts=ts, value=20 + i * 0.01, quality=Quality.SENSOR, provenance=Provenance.HOME_ASSISTANT, source_ref="a"))
            rows.append(Measurement(signal=Signal.GRID_IMPORT_KWH, ts=ts, value=100 + i, quality=Quality.METERED, provenance=Provenance.OCTOPUS_HA, source_ref="b"))
        repo.upsert_measurements(s, prop.id, rows)
        deleted = repo.compact_measurements(s, prop.id, t0 + timedelta(days=1))
        assert deleted == 48 - 4
        left = s.query(m.SensorMeasurement).order_by(m.SensorMeasurement.signal, m.SensorMeasurement.ts).all()
        counters = [r for r in left if r.signal == "grid_import_kwh"]
        assert [r.value for r in counters] == [111.0, 123.0]  # last reading of each hour
        temps = [r for r in left if r.signal == "indoor_temperature"]
        assert [r.ts.minute for r in temps] == [0, 0]  # nearest the hour start


def test_grouped_reports_and_csv(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'g.sqlite'}", sim_days=16, sim_seed=8)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    today = p.clock.now().astimezone(timezone.utc).date()
    weeks = p.grouped_series(today - timedelta(days=14), today, "week")
    assert 2 <= len(weeks) <= 4 and all(w["days"] >= 1 for w in weeks)
    assert sum(w["days"] for w in weeks) == 14
    assert all(w["label"].startswith("Week") for w in weeks)
    months = p.grouped_series(today - timedelta(days=14), today, "month")
    assert 1 <= len(months) <= 2
    app = create_app(settings, platform=p, poll=False)
    with TestClient(app) as c:
        r = c.get(f"/api/costs?start={(today - timedelta(days=14)).isoformat()}&end={(today - timedelta(days=1)).isoformat()}&group=week")
        assert r.status_code == 200 and r.json()["group"] == "week" and len(r.json()["daily"]) == len(weeks)
        csv_text = c.get("/api/costs.csv?days=5").text
        assert csv_text.splitlines()[0].startswith("period_start,period_end,heating_kwh_electric")
        assert len(csv_text.splitlines()) == 6
        assert c.get("/api/costs?start=2026-10-10&end=2026-10-01").status_code == 400
