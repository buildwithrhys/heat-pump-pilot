"""The canonical signal catalogue (data dictionary).

A *signal* is a named, unit-fixed quantity Heat Pump Pilot understands, independent of
where it comes from. Integrations map their native entities onto signals.
Discovery uses the hints below to propose mappings; the user can override them.

Units are fixed per signal: kW, kWh, W/K, degC, pence/kWh, percent, bar, hours.
Energy counters are stored as cumulative kWh (``total_increasing``) and are
converted to interval energy downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .quality import Quality


class Signal(StrEnum):
    # --- Indoor / building
    INDOOR_TEMPERATURE = "indoor_temperature"
    INDOOR_HUMIDITY = "indoor_humidity"
    INDOOR_SETPOINT = "indoor_setpoint"

    # --- Outdoor
    OUTDOOR_TEMPERATURE = "outdoor_temperature"

    # --- Heat pump hydraulics
    FLOW_TEMPERATURE = "flow_temperature"
    FLOW_TEMPERATURE_SETPOINT = "flow_temperature_setpoint"
    RETURN_TEMPERATURE = "return_temperature"
    HEATING_CURVE = "heating_curve"
    MIN_FLOW_SETPOINT = "min_flow_setpoint"
    HEAT_DEMAND_LIMIT_OUTDOOR = "heat_demand_limit_outdoor"
    SYSTEM_WATER_PRESSURE = "system_water_pressure"

    # --- Heat pump operation
    HP_ELECTRICAL_POWER = "hp_electrical_power"  # kW, instantaneous
    HP_OPERATING_STATE = "hp_operating_state"  # text enum: HEATING, DHW, STANDBY...
    HP_COMPRESSOR_ON = "hp_compressor_on"
    HP_DEFROST_ACTIVE = "hp_defrost_active"
    HP_ON_OFF_CYCLES = "hp_on_off_cycles"
    HP_OPERATION_HOURS = "hp_operation_hours"

    # --- Energy counters (cumulative kWh)
    HP_ELEC_HEATING_KWH = "hp_elec_heating_kwh"
    HP_ELEC_DHW_KWH = "hp_elec_dhw_kwh"
    HP_HEAT_HEATING_KWH = "hp_heat_heating_kwh"
    HP_HEAT_DHW_KWH = "hp_heat_dhw_kwh"
    HP_ENV_HEATING_KWH = "hp_env_heating_kwh"
    HP_ENV_DHW_KWH = "hp_env_dhw_kwh"
    AUX_ELEC_HEATING_KWH = "aux_elec_heating_kwh"  # immersion / backup heater
    AUX_ELEC_DHW_KWH = "aux_elec_dhw_kwh"
    AUX_ELECTRICAL_POWER = "aux_electrical_power"

    # --- Domestic hot water
    DHW_TANK_TEMPERATURE = "dhw_tank_temperature"
    DHW_SETPOINT = "dhw_setpoint"
    DHW_OPERATING_MODE = "dhw_operating_mode"
    DHW_BOOST_ACTIVE = "dhw_boost_active"

    # --- Whole home electricity
    GRID_IMPORT_KWH = "grid_import_kwh"
    GRID_EXPORT_KWH = "grid_export_kwh"
    GRID_POWER = "grid_power"  # kW, +import / -export
    HOME_LOAD_POWER = "home_load_power"
    PV_GENERATION_KWH = "pv_generation_kwh"
    PV_POWER = "pv_power"
    BATTERY_SOC = "battery_soc"
    BATTERY_POWER = "battery_power"
    BATTERY_CHARGE_KWH = "battery_charge_kwh"
    BATTERY_DISCHARGE_KWH = "battery_discharge_kwh"
    EV_CHARGER_POWER = "ev_charger_power"
    EV_CHARGER_SESSION_KWH = "ev_charger_session_kwh"

    # --- Tariff
    IMPORT_RATE_NOW = "import_rate_now"  # p/kWh inc VAT
    EXPORT_RATE_NOW = "export_rate_now"
    STANDING_CHARGE_NOW = "standing_charge_now"  # p/day inc VAT
    OFF_PEAK_ACTIVE = "off_peak_active"
    INTELLIGENT_DISPATCHING = "intelligent_dispatching"

    # --- Weather observations
    WEATHER_TEMPERATURE = "weather_temperature"
    WEATHER_WIND_SPEED = "weather_wind_speed"  # m/s
    WEATHER_HUMIDITY = "weather_humidity"
    WEATHER_CLOUD_COVER = "weather_cloud_cover"
    SOLAR_IRRADIANCE = "solar_irradiance"  # W/m2, from Open-Meteo


class SignalKind(StrEnum):
    INSTANT = "instant"  # a value at a moment in time
    CUMULATIVE = "cumulative"  # a monotonically increasing counter
    STATE = "state"  # text state


@dataclass(frozen=True)
class SignalSpec:
    signal: Signal
    unit: str
    kind: SignalKind
    description: str
    default_quality: Quality = Quality.SENSOR
    required_for_thermal_model: bool = False
    # Discovery hints: substrings matched (case-insensitively) against HA entity_id,
    # plus optional device_class / unit filters. Earlier hints score higher.
    hints: tuple[str, ...] = ()
    device_class: str | None = None
    accept_units: tuple[str, ...] = ()
    # Multiply native value by this to get canonical units (e.g. Wh -> kWh = 0.001)
    unit_scale: dict[str, float] = field(default_factory=dict)
    # Entity domains that may carry this signal. Power, temperature and energy are
    # sensors; booleans are binary sensors. A switch is never a reading.
    domains: tuple[str, ...] = ("sensor",)
    # Every substring in ``require`` must appear in the entity id; any in ``exclude`` rejects it.
    require: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


_W_TO_KW = {"W": 0.001, "kW": 1.0}
_WH_TO_KWH = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}
_RATE = {"GBP/kWh": 100.0, "p/kWh": 1.0, "£/kWh": 100.0}

SIGNAL_CATALOGUE: dict[Signal, SignalSpec] = {
    s.signal: s
    for s in [
        SignalSpec(Signal.INDOOR_TEMPERATURE, "degC", SignalKind.INSTANT,
                   "Representative indoor air temperature", required_for_thermal_model=True,
                   hints=("zone_1_circuit_0_current_temperature", "zone_0_current_temperature",
                          "room_temperature", "indoor_temperature", "current_temperature"),
                   device_class="temperature", accept_units=("°C",)),
        SignalSpec(Signal.INDOOR_HUMIDITY, "percent", SignalKind.INSTANT, "Indoor relative humidity",
                   hints=("zone_1_circuit_0_humidity", "zone_0_humidity", "indoor_humidity"),
                   device_class="humidity"),
        SignalSpec(Signal.INDOOR_SETPOINT, "degC", SignalKind.INSTANT, "Active room setpoint",
                   hints=("desired_heating_temperature", "desired_temperature", "target_temperature"),
                   device_class="temperature"),
        SignalSpec(Signal.OUTDOOR_TEMPERATURE, "degC", SignalKind.INSTANT,
                   "Outdoor temperature from the heat pump's own sensor", required_for_thermal_model=True,
                   hints=("glendale_house_outdoor_temperature", "outdoor_temperature", "outside_temperature"),
                   device_class="temperature"),
        SignalSpec(Signal.FLOW_TEMPERATURE, "degC", SignalKind.INSTANT, "Heating circuit flow temperature",
                   hints=("current_flow_temperature", "flow_temperature"), device_class="temperature"),
        SignalSpec(Signal.FLOW_TEMPERATURE_SETPOINT, "degC", SignalKind.INSTANT,
                   "Flow temperature target from weather compensation",
                   hints=("flow_temperature_setpoint",), device_class="temperature"),
        SignalSpec(Signal.RETURN_TEMPERATURE, "degC", SignalKind.INSTANT, "Heating circuit return temperature",
                   hints=("return_temperature",), device_class="temperature"),
        SignalSpec(Signal.HEATING_CURVE, "ratio", SignalKind.INSTANT, "Weather compensation curve",
                   hints=("circuit_0_heating_curve", "heating_curve"), domains=("sensor", "number")),
        SignalSpec(Signal.MIN_FLOW_SETPOINT, "degC", SignalKind.INSTANT, "Minimum flow setpoint",
                   hints=("min_flow_temperature_setpoint",), domains=("sensor", "number")),
        SignalSpec(Signal.HEAT_DEMAND_LIMIT_OUTDOOR, "degC", SignalKind.INSTANT,
                   "Outdoor temperature above which heating is suppressed",
                   hints=("heat_demand_limited_by_outside_temperature",), domains=("sensor", "number")),
        SignalSpec(Signal.SYSTEM_WATER_PRESSURE, "bar", SignalKind.INSTANT, "System water pressure",
                   hints=("system_water_pressure",), device_class="pressure"),
        SignalSpec(Signal.HP_ELECTRICAL_POWER, "kW", SignalKind.INSTANT,
                   "Heat pump outdoor unit electrical power", default_quality=Quality.MANUFACTURER_ESTIMATED,
                   hints=("arotherm_pro_current_power", "heat_pump_current_power", "hp_power"),
                   device_class="power", unit_scale=_W_TO_KW),
        SignalSpec(Signal.AUX_ELECTRICAL_POWER, "kW", SignalKind.INSTANT,
                   "Indoor unit / backup heater electrical power", default_quality=Quality.MANUFACTURER_ESTIMATED,
                   hints=("vwz_ai_current_power", "backup_heater_power", "immersion_power"),
                   device_class="power", unit_scale=_W_TO_KW),
        SignalSpec(Signal.HP_OPERATING_STATE, "state", SignalKind.STATE,
                   "Controller operating state (HEATING, DHW, STANDBY, ...)",
                   hints=("energy_manager_state", "circuit_0_state", "operating_state")),
        SignalSpec(Signal.HP_COMPRESSOR_ON, "bool", SignalKind.STATE, "Compressor running",
                   default_quality=Quality.UNAVAILABLE, hints=("compressor",), domains=("binary_sensor",)),
        SignalSpec(Signal.HP_DEFROST_ACTIVE, "bool", SignalKind.STATE, "Defrost in progress",
                   default_quality=Quality.UNAVAILABLE, hints=("defrost",), domains=("binary_sensor",)),
        SignalSpec(Signal.HP_ON_OFF_CYCLES, "count", SignalKind.CUMULATIVE, "Compressor on/off cycles",
                   default_quality=Quality.MANUFACTURER_ESTIMATED, hints=("arotherm_pro_on_off_cycles", "on_off_cycles")),
        SignalSpec(Signal.HP_OPERATION_HOURS, "h", SignalKind.CUMULATIVE, "Compressor operating hours",
                   default_quality=Quality.MANUFACTURER_ESTIMATED, hints=("arotherm_pro_operation_time", "operation_time")),
        SignalSpec(Signal.HP_ELEC_HEATING_KWH, "kWh", SignalKind.CUMULATIVE,
                   "Electricity consumed for space heating", default_quality=Quality.MANUFACTURER_ESTIMATED,
                   required_for_thermal_model=True,
                   hints=("arotherm_pro_consumed_electrical_energy_heating",), device_class="energy",
                   unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.HP_ELEC_DHW_KWH, "kWh", SignalKind.CUMULATIVE,
                   "Electricity consumed for hot water", default_quality=Quality.MANUFACTURER_ESTIMATED,
                   hints=("arotherm_pro_consumed_electrical_energy_domestic_hot_water",), device_class="energy",
                   unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.HP_HEAT_HEATING_KWH, "kWh", SignalKind.CUMULATIVE,
                   "Heat delivered for space heating", default_quality=Quality.MANUFACTURER_ESTIMATED,
                   required_for_thermal_model=True,
                   hints=("arotherm_pro_heat_generated_heating",), device_class="energy", unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.HP_HEAT_DHW_KWH, "kWh", SignalKind.CUMULATIVE,
                   "Heat delivered for hot water", default_quality=Quality.MANUFACTURER_ESTIMATED,
                   hints=("arotherm_pro_heat_generated_domestic_hot_water",), device_class="energy",
                   unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.HP_ENV_HEATING_KWH, "kWh", SignalKind.CUMULATIVE,
                   "Environmental energy harvested for heating", default_quality=Quality.MANUFACTURER_ESTIMATED,
                   hints=("arotherm_pro_earned_environment_energy_heating",), device_class="energy",
                   unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.HP_ENV_DHW_KWH, "kWh", SignalKind.CUMULATIVE,
                   "Environmental energy harvested for hot water", default_quality=Quality.MANUFACTURER_ESTIMATED,
                   hints=("arotherm_pro_earned_environment_energy_domestic_hot_water",), device_class="energy",
                   unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.AUX_ELEC_HEATING_KWH, "kWh", SignalKind.CUMULATIVE,
                   "Backup/immersion electricity for heating", default_quality=Quality.MANUFACTURER_ESTIMATED,
                   hints=("vwz_ai_consumed_electrical_energy_heating",), device_class="energy", unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.AUX_ELEC_DHW_KWH, "kWh", SignalKind.CUMULATIVE,
                   "Backup/immersion electricity for hot water", default_quality=Quality.MANUFACTURER_ESTIMATED,
                   hints=("vwz_ai_consumed_electrical_energy_domestic_hot_water",), device_class="energy",
                   unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.DHW_TANK_TEMPERATURE, "degC", SignalKind.INSTANT, "Hot water cylinder temperature",
                   hints=("domestic_hot_water_0_tank_temperature", "tank_temperature", "cylinder_temperature"),
                   device_class="temperature"),
        SignalSpec(Signal.DHW_SETPOINT, "degC", SignalKind.INSTANT, "Hot water setpoint",
                   hints=("domestic_hot_water_0_setpoint", "dhw_setpoint"), device_class="temperature"),
        SignalSpec(Signal.DHW_OPERATING_MODE, "state", SignalKind.STATE, "Hot water operating mode",
                   hints=("domestic_hot_water_0_operation_mode",)),
        SignalSpec(Signal.DHW_BOOST_ACTIVE, "bool", SignalKind.STATE, "Hot water boost active",
                   hints=("domestic_hot_water_0_boost",), domains=("binary_sensor", "switch")),
        SignalSpec(Signal.GRID_IMPORT_KWH, "kWh", SignalKind.CUMULATIVE, "Grid import energy",
                   default_quality=Quality.METERED,
                   hints=("current_total_consumption", "power_meter_consumption", "grid_import"),
                   device_class="energy", unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.GRID_EXPORT_KWH, "kWh", SignalKind.CUMULATIVE, "Grid export energy",
                   default_quality=Quality.METERED,
                   hints=("power_meter_exported", "current_total_export", "grid_export"),
                   device_class="energy", unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.GRID_POWER, "kW", SignalKind.INSTANT, "Grid power (+import, -export)",
                   default_quality=Quality.METERED,
                   hints=("current_demand", "power_meter_active_power", "grid_power"),
                   device_class="power", unit_scale=_W_TO_KW),
        SignalSpec(Signal.HOME_LOAD_POWER, "kW", SignalKind.INSTANT, "Whole-home load power",
                   default_quality=Quality.MODEL_DERIVED, hints=("home_consumption", "house_load"),
                   unit_scale=_W_TO_KW),
        SignalSpec(Signal.PV_GENERATION_KWH, "kWh", SignalKind.CUMULATIVE, "PV generation energy",
                   default_quality=Quality.METERED,
                   hints=("inverter_total_yield", "pv_total", "solar_energy"), device_class="energy",
                   unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.PV_POWER, "kW", SignalKind.INSTANT, "PV power", default_quality=Quality.METERED,
                   hints=("inverter_input_power", "pv_power", "solar_power"), device_class="power",
                   unit_scale=_W_TO_KW),
        SignalSpec(Signal.BATTERY_SOC, "percent", SignalKind.INSTANT, "Battery state of charge",
                   hints=("batteries_state_of_capacity", "battery_soc"), device_class="battery"),
        SignalSpec(Signal.BATTERY_POWER, "kW", SignalKind.INSTANT, "Battery power (+charge, -discharge)",
                   hints=("batteries_charge_discharge_power", "battery_power"), device_class="power",
                   unit_scale=_W_TO_KW),
        SignalSpec(Signal.BATTERY_CHARGE_KWH, "kWh", SignalKind.CUMULATIVE, "Battery charge energy",
                   hints=("batteries_total_charge",), device_class="energy", unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.BATTERY_DISCHARGE_KWH, "kWh", SignalKind.CUMULATIVE, "Battery discharge energy",
                   hints=("batteries_total_discharge",), device_class="energy", unit_scale=_WH_TO_KWH),
        SignalSpec(Signal.EV_CHARGER_POWER, "kW", SignalKind.INSTANT, "EV charger power",
                   hints=("predbat_hypervolt", "hypervolt", "ohme_home_go_power", "ev_charger_power"), device_class="power",
                   unit_scale=_W_TO_KW, require=("power",), exclude=("session", "energy", "max_", "limit")),
        # Octopus entities are anchored to the integration prefix; the import meter's
        # entities never contain "export", the export meter's always do.
        SignalSpec(Signal.IMPORT_RATE_NOW, "p/kWh", SignalKind.INSTANT, "Current import unit rate inc VAT",
                   default_quality=Quality.METERED, hints=("_current_rate",), unit_scale=_RATE,
                   require=("octopus_energy_electricity",), exclude=("export", "previous", "next", "calculated")),
        SignalSpec(Signal.EXPORT_RATE_NOW, "p/kWh", SignalKind.INSTANT, "Current export unit rate",
                   hints=("export_current_rate",), unit_scale=_RATE,
                   require=("octopus_energy_electricity",), exclude=("previous", "next")),
        SignalSpec(Signal.STANDING_CHARGE_NOW, "p/day", SignalKind.INSTANT, "Current standing charge inc VAT",
                   hints=("current_standing_charge",), unit_scale={"GBP": 100.0, "GBP/day": 100.0, "p": 1.0, "p/day": 1.0},
                   require=("octopus_energy_electricity",), exclude=("export",)),
        SignalSpec(Signal.OFF_PEAK_ACTIVE, "bool", SignalKind.STATE, "Off-peak period active",
                   hints=("_off_peak",), domains=("binary_sensor",),
                   require=("octopus_energy_electricity",), exclude=("export",)),
        SignalSpec(Signal.INTELLIGENT_DISPATCHING, "bool", SignalKind.STATE,
                   "Intelligent Octopus dispatch in progress", hints=("intelligent_dispatching",), domains=("binary_sensor",)),
        SignalSpec(Signal.WEATHER_TEMPERATURE, "degC", SignalKind.INSTANT, "Weather service air temperature",
                   hints=("openweathermap_temperature",), device_class="temperature"),
        SignalSpec(Signal.WEATHER_WIND_SPEED, "m/s", SignalKind.INSTANT, "Wind speed",
                   hints=("openweathermap_wind_speed",), device_class="wind_speed",
                   unit_scale={"m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.44704}),
        SignalSpec(Signal.WEATHER_HUMIDITY, "percent", SignalKind.INSTANT, "Outdoor humidity",
                   hints=("openweathermap_humidity",), device_class="humidity"),
        SignalSpec(Signal.WEATHER_CLOUD_COVER, "percent", SignalKind.INSTANT, "Cloud cover",
                   hints=("openweathermap_cloud_coverage",)),
        SignalSpec(Signal.SOLAR_IRRADIANCE, "W/m2", SignalKind.INSTANT, "Solar irradiance on a horizontal surface (Open-Meteo)",
                   default_quality=Quality.MODEL_DERIVED, hints=("solar_irradiance", "global_horizontal_irradiance"),
                   accept_units=("W/m²", "W/m2")),
    ]
}


def minimum_thermal_model_signals() -> list[Signal]:
    """The smallest set of signals needed before heat-loss learning can begin."""
    return [s for s, spec in SIGNAL_CATALOGUE.items() if spec.required_for_thermal_model]


# How to judge whether a reading is current. Telemetry keeps changing while the integration is
# alive, so its age matters. Settings, modes, on/off flags, rates and counters can legitimately
# hold the same value for days; Home Assistant then leaves last_updated alone, so their age says
# nothing about health. They count as current unless the entity is unavailable or its whole
# integration has gone quiet.
TELEMETRY_SIGNALS = frozenset({
    Signal.INDOOR_TEMPERATURE, Signal.INDOOR_HUMIDITY, Signal.OUTDOOR_TEMPERATURE, Signal.FLOW_TEMPERATURE,
    Signal.RETURN_TEMPERATURE, Signal.DHW_TANK_TEMPERATURE, Signal.SYSTEM_WATER_PRESSURE, Signal.WEATHER_TEMPERATURE,
    Signal.WEATHER_HUMIDITY, Signal.HOME_LOAD_POWER, Signal.GRID_POWER,
})


def freshness_class(sig: Signal) -> str:
    """``telemetry``, ``counter`` or ``setting`` (settings, modes, flags, rates: values that may hold for days)."""
    if sig in TELEMETRY_SIGNALS:
        return "telemetry"
    if SIGNAL_CATALOGUE[sig].kind == SignalKind.CUMULATIVE:
        return "counter"
    return "setting"
