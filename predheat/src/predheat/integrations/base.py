"""Provider interfaces.

These are structural Protocols so that live adapters, Home Assistant adapters
and the simulator are interchangeable in tests and in the collector.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..domain import (
    CapabilityReport,
    Measurement,
    RateInterval,
    SolarPoint,
    TariffSnapshot,
    WeatherPoint,
)


@dataclass
class ProviderHealth:
    provider: str
    ok: bool
    last_success_at: datetime | None = None
    last_error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TelemetryProvider(Protocol):
    """Supplies current measurements for mapped signals (heat pump, meters, sensors)."""

    name: str

    async def discover(self) -> CapabilityReport: ...

    async def read_measurements(self) -> list[Measurement]: ...

    async def read_history(self, start: datetime, end: datetime) -> list[Measurement]:
        """Historic measurements, where the source keeps any."""
        ...

    async def health(self) -> ProviderHealth: ...


@runtime_checkable
class TariffProvider(Protocol):
    name: str

    async def snapshot(self) -> TariffSnapshot: ...

    async def rate_history(self, start: datetime, end: datetime) -> list[RateInterval]:
        """Historic rates where the source can supply them (direct APIs, simulator).

        The Home Assistant Octopus integration only publishes three days, so its
        provider returns an empty list and PredHeat relies on its own retained history.
        """
        ...

    async def health(self) -> ProviderHealth: ...


@runtime_checkable
class WeatherProvider(Protocol):
    name: str

    async def forecast(self, hours: int = 48) -> list[WeatherPoint]: ...

    async def observation(self) -> WeatherPoint | None: ...

    async def health(self) -> ProviderHealth: ...


@runtime_checkable
class SolarProvider(Protocol):
    name: str

    async def forecast(self, hours: int = 48) -> list[SolarPoint]: ...

    async def health(self) -> ProviderHealth: ...


@dataclass
class ControlCapability:
    """A control the equipment exposes. Nothing here is callable until Phase 5."""

    name: str
    target: str
    kind: str  # setpoint, mode, switch, number
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    notes: str = ""


@runtime_checkable
class ControlAdapter(Protocol):
    """Deliberately minimal. Phase 0 to 4 adapters only *describe* capabilities."""

    name: str

    async def capabilities(self) -> list[ControlCapability]: ...

    async def ownership_verified(self) -> bool:
        """True only when no other energy manager (EnergyPLUS, EEBUS) holds control."""
        ...
