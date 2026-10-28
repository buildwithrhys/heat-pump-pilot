"""Capability discovery against an anonymised dump of the pilot installation."""

from __future__ import annotations

from heat_pump_pilot.domain import Quality, Signal
from heat_pump_pilot.integrations.homeassistant.discovery import discover_capabilities
from heat_pump_pilot.integrations.homeassistant.providers import mapping_from_report, measurement_from_state
from datetime import datetime, timezone


def _cap(report, sig):
    return next(c for c in report.capabilities if c.signal == sig)


def test_discovery_maps_core_vaillant_signals(pilot_states):
    report = discover_capabilities(pilot_states)
    assert _cap(report, Signal.INDOOR_TEMPERATURE).source_ref.endswith("zone_1_circuit_0_current_temperature")
    assert _cap(report, Signal.OUTDOOR_TEMPERATURE).source_ref.endswith("pilot_house_outdoor_temperature")
    assert _cap(report, Signal.FLOW_TEMPERATURE).source_ref.endswith("circuit_0_current_flow_temperature")
    assert _cap(report, Signal.HP_ELEC_HEATING_KWH).source_ref.endswith("arotherm_pro_consumed_electrical_energy_heating")
    assert _cap(report, Signal.HP_HEAT_HEATING_KWH).status == "available"
    assert _cap(report, Signal.HP_ELECTRICAL_POWER).source_ref.endswith("arotherm_pro_current_power")
    assert _cap(report, Signal.AUX_ELECTRICAL_POWER).source_ref.endswith("vwz_ai_current_power")
    assert _cap(report, Signal.DHW_TANK_TEMPERATURE).source_ref.endswith("tank_temperature")


def test_discovery_quality_classification(pilot_states):
    report = discover_capabilities(pilot_states)
    assert _cap(report, Signal.HP_ELEC_HEATING_KWH).quality == Quality.MANUFACTURER_ESTIMATED
    assert _cap(report, Signal.GRID_IMPORT_KWH).quality == Quality.METERED
    assert _cap(report, Signal.INDOOR_TEMPERATURE).quality == Quality.SENSOR
    assert _cap(report, Signal.HP_COMPRESSOR_ON).status == "unavailable"
    assert _cap(report, Signal.HP_DEFROST_ACTIVE).status == "unavailable"
    assert _cap(report, Signal.RETURN_TEMPERATURE).status == "unavailable"


def test_discovery_ignores_unavailable_and_unrelated_temperatures(pilot_states):
    report = discover_capabilities(pilot_states)
    indoor = _cap(report, Signal.INDOOR_TEMPERATURE)
    assert "model_y" not in (indoor.source_ref or "")
    assert all("wiser" not in a for a in indoor.alternatives)
    pv = _cap(report, Signal.PV_POWER)
    assert pv.status in ("degraded", "unavailable")  # inverter asleep in the fixture


def test_thermal_model_minimum_set_is_satisfied(pilot_states):
    report = discover_capabilities(pilot_states)
    assert report.missing_for_thermal_model() == []


def test_writable_controls_listed_but_not_used(pilot_states):
    report = discover_capabilities(pilot_states)
    ids = {w["entity_id"] for w in report.writable_controls}
    assert "climate.pilot_house_zone_1_circuit_0_climate" in ids
    assert "number.pilot_house_circuit_0_heating_curve" in ids
    assert any("energy manager" in w.lower() for w in report.warnings)


def test_mapping_applies_unit_scaling(pilot_states):
    report = discover_capabilities(pilot_states)
    mapping = {m.signal: m for m in mapping_from_report(report)}
    now = datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc)
    st = next(s for s in pilot_states if s.entity_id.endswith("arotherm_pro_consumed_electrical_energy_heating"))
    meas = measurement_from_state(mapping[Signal.HP_ELEC_HEATING_KWH], st, now)
    assert abs(meas.value - 8.1) < 1e-9  # Wh -> kWh
    st = next(s for s in pilot_states if s.entity_id.endswith("arotherm_pro_current_power"))
    meas = measurement_from_state(mapping[Signal.HP_ELECTRICAL_POWER], st, now)
    assert abs(meas.value - 0.735) < 1e-9  # W -> kW
    st = next(s for s in pilot_states if s.entity_id.endswith("import_current_rate"))
    meas = measurement_from_state(mapping[Signal.IMPORT_RATE_NOW], st, now)
    assert abs(meas.value - 6.6) < 1e-9  # GBP/kWh -> p/kWh
