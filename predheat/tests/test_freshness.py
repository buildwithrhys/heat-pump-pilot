"""Whether a reading is current. Settings, modes and flags that hold for days are not stale; a
temperature that stops arriving, an unavailable entity or a silent integration is."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from predheat.config import DataMode, Settings
from predheat.domain import Provenance, Quality, Signal
from predheat.integrations.homeassistant.client import HAState
from predheat.integrations.homeassistant.providers import HATelemetryProvider, MappedSignal
from predheat.service import build_platform

UTC = timezone.utc
NOW = datetime(2026, 10, 27, 18, 0, tzinfo=UTC)


def st(eid, state, platform, updated_ago, reported_ago=None):
    s = HAState(eid, state, {}, last_changed=NOW - updated_ago, last_updated=NOW - updated_ago,
                last_reported=(NOW - reported_ago) if reported_ago is not None else None, platform=platform)
    return s


def install(tmp_path, states):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'f.sqlite'}", sim_days=2, data_dir=tmp_path)
    p = build_platform(settings)
    tel = HATelemetryProvider(client=None)
    tel.last_states = {s.entity_id: s for s in states}
    tel.live_states = {s.entity_id: s for s in states}
    p.telemetry = tel
    return p


def fresh(p, sig, eid):
    s = p.telemetry.live_states[eid]
    return p._freshness(sig, SimpleNamespace(ts=s.last_updated), NOW, eid)


H = timedelta(hours=1)
M = timedelta(minutes=1)


def users_install():
    """The entities from the report: settings and flags unchanged for hours, with their integrations healthy."""
    return [
        st("number.home_circuit_0_heating_curve", "0.5", "mypyllant", 16 * H),
        st("number.home_circuit_0_min_flow_temperature_setpoint", "20.0", "mypyllant", 16 * H),
        st("number.home_circuit_0_heat_demand_limited_by_outside_temperature", "18.0", "mypyllant", 16 * H),
        st("select.home_domestic_hot_water_0_operation_mode", "Manual", "mypyllant", 16 * H),
        st("switch.home_domestic_hot_water_0_boost", "off", "mypyllant", 16 * H),
        st("sensor.home_energy_manager_state", "HEATING", "mypyllant", 11.5 * H),
        st("binary_sensor.octopus_energy_intelligent_dispatching", "off", "octopus_energy", 7 * H),
        st("binary_sensor.octopus_energy_electricity_off_peak", "off", "octopus_energy", 5.5 * H),
        st("sensor.home_circuit_0_flow_temperature", "34.5", "mypyllant", 3 * M),
        st("sensor.octopus_energy_electricity_current_rate", "0.27", "octopus_energy", 20 * M),
        st("sensor.home_heat_pump_heating_energy", "1234.5", "mypyllant", 12 * H),
    ]


def test_settings_and_flags_that_hold_for_hours_are_current_while_their_integration_is_active(tmp_path):
    p = install(tmp_path, users_install())
    for sig, eid in [
        (Signal.HEATING_CURVE, "number.home_circuit_0_heating_curve"),
        (Signal.MIN_FLOW_SETPOINT, "number.home_circuit_0_min_flow_temperature_setpoint"),
        (Signal.HEAT_DEMAND_LIMIT_OUTDOOR, "number.home_circuit_0_heat_demand_limited_by_outside_temperature"),
        (Signal.DHW_OPERATING_MODE, "select.home_domestic_hot_water_0_operation_mode"),
        (Signal.DHW_BOOST_ACTIVE, "switch.home_domestic_hot_water_0_boost"),
        (Signal.HP_OPERATING_STATE, "sensor.home_energy_manager_state"),
        (Signal.INTELLIGENT_DISPATCHING, "binary_sensor.octopus_energy_intelligent_dispatching"),
        (Signal.OFF_PEAK_ACTIVE, "binary_sensor.octopus_energy_electricity_off_peak"),
        (Signal.HP_ELEC_HEATING_KWH, "sensor.home_heat_pump_heating_energy"),
    ]:
        f = fresh(p, sig, eid)
        assert f["stale"] is False, (sig, f)
    assert fresh(p, Signal.FLOW_TEMPERATURE, "sensor.home_circuit_0_flow_temperature")["stale"] is False


def test_a_silent_integration_makes_its_settings_stale(tmp_path):
    quiet = [st("number.home_circuit_0_heating_curve", "0.5", "mypyllant", 16 * H),
             st("sensor.home_circuit_0_flow_temperature", "34.5", "mypyllant", 3 * H)]
    p = install(tmp_path, quiet)
    f = fresh(p, Signal.HEATING_CURVE, "number.home_circuit_0_heating_curve")
    assert f["stale"] and "mypyllant" in f["stale_reason"]


def test_unavailable_is_always_stale(tmp_path):
    p = install(tmp_path, users_install() + [st("switch.home_dhw_boost_2", "unavailable", "mypyllant", 1 * H)])
    f = fresh(p, Signal.DHW_BOOST_ACTIVE, "switch.home_dhw_boost_2")
    assert f["stale"] and "unavailable" in f["stale_reason"]


def test_a_temperature_is_judged_by_age_counting_last_reported(tmp_path):
    p = install(tmp_path, [st("sensor.flow_steady", "35.0", "mypyllant", 3 * H, reported_ago=2 * M),
                           st("sensor.flow_stuck", "35.0", "mypyllant", 3 * H)])
    assert fresh(p, Signal.FLOW_TEMPERATURE, "sensor.flow_steady")["stale"] is False  # unchanged, but Home Assistant confirmed it
    stuck = fresh(p, Signal.FLOW_TEMPERATURE, "sensor.flow_stuck")
    assert stuck["stale"] and "3.0 hours" in stuck["stale_reason"]


# ---------------------------------------------------------------- confirmation readings


class FakeClient:
    def __init__(self, states):
        self._states = states

    async def states(self):
        return self._states


def provider(states):
    tel = HATelemetryProvider(FakeClient(states))
    tel.last_states = {s.entity_id: s for s in states}
    tel.mapping = [MappedSignal(Signal.FLOW_TEMPERATURE, "sensor.flow", 1.0, Quality.SENSOR, Provenance.HOME_ASSISTANT),
                   MappedSignal(Signal.HEATING_CURVE, "number.curve", 1.0, Quality.SENSOR, Provenance.HOME_ASSISTANT),
                   MappedSignal(Signal.HP_ELEC_HEATING_KWH, "sensor.elec", 1.0, Quality.SENSOR, Provenance.HOME_ASSISTANT)]
    return tel


def test_unchanged_values_get_a_confirmation_reading_but_counters_never_do():
    now = datetime.now(tz=UTC)
    states = [HAState("sensor.flow", "35.0", {}, last_updated=now - timedelta(hours=2), last_reported=now - timedelta(minutes=1), platform="mypyllant"),
              HAState("number.curve", "0.5", {}, last_updated=now - timedelta(hours=16), platform="mypyllant"),
              HAState("sensor.elec", "1234.5", {}, last_updated=now - timedelta(hours=12), last_reported=now - timedelta(minutes=1), platform="mypyllant")]
    tel = provider(states)
    out = asyncio.run(tel.read_measurements())
    by = {}
    for x in out:
        by.setdefault(x.signal, []).append(x.ts)
    assert len(by[Signal.FLOW_TEMPERATURE]) == 2 and max(by[Signal.FLOW_TEMPERATURE]) == now - timedelta(minutes=1)
    assert len(by[Signal.HEATING_CURVE]) == 2  # integration active, so the setting is confirmed now
    assert len(by[Signal.HP_ELEC_HEATING_KWH]) == 1  # counters are never repeated
    again = asyncio.run(tel.read_measurements())
    assert sum(1 for x in again if x.signal == Signal.FLOW_TEMPERATURE) == 1  # at most one confirmation per 30 minutes


def test_no_confirmation_when_the_integration_has_gone_quiet():
    now = datetime.now(tz=UTC)
    states = [HAState("number.curve", "0.5", {}, last_updated=now - timedelta(hours=16), platform="mypyllant"),
              HAState("sensor.flow", "35.0", {}, last_updated=now - timedelta(hours=5), platform="mypyllant")]
    out = asyncio.run(provider(states).read_measurements())
    assert sum(1 for x in out if x.signal == Signal.HEATING_CURVE) == 1
    assert sum(1 for x in out if x.signal == Signal.FLOW_TEMPERATURE) == 1  # no last_reported: a steady temperature is not vouched for


def test_parses_last_reported_from_home_assistant():
    from predheat.integrations.homeassistant.client import _parse_state

    s = _parse_state({"entity_id": "sensor.x", "state": "1", "attributes": {}, "last_changed": "2026-10-27T01:00:00+00:00",
                      "last_updated": "2026-10-27T01:00:00+00:00", "last_reported": "2026-10-27T17:59:00+00:00"})
    assert s.last_reported == datetime(2026, 10, 27, 17, 59, tzinfo=UTC)
