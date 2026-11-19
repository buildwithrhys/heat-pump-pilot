"""The hot water entity is suggested the same way as the climate entity."""

from __future__ import annotations

from heat_pump_pilot.config import DataMode, Settings
from heat_pump_pilot.integrations.homeassistant.client import HAState
from heat_pump_pilot.service import build_platform


def _p(tmp_path, states):
    p = build_platform(Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'w.sqlite'}", sim_days=1))
    p.telemetry.last_states = {s.entity_id: s for s in states}
    return p


def _st(eid, platform):
    s = HAState(entity_id=eid, state="eco", attributes={})
    s.platform = platform
    return s


def test_single_vaillant_water_heater_is_suggested(tmp_path):
    p = _p(tmp_path, [_st("water_heater.home_domestic_hot_water_0", "mypyllant"), _st("water_heater.garage_immersion", "tuya")])
    assert p.climate_entities()["suggested_water_heater"] == "water_heater.home_domestic_hot_water_0"


def test_only_water_heater_is_suggested_and_ambiguity_is_not(tmp_path):
    assert _p(tmp_path, [_st("water_heater.tank", "tuya")]).climate_entities()["suggested_water_heater"] == "water_heater.tank"
    two = [_st("water_heater.a", "tuya"), _st("water_heater.b", "tuya")]
    assert _p(tmp_path, two).climate_entities()["suggested_water_heater"] is None
    assert _p(tmp_path, []).climate_entities()["suggested_water_heater"] is None
