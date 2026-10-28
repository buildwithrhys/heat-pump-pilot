"""Providers that read canonical signals out of Home Assistant.

``HATelemetryProvider`` covers the Vaillant (mypyllant), Huawei and misc sensors
through a signal mapping. ``HAOctopusTariffProvider``, ``HAWeatherProvider`` and
``HASolcastProvider`` read the respective HA integrations' entities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ...domain import (
    CapabilityReport,
    Dispatch,
    DispatchKind,
    Measurement,
    Provenance,
    Quality,
    RateInterval,
    SIGNAL_CATALOGUE,
    Signal,
    freshness_class,
    SolarPoint,
    StandingCharge,
    TariffSnapshot,
    WeatherPoint,
)
from ..base import ProviderHealth
from .client import HAClient, HAState
from .discovery import discover_capabilities, propose_mappings


@dataclass
class MappedSignal:
    signal: Signal
    entity_id: str
    unit_scale: float
    quality: Quality
    provenance: Provenance


def mapping_from_report(report: CapabilityReport) -> list[MappedSignal]:
    """Turn a capability report into a concrete mapping (available signals only)."""
    out = []
    for cap in report.capabilities:
        if cap.status != "available" or not cap.source_ref:
            continue
        spec = SIGNAL_CATALOGUE[cap.signal]
        scale = spec.unit_scale.get(cap.unit or "", 1.0) if spec.unit_scale else 1.0
        prov = Provenance.HOME_ASSISTANT
        eid = cap.source_ref
        if "octopus" in eid:
            prov = Provenance.OCTOPUS_HA
        elif "openweathermap" in eid:
            prov = Provenance.OPENWEATHER_HA
        elif any(k in eid for k in ("arotherm", "glendale", "vaillant", "circuit_0", "domestic_hot_water")):
            prov = Provenance.VAILLANT_MYPYLLANT
        elif any(k in eid for k in ("inverter", "batter", "power_meter")):
            prov = Provenance.HUAWEI_SOLAR_HA
        out.append(MappedSignal(cap.signal, eid, scale, cap.quality, prov))
    return out


def apply_overrides(report: CapabilityReport, overrides: dict[str, dict[str, Any]], states: dict[str, HAState] | None = None) -> CapabilityReport:
    """Replace discovered sources with user-confirmed ones. ``overrides`` maps signal -> {source_ref, unit}."""
    if not overrides:
        return report
    caps = []
    for cap in report.capabilities:
        o = overrides.get(cap.signal.value)
        if not o:
            caps.append(cap)
            continue
        st = (states or {}).get(o["source_ref"])
        live = st.is_live if st else True
        # a user's choice is always mapped, even while the entity is unavailable, so it recovers on its own
        caps.append(cap.model_copy(update={"source_ref": o["source_ref"], "unit": o.get("unit") or (st.unit if st else cap.unit),
                                           "status": "available", "confidence": 1.0,
                                           "notes": "chosen by you" + ("" if live else f"; state is currently {st.state}")}))
    return report.model_copy(update={"capabilities": caps})


def measurement_from_state(m: MappedSignal, state: HAState, now: datetime) -> Measurement | None:
    spec = SIGNAL_CATALOGUE[m.signal]
    ts = state.last_updated or now
    if not state.is_live:
        return None
    if spec.kind.value == "state":
        txt = state.state
        val = 1.0 if txt in ("on", "true", "True") else 0.0 if txt in ("off", "false", "False") else None
        return Measurement(signal=m.signal, ts=ts, value=val, text=txt, quality=m.quality,
                           provenance=m.provenance, source_ref=state.entity_id)
    val = state.as_float()
    if val is None:
        return None
    return Measurement(signal=m.signal, ts=ts, value=val * m.unit_scale, quality=m.quality,
                       provenance=m.provenance, source_ref=state.entity_id)


class HATelemetryProvider:
    name = "home_assistant_telemetry"

    def __init__(self, client: HAClient, mapping: list[MappedSignal] | None = None):
        self.client = client
        self.mapping = mapping or []
        self._last_ok: datetime | None = None
        self._last_error: str | None = None

    overrides: dict[str, dict[str, Any]] | None = None

    async def discover(self) -> CapabilityReport:
        states = await self.client.states_with_registry()
        entries = await self.client.config_entries()
        report = discover_capabilities(states, entries)
        self.last_states = {s.entity_id: s for s in states}
        self.last_candidates = {sig.value: [(st.entity_id, round(sc, 2), note) for st, sc, note in cands[:6]]
                                for sig, cands in propose_mappings(states).items()}
        report = apply_overrides(report, self.overrides or {}, self.last_states)
        self.mapping = mapping_from_report(report)
        return report

    def set_overrides(self, overrides: dict[str, dict[str, Any]], report: CapabilityReport | None = None) -> CapabilityReport | None:
        """Apply new overrides to the last report without a round trip to Home Assistant."""
        self.overrides = overrides
        if report is None:
            return None
        report = apply_overrides(report, overrides, getattr(self, "last_states", None))
        self.mapping = mapping_from_report(report)
        return report

    async def read_measurements(self) -> list[Measurement]:
        now = datetime.now(tz=timezone.utc)
        try:
            states = {s.entity_id: s for s in await self.client.states()}
            self.live_states = states  # every entity with attributes, as read this cycle (Predbat's plan lives here)
            self._last_ok = now
            self._last_error = None
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            raise
        out = []
        confirmed = getattr(self, "_confirmed", None)
        if confirmed is None:
            confirmed = self._confirmed = {}
        for m in self.mapping:
            st = states.get(m.entity_id)
            if st is None:
                continue
            meas = measurement_from_state(m, st, now)
            if meas:
                out.append(meas)
                beat = self._confirmation(m, st, now, confirmed)
                if beat is not None:
                    out.append(beat)
        return out

    CONFIRM_EVERY = timedelta(minutes=30)
    QUIET_AFTER = timedelta(hours=2)

    def _confirmation(self, m, st, now: datetime, confirmed: dict[str, datetime]) -> Measurement | None:
        """A reading that repeats an unchanged value, so a steady temperature or a setting that holds
        for days is not mistaken for a gap. Home Assistant stores nothing new while a value is
        unchanged. Telemetry is confirmed only by Home Assistant's own last_reported; settings and
        flags also by their integration still being active. Counters are never repeated."""
        cls = freshness_class(m.signal)
        if cls == "counter" or st.last_updated is None:
            return None
        at = st.last_reported if st.last_reported and st.last_reported > st.last_updated else None
        if at is None and cls == "setting" and self.integration_active(getattr(self.last_states.get(st.entity_id), "platform", None) if hasattr(self, "last_states") else None, now):
            at = now
        if at is None or at - st.last_updated < self.CONFIRM_EVERY:
            return None
        if at - confirmed.get(st.entity_id, st.last_updated) < self.CONFIRM_EVERY:
            return None
        confirmed[st.entity_id] = at
        beat = measurement_from_state(m, st, at)
        return beat.model_copy(update={"ts": at}) if beat else None

    def seen_at(self, st) -> datetime | None:
        stamps = [t for t in (st.last_updated, st.last_reported) if t is not None]
        return max(stamps) if stamps else None

    def integration_active(self, platform: str | None, now: datetime) -> bool | None:
        """Whether any entity of this integration was written in the last two hours. ``None`` when unknown."""
        if not platform or not hasattr(self, "last_states") or not getattr(self, "live_states", None):
            return None
        ids = [eid for eid, st in self.last_states.items() if st.platform == platform]
        stamps = [self.seen_at(self.live_states[eid]) for eid in ids if eid in self.live_states]
        stamps = [t for t in stamps if t is not None]
        return bool(stamps) and now - max(stamps) <= self.QUIET_AFTER

    async def read_history(self, start: datetime, end: datetime) -> list[Measurement]:
        """Hourly long-term statistics: counters and means.

        A statistics row's ``state`` is the counter's last value *within* the hour,
        so it is stamped at the end of the hour. Stamping it at the start made the
        counter look as if it ran backwards whenever live readings were interleaved,
        which the accounting then mistook for a meter reset. Means are stamped at
        the middle of the hour.
        """
        ids = [m.entity_id for m in self.mapping]
        stats: dict[str, list[dict[str, Any]]] = {}
        chunk = timedelta(days=14)
        t = start
        while t < end:  # keep each websocket reply small enough for long backfills
            part = await self.client.statistics(ids, t, min(t + chunk, end), period="hour")
            for eid, rows in part.items():
                stats.setdefault(eid, []).extend(rows)
            t += chunk
        by_id = {m.entity_id: m for m in self.mapping}
        now = datetime.now(tz=timezone.utc)
        out: list[Measurement] = []

        def _ts(v) -> datetime:
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc) if isinstance(v, (int, float)) else datetime.fromisoformat(v)

        for eid, rows in stats.items():
            m = by_id[eid]
            spec = SIGNAL_CATALOGUE[m.signal]
            for row in rows:
                t0 = _ts(row["start"])
                t1 = _ts(row["end"]) if row.get("end") is not None else t0 + timedelta(hours=1)
                if spec.kind.value == "cumulative":
                    # "sum" is Home Assistant's own running total across daily resets; "state" restarts
                    # every midnight for myVAILLANT's daily figures, so "sum" is the series to difference.
                    val = row.get("sum") if row.get("sum") is not None else row.get("state")
                    ts = min(t1, now)
                else:
                    val = row.get("mean")
                    ts = t0 + (t1 - t0) / 2
                if val is None:
                    continue
                # statistics rows carry their own source tag: they may sit on a different baseline
                # from the live sensor (HA restates and re-bases long-term statistics), so they
                # must never be differenced against live readings.
                tag = "#statistics_sum" if spec.kind.value == "cumulative" and row.get("sum") is not None else "#statistics"
                out.append(Measurement(signal=m.signal, ts=ts, value=float(val) * m.unit_scale,
                                       quality=m.quality, provenance=m.provenance, source_ref=f"{eid}{tag}"))
        return out

    async def health(self) -> ProviderHealth:
        try:
            await self.client.ping()
            return ProviderHealth(self.name, True, self._last_ok, None, {"mapped_signals": len(self.mapping)})
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(self.name, False, self._last_ok, str(exc))


# ----------------------------------------------------------------------------
# Octopus (BottlecapDave integration) read via HA entities


def _parse_dt(v: str) -> datetime:
    return datetime.fromisoformat(v).astimezone(timezone.utc)


def parse_rate_event(state: HAState, tariff_code: str, is_export: bool = False) -> list[RateInterval]:
    rates = state.attributes.get("rates") or []
    out = []
    for r in rates:
        out.append(
            RateInterval(
                start=_parse_dt(r["start"]),
                end=_parse_dt(r["end"]),
                rate_p_per_kwh=round(float(r["value_inc_vat"]) * 100.0, 4),
                tariff_code=tariff_code,
                is_export=is_export,
                is_intelligent_adjusted=bool(r.get("is_intelligent_adjusted", False)),
                is_capped=bool(r.get("is_capped", False)),
                provenance=Provenance.OCTOPUS_HA,
            )
        )
    return out


def parse_dispatches(state: HAState, observed_at: datetime) -> list[Dispatch]:
    a = state.attributes
    out: list[Dispatch] = []
    for kind, key in ((DispatchKind.PLANNED, "planned_dispatches"), (DispatchKind.COMPLETED, "completed_dispatches"), (DispatchKind.STARTED, "started_dispatches")):
        for d in a.get(key) or []:
            out.append(
                Dispatch(
                    kind=kind,
                    start=_parse_dt(d["start"]),
                    end=_parse_dt(d["end"]),
                    charge_kwh=abs(float(d["charge_in_kwh"])) if d.get("charge_in_kwh") is not None else None,
                    source=d.get("source"),
                    location=d.get("location"),
                    observed_at=observed_at,
                )
            )
    return out


class HAOctopusTariffProvider:
    name = "octopus_via_home_assistant"

    def __init__(self, client: HAClient):
        self.client = client
        self._last_ok: datetime | None = None
        self._last_error: str | None = None

    @staticmethod
    def _find(states: dict[str, HAState], suffix: str, must: tuple[str, ...] = ()) -> HAState | None:
        for eid, s in states.items():
            if eid.endswith(suffix) and all(m in eid for m in must):
                return s
        return None

    async def snapshot(self) -> TariffSnapshot:
        now = datetime.now(tz=timezone.utc)
        states = {s.entity_id: s for s in await self.client.states() if s.entity_id.startswith(("sensor.octopus_energy", "event.octopus_energy", "binary_sensor.octopus_energy"))}
        imp = next((s for eid, s in states.items() if eid.endswith("_current_rate") and "export" not in eid), None)
        if imp is None:
            raise RuntimeError("No Octopus import current_rate sensor found")
        tariff = imp.attributes.get("tariff", "unknown")
        exp = next((s for eid, s in states.items() if eid.endswith("_export_current_rate")), None)
        rates: list[RateInterval] = []
        for suffix in ("_previous_day_rates", "_current_day_rates", "_next_day_rates"):
            ev = next((s for eid, s in states.items() if eid.endswith(suffix) and "export" not in eid), None)
            if ev:
                rates.extend(parse_rate_event(ev, tariff))
            if exp:
                evx = next((s for eid, s in states.items() if eid.endswith("_export" + suffix)), None)
                if evx:
                    rates.extend(parse_rate_event(evx, exp.attributes.get("tariff", "export"), is_export=True))
        sc = next((s for eid, s in states.items() if eid.endswith("_current_standing_charge") and "export" not in eid), None)
        standing = []
        if sc and sc.as_float() is not None:
            standing.append(StandingCharge(
                start=_parse_dt(sc.attributes["start"]) if sc.attributes.get("start") else now,
                end=_parse_dt(sc.attributes["end"]) if sc.attributes.get("end") else None,
                pence_per_day=round(sc.as_float() * 100.0, 4), tariff_code=tariff))
        dispatches: list[Dispatch] = []
        for eid, s in states.items():
            if eid.endswith("_intelligent_dispatching") and s.is_live:
                dispatches.extend(parse_dispatches(s, now))
        self._last_ok = now
        region = tariff.rsplit("-", 1)[-1] if "-" in tariff else None
        product = tariff[len("E-1R-"):-2] if tariff.startswith("E-1R-") else None
        return TariffSnapshot(observed_at=now, import_tariff_code=tariff,
                              export_tariff_code=exp.attributes.get("tariff") if exp else None,
                              region=region, product=product, rates=rates,
                              standing_charges=standing, dispatches=dispatches)

    async def rate_history(self, start: datetime, end: datetime) -> list[RateInterval]:
        # The HA integration exposes only previous/current/next day; history accrues
        # through regular polling. The direct Octopus API client fills this in when configured.
        return []

    async def metered_intervals(self) -> list[tuple[datetime, datetime, float]]:
        """Half-hourly metered import from the Octopus integration's accumulative consumption
        sensors (smart meter data for yesterday, Home Mini data for today). The best whole-home
        series there is: metered, half-hourly, no counter arithmetic."""
        states = {s.entity_id: s for s in await self.client.states() if s.entity_id.startswith("sensor.octopus_energy_electricity")}
        out: list[tuple[datetime, datetime, float]] = []
        for eid, s in states.items():
            if "export" in eid or not (eid.endswith("_previous_accumulative_consumption") or eid.endswith("_current_accumulative_consumption")):
                continue
            for c in s.attributes.get("charges") or []:
                try:
                    out.append((_parse_dt(c["start"]), _parse_dt(c["end"]), float(c["consumption"])))
                except (KeyError, TypeError, ValueError):
                    continue
        return out

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, self._last_error is None, self._last_ok, self._last_error)


# ----------------------------------------------------------------------------
# Weather and solar via HA


class HAWeatherProvider:
    name = "openweather_via_home_assistant"

    def __init__(self, client: HAClient, entity_id: str = "weather.weather_forecast"):
        self.client = client
        self.entity_id = entity_id
        self._last_ok: datetime | None = None

    async def forecast(self, hours: int = 48) -> list[WeatherPoint]:
        now = datetime.now(tz=timezone.utc)
        rows = await self.client.weather_forecast(self.entity_id, "hourly")
        out = []
        for r in rows[:hours]:
            out.append(WeatherPoint(
                issued_at=now, valid_at=_parse_dt(r["datetime"]), temperature_c=float(r["temperature"]),
                wind_speed_ms=_wind_ms(r.get("wind_speed"), r.get("wind_speed_unit")),
                humidity_pct=r.get("humidity"), cloud_cover_pct=r.get("cloud_coverage"),
                condition=r.get("condition"), provenance=Provenance.OPENWEATHER_HA))
        self._last_ok = now
        return out

    async def observation(self) -> WeatherPoint | None:
        now = datetime.now(tz=timezone.utc)
        states = {s.entity_id: s for s in await self.client.states()}
        w = states.get(self.entity_id)
        if not w or not w.is_live:
            return None
        a = w.attributes
        return WeatherPoint(issued_at=now, valid_at=now, temperature_c=float(a["temperature"]),
                            wind_speed_ms=_wind_ms(a.get("wind_speed"), a.get("wind_speed_unit")),
                            humidity_pct=a.get("humidity"), cloud_cover_pct=a.get("cloud_coverage"),
                            condition=w.state, quality=Quality.SENSOR, provenance=Provenance.OPENWEATHER_HA)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, self._last_ok)


def _wind_ms(v: Any, unit: str | None) -> float | None:
    if v is None:
        return None
    v = float(v)
    return {"mph": v * 0.44704, "km/h": v / 3.6, "m/s": v, "kn": v * 0.514444}.get(unit or "m/s", v)


class HASolcastProvider:
    name = "solcast_via_home_assistant"

    def __init__(self, client: HAClient):
        self.client = client
        self._last_ok: datetime | None = None

    async def forecast(self, hours: int = 48) -> list[SolarPoint]:
        now = datetime.now(tz=timezone.utc)
        states = {s.entity_id: s for s in await self.client.states() if s.entity_id.startswith("sensor.solcast_pv_forecast_forecast_")}
        points: list[SolarPoint] = []
        horizon = now + timedelta(hours=hours)
        for key in ("today", "tomorrow", "day_3"):
            s = states.get(f"sensor.solcast_pv_forecast_forecast_{key}")
            if not s:
                continue
            for row in s.attributes.get("detailedForecast") or []:
                ps = _parse_dt(row["period_start"])
                if ps < now - timedelta(hours=1) or ps > horizon:
                    continue
                points.append(SolarPoint(issued_at=now, period_start=ps, period_minutes=30,
                                         pv_estimate_kw=float(row["pv_estimate"]),
                                         pv_estimate10_kw=row.get("pv_estimate10"),
                                         pv_estimate90_kw=row.get("pv_estimate90"),
                                         provenance=Provenance.SOLCAST_HA))
        self._last_ok = now
        return points

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, self._last_ok)
