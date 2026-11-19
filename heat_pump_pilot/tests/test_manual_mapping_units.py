"""Issue #4: a hand-picked sensor keeps the conversion chosen with it, whatever it looks like at start-up."""

from __future__ import annotations

import asyncio

import pytest

from heat_pump_pilot.domain import Quality, Signal
from heat_pump_pilot.integrations.homeassistant.client import HAState
from heat_pump_pilot.integrations.homeassistant.providers import HATelemetryProvider, override_scale

EID = "sensor.my_heat_pump_watts"  # a name discovery does not recognise


class Stub:
    def __init__(self, states):
        self._s = states

    async def states(self):
        return list(self._s)

    async def states_with_registry(self):
        return list(self._s)

    async def config_entries(self):
        return []


def _start(state: str, attrs: dict, saved_scale: float = 0.001, quality: str = "manufacturer_estimated"):
    st = HAState(EID, state, attrs)
    st.platform = "mypyllant"
    p = HATelemetryProvider(Stub([st]))
    p.set_overrides({"hp_electrical_power": {"source_ref": EID, "unit_scale": saved_scale, "quality": quality}})
    report = asyncio.run(p.discover())
    cap = next(c for c in report.capabilities if c.signal == Signal.HP_ELECTRICAL_POWER)
    m = next(x for x in p.mapping if x.signal == Signal.HP_ELECTRICAL_POWER)
    return cap, m


@pytest.mark.parametrize("state,attrs", [
    ("342", {"unit_of_measurement": "W", "device_class": "power"}),
    ("unavailable", {"unit_of_measurement": "W"}),
    ("unavailable", {"friendly_name": "no unit while unavailable"}),  # the issue #4 case: used to give a factor of 1
])
def test_saved_factor_is_used_at_start_up(state, attrs):
    cap, m = _start(state, attrs)
    assert m.unit_scale == 0.001
    assert m.quality == Quality.MANUFACTURER_ESTIMATED  # was left as "unavailable"
    assert cap.notes.startswith("chosen by you")


def test_a_real_unit_change_is_followed_and_noted():
    cap, m = _start("0.342", {"unit_of_measurement": "kW", "device_class": "power"})
    assert m.unit_scale == 1.0 and "its unit is now kW" in cap.notes


def test_override_scale_rules():
    assert override_scale(Signal.HP_ELECTRICAL_POWER, 0.001, None) == (0.001, "")
    assert override_scale(Signal.HP_ELECTRICAL_POWER, 0.001, "°C")[0] == 0.001  # not convertible: keep the saved factor
    assert override_scale(Signal.HP_ELEC_HEATING_KWH, 0.001, "Wh") == (0.001, "")
    assert override_scale(Signal.HP_ELEC_HEATING_KWH, 0.001, "kWh")[0] == 1.0
    assert override_scale(Signal.INDOOR_TEMPERATURE, 1.0, "°C") == (1.0, "")  # nothing to convert


def test_choosing_a_sensor_with_no_unit_is_refused(tmp_path):
    from heat_pump_pilot.config import DataMode, Settings
    from heat_pump_pilot.db import session_scope
    from heat_pump_pilot.service import build_platform

    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'u.sqlite'}", sim_days=1)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    st = HAState(EID, "unavailable", {"friendly_name": "x"})
    p.telemetry = HATelemetryProvider(Stub([st]))
    p.capability_report = asyncio.run(p.telemetry.discover())
    with session_scope(settings.database_url) as s:
        with pytest.raises(ValueError, match="isn't reporting a unit"):
            asyncio.run(p.set_mapping(s, "hp_electrical_power", EID))
        # a temperature needs no conversion, so a missing unit is fine there
        asyncio.run(p.set_mapping(s, "indoor_temperature", EID))
