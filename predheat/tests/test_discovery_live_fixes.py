"""Mapping corrections verified against the live installation on 26 October 2026."""

from __future__ import annotations

from datetime import datetime, timezone

from predheat.domain import Signal
from predheat.integrations.homeassistant.client import HAState
from predheat.integrations.homeassistant.discovery import discover_capabilities

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def st(entity_id: str, state: str, unit: str | None = None, device_class: str | None = None, platform: str | None = None) -> HAState:
    attrs = {}
    if unit:
        attrs["unit_of_measurement"] = unit
    if device_class:
        attrs["device_class"] = device_class
    s = HAState(entity_id=entity_id, state=state, attributes=attrs, last_updated=NOW)
    s.platform = platform
    return s


LIVE = [
    # EV charger: decoy switch, old faulty charger, the live one, and its energy sibling
    st("switch.hypervolt_charger_pre_release", "off", platform="hypervolt_charger"),
    st("sensor.predbat_hypervolt_216455233649206739_power", "unknown", "W", "power"),
    st("sensor.predbat_hypervolt_216454728590922601_power", "6952", "W", "power"),
    st("sensor.predbat_hypervolt_216454728590922601_session_energy", "12.4", "kWh", "energy"),
    # rates: decoy with unit P, real import and export, standing charge
    st("sensor.calculated_current_rate", "6.6", "P"),
    st("sensor.octopus_energy_electricity_19l2379200_2100040663425_current_rate", "0.066", "GBP/kWh", platform="octopus_energy"),
    st("sensor.octopus_energy_electricity_19l2379200_2100040663425_previous_rate", "0.29", "GBP/kWh", platform="octopus_energy"),
    st("sensor.octopus_energy_electricity_19l2379200_2100041772612_export_current_rate", "0.12", "GBP/kWh", platform="octopus_energy"),
    st("sensor.octopus_energy_electricity_19l2379200_2100040663425_current_standing_charge", "0.543619", "GBP/day", platform="octopus_energy"),
    # off-peak: Huawei energy counter decoy and the real binary sensor
    st("sensor.meet_your_maker_negative_active_energy_off_peak", "1234.5", "kWh", "energy", platform="huawei_solar"),
    st("binary_sensor.octopus_energy_electricity_19l2379200_2100040663425_off_peak", "on", platform="octopus_energy"),
]


def _src(report, sig):
    return next(c for c in report.capabilities if c.signal == sig).source_ref


def test_ev_charger_power_is_the_live_hypervolt_power_sensor():
    r = discover_capabilities(LIVE, now=NOW)
    assert _src(r, Signal.EV_CHARGER_POWER) == "sensor.predbat_hypervolt_216454728590922601_power"


def test_import_rate_is_the_octopus_import_meter_not_the_calculated_sensor():
    r = discover_capabilities(LIVE, now=NOW)
    assert _src(r, Signal.IMPORT_RATE_NOW) == "sensor.octopus_energy_electricity_19l2379200_2100040663425_current_rate"
    assert _src(r, Signal.EXPORT_RATE_NOW) == "sensor.octopus_energy_electricity_19l2379200_2100041772612_export_current_rate"
    assert _src(r, Signal.STANDING_CHARGE_NOW) == "sensor.octopus_energy_electricity_19l2379200_2100040663425_current_standing_charge"


def test_off_peak_is_the_binary_sensor_not_a_meter_counter():
    r = discover_capabilities(LIVE, now=NOW)
    assert _src(r, Signal.OFF_PEAK_ACTIVE) == "binary_sensor.octopus_energy_electricity_19l2379200_2100040663425_off_peak"


def test_switches_and_counters_never_carry_power_or_boolean_signals():
    r = discover_capabilities(LIVE, now=NOW)
    for c in r.capabilities:
        if c.source_ref:
            assert not c.source_ref.startswith("switch."), c
            if c.signal in (Signal.EV_CHARGER_POWER, Signal.OFF_PEAK_ACTIVE, Signal.IMPORT_RATE_NOW):
                assert "active_energy" not in c.source_ref and not c.source_ref.endswith("_energy")
