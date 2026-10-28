"""Measurement quality and provenance.

Every number Heat Pump Pilot stores or displays carries a quality classification so
that measured, estimated, modelled and forecast values are never confused.
"""

from __future__ import annotations

from enum import StrEnum


class Quality(StrEnum):
    """How trustworthy a value is, and why."""

    METERED = "metered"
    """Directly metered by an independent meter (smart meter, CT clamp, heat meter)."""

    MANUFACTURER_ESTIMATED = "manufacturer_estimated"
    """Reported by the appliance's own controller, calculated internally rather than
    metered (for example Vaillant's consumed/generated energy figures)."""

    SENSOR = "sensor"
    """A direct physical sensor reading (temperature, pressure, humidity)."""

    MODEL_DERIVED = "model_derived"
    """Calculated by Heat Pump Pilot from other measurements plus a model or allocation rule."""

    FORECAST = "forecast"
    """A prediction about the future, issued at a specific time."""

    SIMULATED = "simulated"
    """Produced by the synthetic home in simulated-data mode."""

    UNAVAILABLE = "unavailable"
    """The installation does not expose this value."""

    @property
    def is_measured(self) -> bool:
        return self in {Quality.METERED, Quality.SENSOR}

    @property
    def is_estimate(self) -> bool:
        return self in {Quality.MANUFACTURER_ESTIMATED, Quality.MODEL_DERIVED}


class Provenance(StrEnum):
    """Which system supplied a value."""

    HOME_ASSISTANT = "home_assistant"
    VAILLANT_MYPYLLANT = "vaillant_mypyllant"
    VAILLANT_OFFICIAL_API = "vaillant_official_api"
    OCTOPUS_HA = "octopus_ha_integration"
    OCTOPUS_API = "octopus_api"
    OPENWEATHER_HA = "openweather_ha_integration"
    OPENWEATHER_API = "openweather_api"
    OPEN_METEO = "open_meteo"
    SOLCAST_HA = "solcast_ha_integration"
    SOLCAST_API = "solcast_api"
    HUAWEI_SOLAR_HA = "huawei_solar_ha_integration"
    PREDHEAT = "predheat"
    SIMULATOR = "simulator"
    USER = "user"
