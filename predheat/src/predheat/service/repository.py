"""Persistence helpers. Idempotent upserts keyed on natural keys."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import models as m
from ..db.session import as_utc
from ..domain import Dispatch, Measurement, RateInterval, SolarPoint, StandingCharge, WeatherPoint


def ensure_property(session: Session, name: str, timezone_name: str, **kwargs) -> m.Property:
    prop = session.scalar(select(m.Property).where(m.Property.name == name))
    if prop is None:
        prop = m.Property(name=name, timezone=timezone_name, created_at=datetime.now(tz=timezone.utc), **kwargs)
        session.add(prop)
        session.flush()
    return prop


def upsert_measurements(session: Session, property_id: int, measurements: list[Measurement]) -> int:
    """Insert measurements that are not already present. Returns inserted count."""
    if not measurements:
        return 0
    now = datetime.now(tz=timezone.utc)
    inserted = 0
    # Fetch existing keys for the affected time range to avoid per-row queries
    ts_min = min(x.ts for x in measurements)
    ts_max = max(x.ts for x in measurements)
    existing = set(
        session.execute(
            select(m.SensorMeasurement.signal, m.SensorMeasurement.ts, m.SensorMeasurement.source_ref)
            .where(m.SensorMeasurement.property_id == property_id)
            .where(m.SensorMeasurement.ts >= ts_min, m.SensorMeasurement.ts <= ts_max)
        ).all()
    )
    existing = {(s, as_utc(t), r) for s, t, r in existing}
    for x in measurements:
        key = (x.signal.value, x.ts, x.source_ref)
        if key in existing:
            continue
        existing.add(key)
        session.add(
            m.SensorMeasurement(
                property_id=property_id, signal=x.signal.value, ts=x.ts, value=x.value, text=x.text,
                quality=x.quality.value, provenance=x.provenance.value, source_ref=x.source_ref, ingested_at=now,
            )
        )
        inserted += 1
    return inserted


def upsert_rates(session: Session, rates: list[RateInterval], observed_at: datetime) -> int:
    inserted = 0
    for r in rates:
        existing = session.scalar(
            select(m.TariffRateInterval).where(
                m.TariffRateInterval.tariff_code == r.tariff_code,
                m.TariffRateInterval.start == r.start,
                m.TariffRateInterval.is_intelligent_adjusted == r.is_intelligent_adjusted,
            )
        )
        if existing:
            if existing.rate_p_per_kwh != r.rate_p_per_kwh:
                existing.rate_p_per_kwh = r.rate_p_per_kwh
                existing.observed_at = observed_at
            continue
        session.add(m.TariffRateInterval(tariff_code=r.tariff_code, start=r.start, end=r.end, rate_p_per_kwh=r.rate_p_per_kwh,
                                         is_intelligent_adjusted=r.is_intelligent_adjusted, is_capped=r.is_capped,
                                         provenance=r.provenance.value, observed_at=observed_at))
        inserted += 1
    return inserted


def upsert_standing_charges(session: Session, charges: list[StandingCharge], observed_at: datetime) -> None:
    for c in charges:
        existing = session.scalar(select(m.TariffStandingCharge).where(
            m.TariffStandingCharge.tariff_code == c.tariff_code, m.TariffStandingCharge.start == c.start))
        if existing:
            existing.pence_per_day = c.pence_per_day
            existing.end = c.end
        else:
            session.add(m.TariffStandingCharge(tariff_code=c.tariff_code, start=c.start, end=c.end,
                                               pence_per_day=c.pence_per_day, observed_at=observed_at))


def record_dispatches(session: Session, property_id: int, dispatches: list[Dispatch]) -> int:
    """Append dispatch observations; the newest observation of a window wins on read."""
    n = 0
    for d in dispatches:
        exists = session.scalar(select(m.SmartChargingDispatch).where(
            m.SmartChargingDispatch.property_id == property_id, m.SmartChargingDispatch.kind == d.kind.value,
            m.SmartChargingDispatch.start == d.start, m.SmartChargingDispatch.end == d.end))
        if exists:
            exists.observed_at = d.observed_at
            exists.charge_kwh = d.charge_kwh
            continue
        session.add(m.SmartChargingDispatch(property_id=property_id, kind=d.kind.value, start=d.start, end=d.end,
                                            charge_kwh=d.charge_kwh, source=d.source, location=d.location,
                                            observed_at=d.observed_at))
        n += 1
    return n


def store_weather(session: Session, property_id: int, points: list[WeatherPoint]) -> int:
    n = 0
    for p in points:
        exists = session.scalar(select(m.WeatherForecast.id).where(
            m.WeatherForecast.property_id == property_id, m.WeatherForecast.provenance == p.provenance.value,
            m.WeatherForecast.issued_at == p.issued_at, m.WeatherForecast.valid_at == p.valid_at))
        if exists:
            continue
        session.add(m.WeatherForecast(property_id=property_id, provenance=p.provenance.value, issued_at=p.issued_at,
                                      valid_at=p.valid_at, temperature_c=p.temperature_c, wind_speed_ms=p.wind_speed_ms,
                                      humidity_pct=p.humidity_pct, cloud_cover_pct=p.cloud_cover_pct,
                                      condition=p.condition, quality=p.quality.value))
        n += 1
    return n


def store_solar(session: Session, property_id: int, points: list[SolarPoint]) -> int:
    n = 0
    for p in points:
        exists = session.scalar(select(m.SolarForecast.id).where(
            m.SolarForecast.property_id == property_id, m.SolarForecast.provenance == p.provenance.value,
            m.SolarForecast.issued_at == p.issued_at, m.SolarForecast.period_start == p.period_start))
        if exists:
            continue
        session.add(m.SolarForecast(property_id=property_id, provenance=p.provenance.value, issued_at=p.issued_at,
                                    period_start=p.period_start, period_minutes=p.period_minutes,
                                    pv_estimate_kw=p.pv_estimate_kw, pv_estimate10_kw=p.pv_estimate10_kw,
                                    pv_estimate90_kw=p.pv_estimate90_kw, ghi_w_m2=p.ghi_w_m2))
        n += 1
    return n


def latest_measurements(session: Session, property_id: int) -> dict[str, m.SensorMeasurement]:
    sub = (
        select(m.SensorMeasurement.signal, func.max(m.SensorMeasurement.ts).label("ts"))
        .where(m.SensorMeasurement.property_id == property_id)
        .group_by(m.SensorMeasurement.signal)
        .subquery()
    )
    rows = session.scalars(
        select(m.SensorMeasurement).join(sub, (m.SensorMeasurement.signal == sub.c.signal) & (m.SensorMeasurement.ts == sub.c.ts))
        .where(m.SensorMeasurement.property_id == property_id)
    ).all()
    return {r.signal: r for r in rows}


def measurements_for(session: Session, property_id: int, signal: str, start: datetime, end: datetime) -> list[m.SensorMeasurement]:
    return list(session.scalars(
        select(m.SensorMeasurement)
        .where(m.SensorMeasurement.property_id == property_id, m.SensorMeasurement.signal == signal,
               m.SensorMeasurement.ts >= start, m.SensorMeasurement.ts <= end)
        .order_by(m.SensorMeasurement.ts)
    ).all())


def rates_for(session: Session, tariff_code: str, start: datetime, end: datetime) -> list[m.TariffRateInterval]:
    return list(session.scalars(
        select(m.TariffRateInterval)
        .where(m.TariffRateInterval.tariff_code == tariff_code, m.TariffRateInterval.end > start, m.TariffRateInterval.start < end)
        .order_by(m.TariffRateInterval.start)
    ).all())


def log_quality_event(session: Session, property_id: int, severity: str, source: str, message: str, signal: str | None = None, **details) -> None:
    session.add(m.DataQualityEvent(property_id=property_id, ts=datetime.now(tz=timezone.utc), severity=severity,
                                   source=source, signal=signal, message=message, details=details))
