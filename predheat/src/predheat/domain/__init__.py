"""Canonical domain model shared by every integration and engine.

Nothing in this package knows about Home Assistant, Octopus or Vaillant.
"""

from .quality import Quality, Provenance
from .signals import Signal, SIGNAL_CATALOGUE, SignalSpec, TELEMETRY_SIGNALS, freshness_class
from .schemas import (
    Measurement,
    RateInterval,
    StandingCharge,
    Dispatch,
    DispatchKind,
    WeatherPoint,
    SolarPoint,
    TariffSnapshot,
    DiscoveredCapability,
    CapabilityReport,
)

__all__ = [
    "Quality",
    "Provenance",
    "Signal",
    "SIGNAL_CATALOGUE",
    "SignalSpec",
    "TELEMETRY_SIGNALS",
    "freshness_class",
    "Measurement",
    "RateInterval",
    "StandingCharge",
    "Dispatch",
    "DispatchKind",
    "WeatherPoint",
    "SolarPoint",
    "TariffSnapshot",
    "DiscoveredCapability",
    "CapabilityReport",
]
