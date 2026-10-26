"""Cumulative counters must never be summed as if they were intervals."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from predheat.analytics import counter_to_intervals
from predheat.domain import Measurement, Provenance, Quality, Signal
from predheat.integrations.homeassistant.providers import HATelemetryProvider, MappedSignal

UTC = timezone.utc


def m(ts, v):
    return Measurement(signal=Signal.GRID_IMPORT_KWH, ts=ts, value=v, quality=Quality.METERED, provenance=Provenance.OCTOPUS_HA, source_ref="x")


def test_out_of_order_lifetime_readings_do_not_explode():
    t = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
    # the pattern that produced 589,756 kWh in a day: an end-of-hour value stamped at the hour start, then live readings
    readings = [m(t, 54052.9), m(t + timedelta(minutes=5), 54052.1), m(t + timedelta(minutes=10), 54052.2), m(t + timedelta(minutes=65), 54053.0)]
    ints = counter_to_intervals(readings, "grid_import")
    total = sum(i.kwh for i in ints)
    assert total < 2.0, total
    assert any("non_monotonic_ignored" in i.notes for i in ints)


def test_real_reset_still_counts_from_zero():
    t = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
    ints = counter_to_intervals([m(t, 120.0), m(t + timedelta(minutes=30), 0.4), m(t + timedelta(minutes=60), 1.0)], "x")
    assert abs(sum(i.kwh for i in ints) - 1.0) < 1e-6
    assert any("counter_reset" in i.notes for i in ints)


class _Stub:
    async def statistics(self, ids, start, end, period="hour"):
        t0 = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
        return {"sensor.meter": [{"start": t0.timestamp() * 1000, "end": (t0 + timedelta(hours=1)).timestamp() * 1000, "state": 100.0, "mean": None},
                                 {"start": (t0 + timedelta(hours=1)).timestamp() * 1000, "end": (t0 + timedelta(hours=2)).timestamp() * 1000, "state": 101.0, "mean": None}],
                "sensor.temp": [{"start": t0.timestamp() * 1000, "end": (t0 + timedelta(hours=1)).timestamp() * 1000, "state": None, "mean": 20.0}]}


def test_history_counters_are_stamped_at_end_of_hour_and_means_mid_hour():
    prov = HATelemetryProvider(_Stub(), mapping=[
        MappedSignal(Signal.GRID_IMPORT_KWH, "sensor.meter", 1.0, Quality.METERED, Provenance.OCTOPUS_HA),
        MappedSignal(Signal.INDOOR_TEMPERATURE, "sensor.temp", 1.0, Quality.SENSOR, Provenance.HOME_ASSISTANT)])
    out = asyncio.run(prov.read_history(datetime(2026, 10, 26, 9, 0, tzinfo=UTC), datetime(2026, 10, 26, 12, 0, tzinfo=UTC)))
    counters = sorted([x for x in out if x.signal == Signal.GRID_IMPORT_KWH], key=lambda x: x.ts)
    assert counters[0].ts == datetime(2026, 10, 26, 10, 0, tzinfo=UTC) and counters[0].value == 100.0
    assert counters[1].ts == datetime(2026, 10, 26, 11, 0, tzinfo=UTC)
    temp = next(x for x in out if x.signal == Signal.INDOOR_TEMPERATURE)
    assert temp.ts == datetime(2026, 10, 26, 9, 30, tzinfo=UTC)


def test_purge_removes_only_on_the_hour_counter_rows(tmp_path):
    from predheat.db import init_db, session_scope
    from predheat.db import models as mdl
    from predheat.service import repository as repo

    url = f"sqlite:///{tmp_path / 'm.sqlite'}"
    init_db(url)
    t = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
    with session_scope(url) as s:
        prop = repo.ensure_property(s, "p", "Europe/London")
        repo.upsert_measurements(s, prop.id, [m(t, 1.0), m(t + timedelta(minutes=5, microseconds=123), 2.0),
                                              Measurement(signal=Signal.INDOOR_TEMPERATURE, ts=t, value=20.0, quality=Quality.SENSOR, provenance=Provenance.HOME_ASSISTANT, source_ref="y")])
        assert repo.purge_misstamped_history(s, prop.id) == 1
        assert repo.purge_misstamped_history(s, prop.id) == 0  # once only
        left = s.query(mdl.SensorMeasurement).count()
        assert left == 2
