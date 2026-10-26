"""Pydantic schemas exchanged between integrations, storage and the API.

All timestamps are timezone-aware and stored in UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .quality import Provenance, Quality
from .signals import Signal


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class Measurement(BaseModel):
    """One observation of one signal."""

    signal: Signal
    ts: datetime
    value: float | None = None
    text: str | None = None
    quality: Quality
    provenance: Provenance
    source_ref: str | None = Field(default=None, description="Native id, e.g. HA entity_id")
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


class RateInterval(BaseModel):
    """A unit price applicable between start and end (half-open)."""

    start: datetime
    end: datetime
    rate_p_per_kwh: float
    tariff_code: str
    is_export: bool = False
    # IOG: True if the integration/API rewrote this slot to off-peak because of a dispatch
    is_intelligent_adjusted: bool = False
    is_capped: bool = False
    provenance: Provenance = Provenance.OCTOPUS_HA

    @field_validator("start", "end")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


class StandingCharge(BaseModel):
    start: datetime
    end: datetime | None
    pence_per_day: float
    tariff_code: str
    is_export: bool = False

    @field_validator("start", "end")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _ensure_utc(v)


class DispatchKind(StrEnum):
    PLANNED = "planned"
    STARTED = "started"
    COMPLETED = "completed"


class Dispatch(BaseModel):
    """An Intelligent Octopus smart-charge dispatch window."""

    kind: DispatchKind
    start: datetime
    end: datetime
    charge_kwh: float | None = None
    source: str | None = None  # SMART, BOOST, bump-charge, unknown
    location: str | None = None
    observed_at: datetime

    @field_validator("start", "end", "observed_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


class TariffSnapshot(BaseModel):
    """What we know about the account tariff at a point in time."""

    observed_at: datetime
    import_tariff_code: str
    export_tariff_code: str | None = None
    region: str | None = None
    product: str | None = None
    rates: list[RateInterval] = Field(default_factory=list)
    standing_charges: list[StandingCharge] = Field(default_factory=list)
    dispatches: list[Dispatch] = Field(default_factory=list)


class WeatherPoint(BaseModel):
    """A forecast or observation for one hour."""

    issued_at: datetime
    valid_at: datetime
    temperature_c: float
    wind_speed_ms: float | None = None
    humidity_pct: float | None = None
    cloud_cover_pct: float | None = None
    condition: str | None = None
    quality: Quality = Quality.FORECAST
    provenance: Provenance

    @field_validator("issued_at", "valid_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


class SolarPoint(BaseModel):
    issued_at: datetime
    period_start: datetime
    period_minutes: int = 30
    pv_estimate_kw: float
    pv_estimate10_kw: float | None = None
    pv_estimate90_kw: float | None = None
    ghi_w_m2: float | None = None
    provenance: Provenance

    @field_validator("issued_at", "period_start")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


class DiscoveredCapability(BaseModel):
    signal: Signal
    status: str  # available | degraded | unavailable
    quality: Quality
    source_ref: str | None = None
    unit: str | None = None
    confidence: float = Field(ge=0, le=1)
    notes: str = ""
    alternatives: list[str] = Field(default_factory=list)


class CapabilityReport(BaseModel):
    generated_at: datetime
    provenance: Provenance
    capabilities: list[DiscoveredCapability]
    writable_controls: list[dict[str, Any]] = Field(default_factory=list)
    integrations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def available(self) -> list[Signal]:
        return [c.signal for c in self.capabilities if c.status == "available"]

    def missing_for_thermal_model(self) -> list[Signal]:
        from .signals import minimum_thermal_model_signals

        have = set(self.available())
        return [s for s in minimum_thermal_model_signals() if s not in have]
