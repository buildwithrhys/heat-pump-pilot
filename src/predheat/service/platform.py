"""Wiring: chooses providers for the configured data mode and runs the collector.

The same ``Platform`` object serves the CLI, the API and the tests. It is
read-only with respect to equipment in every mode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..analytics import allocate_heat_pump_categories, counter_to_intervals, reconcile
from ..config import DataMode, Settings, get_settings
from ..db import init_db, session_scope
from ..db import models as m
from ..db.session import as_utc
from ..domain import CapabilityReport, Measurement, Provenance, Quality, RateInterval, Signal
from ..integrations.base import ProviderHealth
from ..pricing import PriceClass, build_rate_book, classify_slots, standing_charge_for_period
from ..timeutils import LONDON, local_day_bounds, now_utc
from . import repository as repo

log = logging.getLogger("predheat.platform")


@dataclass
class Platform:
    settings: Settings
    telemetry: Any
    tariff: Any
    weather: Any
    solar: Any
    clock: Any  # object with .now()
    property_id: int | None = None
    capability_report: CapabilityReport | None = None
    last_poll: dict[str, Any] = field(default_factory=dict)
    simulated_home: Any = None

    # ------------------------------------------------------------------ lifecycle
    async def bootstrap(self, backfill_days: int | None = None) -> None:
        init_db(self.settings.database_url)
        with session_scope(self.settings.database_url) as s:
            prop = repo.ensure_property(s, "Pilot property", self.settings.timezone,
                                        latitude=self.settings.latitude, longitude=self.settings.longitude)
            self.property_id = prop.id
        self.capability_report = await self.telemetry.discover()
        with session_scope(self.settings.database_url) as s:
            for cap in self.capability_report.capabilities:
                if cap.status != "available":
                    continue
                existing = s.query(m.SensorMapping).filter_by(property_id=self.property_id, signal=cap.signal.value).one_or_none()
                if existing is None:
                    s.add(m.SensorMapping(property_id=self.property_id, signal=cap.signal.value, source_ref=cap.source_ref or "",
                                          provenance=self.capability_report.provenance.value, quality=cap.quality.value,
                                          confidence=cap.confidence, notes=cap.notes))
        days = backfill_days if backfill_days is not None else (self.settings.sim_days if self.settings.data_mode == DataMode.SIMULATED else 7)
        if days:
            await self.backfill(days)
        await self.poll_once()

    async def backfill(self, days: int) -> int:
        end = self.clock.now()
        start = end - timedelta(days=days)
        try:
            hist = await self.telemetry.read_history(start, end)
        except Exception as exc:  # noqa: BLE001
            log.warning("history backfill failed: %s", exc)
            with session_scope(self.settings.database_url) as s:
                repo.log_quality_event(s, self.property_id, "warning", self.telemetry.name, f"backfill failed: {exc}")
            return 0
        with session_scope(self.settings.database_url) as s:
            n = repo.upsert_measurements(s, self.property_id, hist)
            try:
                rates = await self.tariff.rate_history(start - timedelta(days=1), end)
                if rates:
                    repo.upsert_rates(s, rates, end)
                    self.last_poll["tariff_code"] = rates[0].tariff_code
            except Exception as exc:  # noqa: BLE001
                repo.log_quality_event(s, self.property_id, "warning", self.tariff.name, f"rate history unavailable: {exc}")
        log.info("backfilled %d measurements", n)
        return n

    async def poll_once(self) -> dict[str, Any]:
        """Read every provider once and persist. Failures are isolated per provider."""
        result: dict[str, Any] = {"at": self.clock.now().isoformat()}
        with session_scope(self.settings.database_url) as s:
            try:
                meas = await self.telemetry.read_measurements()
                result["measurements"] = repo.upsert_measurements(s, self.property_id, meas)
            except Exception as exc:  # noqa: BLE001
                result["measurements_error"] = str(exc)
                repo.log_quality_event(s, self.property_id, "error", self.telemetry.name, str(exc))
            try:
                snap = await self.tariff.snapshot()
                result["rates"] = repo.upsert_rates(s, snap.rates, snap.observed_at)
                repo.upsert_standing_charges(s, snap.standing_charges, snap.observed_at)
                result["dispatches"] = repo.record_dispatches(s, self.property_id, snap.dispatches)
                result["tariff"] = snap.import_tariff_code
                self.last_poll["tariff_code"] = snap.import_tariff_code
                self.last_poll["export_tariff_code"] = snap.export_tariff_code
                self.last_poll["region"] = snap.region
            except Exception as exc:  # noqa: BLE001
                result["tariff_error"] = str(exc)
                repo.log_quality_event(s, self.property_id, "error", self.tariff.name, str(exc))
            try:
                wx = await self.weather.forecast(48)
                result["weather_points"] = repo.store_weather(s, self.property_id, wx)
                obs = await self.weather.observation()
                if obs:
                    repo.upsert_measurements(s, self.property_id, [Measurement(
                        signal=Signal.WEATHER_TEMPERATURE, ts=obs.valid_at, value=obs.temperature_c,
                        quality=obs.quality, provenance=obs.provenance, source_ref="weather.observation")])
            except Exception as exc:  # noqa: BLE001
                result["weather_error"] = str(exc)
                repo.log_quality_event(s, self.property_id, "error", self.weather.name, str(exc))
            try:
                sol = await self.solar.forecast(48)
                result["solar_points"] = repo.store_solar(s, self.property_id, sol)
            except Exception as exc:  # noqa: BLE001
                result["solar_error"] = str(exc)
                repo.log_quality_event(s, self.property_id, "error", self.solar.name, str(exc))
        self.last_poll.update(result)
        return result

    # ------------------------------------------------------------------ queries
    async def health(self) -> list[ProviderHealth]:
        out = []
        for p in (self.telemetry, self.tariff, self.weather, self.solar):
            try:
                out.append(await p.health())
            except Exception as exc:  # noqa: BLE001
                out.append(ProviderHealth(getattr(p, "name", "?"), False, None, str(exc)))
        return out

    def overview(self) -> dict[str, Any]:
        now = self.clock.now()
        with session_scope(self.settings.database_url) as s:
            latest = repo.latest_measurements(s, self.property_id)

            def val(sig: Signal) -> dict | None:
                r = latest.get(sig.value)
                if not r:
                    return None
                return {"value": r.value, "text": r.text, "ts": as_utc(r.ts).isoformat(), "quality": r.quality, "stale": (now - as_utc(r.ts)) > timedelta(hours=2)}

            today = now.astimezone(LONDON).date()
            costs = self._energy_costs(s, today, today + timedelta(days=1))
            tariff_code = self.last_poll.get("tariff_code")
            rate_now = None
            if tariff_code:
                book = build_rate_book([_ri(r) for r in repo.rates_for(s, tariff_code, now - timedelta(hours=1), now + timedelta(hours=1))], tariff_code)
                ri = book.rate_at(now)
                rate_now = ri.rate_p_per_kwh if ri else None
            return {
                "generated_at": now.isoformat(),
                "data_mode": self.settings.data_mode.value,
                "operating_mode": self.settings.operating_mode.value,
                "control_enabled": self.settings.control_enabled,
                "indoor_temperature": val(Signal.INDOOR_TEMPERATURE),
                "indoor_setpoint": val(Signal.INDOOR_SETPOINT),
                "outdoor_temperature": val(Signal.OUTDOOR_TEMPERATURE),
                "flow_temperature": val(Signal.FLOW_TEMPERATURE),
                "dhw_tank_temperature": val(Signal.DHW_TANK_TEMPERATURE),
                "hp_electrical_power": val(Signal.HP_ELECTRICAL_POWER),
                "hp_operating_state": val(Signal.HP_OPERATING_STATE),
                "tariff_code": tariff_code,
                "import_rate_now_p": rate_now,
                "today": costs,
                "next_action": {"kind": "none", "reason": "Monitor mode: no control actions are generated."},
            }

    def _energy_costs(self, s, start_day: date, end_day: date) -> dict[str, Any]:
        """Half-hourly accounting between two local dates (end exclusive)."""
        start, _ = local_day_bounds(start_day)
        _, end = local_day_bounds(end_day - timedelta(days=1))
        pad = timedelta(hours=1)

        def counter(sig: Signal):
            rows = repo.measurements_for(s, self.property_id, sig.value, start - pad, end + pad)
            return [Measurement(signal=sig, ts=as_utc(r.ts), value=r.value, quality=Quality(r.quality), provenance=Provenance(r.provenance), source_ref=r.source_ref) for r in rows]

        def clip(ints):
            return [i for i in ints if start <= i.start < end]

        heat_e = clip(counter_to_intervals(counter(Signal.HP_ELEC_HEATING_KWH), "hp_elec_heating"))
        dhw_e = clip(counter_to_intervals(counter(Signal.HP_ELEC_DHW_KWH), "hp_elec_dhw"))
        aux_h = clip(counter_to_intervals(counter(Signal.AUX_ELEC_HEATING_KWH), "aux_elec_heating"))
        aux_d = clip(counter_to_intervals(counter(Signal.AUX_ELEC_DHW_KWH), "aux_elec_dhw"))
        heat_th = clip(counter_to_intervals(counter(Signal.HP_HEAT_HEATING_KWH), "heat_heating"))
        dhw_th = clip(counter_to_intervals(counter(Signal.HP_HEAT_DHW_KWH), "heat_dhw"))
        grid_imp = clip(counter_to_intervals(counter(Signal.GRID_IMPORT_KWH), "grid_import"))
        by_slot = allocate_heat_pump_categories(heat_e, dhw_e, aux_h, aux_d)
        recon = reconcile(by_slot)

        tariff_code = self.last_poll.get("tariff_code")
        book = None
        if tariff_code:
            book = build_rate_book([_ri(r) for r in repo.rates_for(s, tariff_code, start, end)], tariff_code)

        def cost_of(ints) -> tuple[float, float, bool]:
            if not book:
                return 0.0, 0.0, False
            total_c = 0.0
            kwh = 0.0
            complete = True
            for i in ints:
                c, _eff, ok = book.cost_pence(i.start, i.end, i.kwh)
                total_c += c
                kwh += i.kwh
                complete = complete and ok
            return round(total_c, 2), round(kwh, 3), complete

        def q_of(ints) -> str:
            qs = {i.quality.value for i in ints}
            return "measured" if qs <= {"metered", "sensor"} else "simulated" if qs == {"simulated"} else "estimated" if qs else "unavailable"

        hc, hk, hok = cost_of(heat_e)
        dc, dk, dok = cost_of(dhw_e)
        auxc, auxk, _ = cost_of(aux_h + aux_d)
        standby = [c["hp_standby"] for c in by_slot.values()]
        sc_, sk, _ = cost_of(standby)
        gc, gk, gok = cost_of(grid_imp)
        heat_kwh_th = round(sum(i.kwh for i in heat_th), 3)
        dhw_kwh_th = round(sum(i.kwh for i in dhw_th), 3)
        sc_pence = 0.0
        if tariff_code:
            charges = s.query(m.TariffStandingCharge).filter_by(tariff_code=tariff_code).all()
            from ..domain import StandingCharge
            sc_pence = standing_charge_for_period([StandingCharge(start=as_utc(c.start), end=as_utc(c.end), pence_per_day=c.pence_per_day, tariff_code=c.tariff_code) for c in charges], start, min(end, self.clock.now()))
        return {
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "tariff_code": tariff_code,
            "space_heating": {"kwh_electric": hk, "kwh_heat": heat_kwh_th, "cop": round(heat_kwh_th / hk, 2) if hk else None,
                              "cost_pence": hc, "pence_per_kwh_heat": round(hc / heat_kwh_th, 2) if heat_kwh_th else None,
                              "quality": q_of(heat_e), "prices_complete": hok},
            "hot_water": {"kwh_electric": dk, "kwh_heat": dhw_kwh_th, "cop": round(dhw_kwh_th / dk, 2) if dk else None,
                          "cost_pence": dc, "pence_per_kwh_heat": round(dc / dhw_kwh_th, 2) if dhw_kwh_th else None,
                          "quality": q_of(dhw_e), "prices_complete": dok},
            "auxiliary": {"kwh_electric": auxk, "cost_pence": auxc, "quality": q_of(aux_h + aux_d)},
            "standby": {"kwh_electric": round(sk, 3), "cost_pence": sc_, "quality": "estimated"},
            "heat_pump_total": {"kwh_electric": round(hk + dk + auxk + sk, 3), "cost_pence": round(hc + dc + auxc + sc_, 2)},
            "whole_home_import": {"kwh": gk, "cost_pence": gc, "quality": q_of(grid_imp), "prices_complete": gok},
            "standing_charge_pence": round(sc_pence, 2),
            "reconciliation": recon,
        }

    def energy_costs(self, start_day: date, end_day: date) -> dict[str, Any]:
        with session_scope(self.settings.database_url) as s:
            return self._energy_costs(s, start_day, end_day)

    def daily_series(self, start_day: date, end_day: date) -> list[dict[str, Any]]:
        out = []
        d = start_day
        with session_scope(self.settings.database_url) as s:
            while d < end_day:
                c = self._energy_costs(s, d, d + timedelta(days=1))
                out.append({"date": d.isoformat(), "space_heating": c["space_heating"], "hot_water": c["hot_water"],
                            "heat_pump_total": c["heat_pump_total"], "whole_home_import": c["whole_home_import"],
                            "standing_charge_pence": c["standing_charge_pence"]})
                d += timedelta(days=1)
        return out

    def timeline(self, hours: int = 48) -> dict[str, Any]:
        """Forecast timeline: prices by class, weather, solar. No optimisation yet (Phase 4)."""
        now = self.clock.now()
        start = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
        end = start + timedelta(hours=hours)
        with session_scope(self.settings.database_url) as s:
            tariff_code = self.last_poll.get("tariff_code")
            slots = []
            if tariff_code:
                book = build_rate_book([_ri(r) for r in repo.rates_for(s, tariff_code, start, end)], tariff_code)
                disp = s.query(m.SmartChargingDispatch).filter(m.SmartChargingDispatch.property_id == self.property_id,
                                                                m.SmartChargingDispatch.end > start - timedelta(days=1)).all()
                from ..domain import Dispatch, DispatchKind
                dispatches = [Dispatch(kind=DispatchKind(d.kind), start=as_utc(d.start), end=as_utc(d.end), charge_kwh=d.charge_kwh,
                                       source=d.source, location=d.location, observed_at=as_utc(d.observed_at)) for d in disp]
                for sp in classify_slots(start, end, book, dispatches):
                    slots.append({"start": sp.start.isoformat(), "end": sp.end.isoformat(), "rate_p": None if sp.rate_p_per_kwh != sp.rate_p_per_kwh else sp.rate_p_per_kwh,
                                  "price_class": sp.price_class.value, "assumed": sp.assumed})
            # latest weather forecast issue
            latest_issue = s.query(m.WeatherForecast.issued_at).filter_by(property_id=self.property_id).order_by(m.WeatherForecast.issued_at.desc()).first()
            weather = []
            if latest_issue:
                for w in s.query(m.WeatherForecast).filter_by(property_id=self.property_id, issued_at=latest_issue[0]).order_by(m.WeatherForecast.valid_at).all():
                    weather.append({"valid_at": as_utc(w.valid_at).isoformat(), "temperature_c": w.temperature_c, "cloud_cover_pct": w.cloud_cover_pct, "wind_speed_ms": w.wind_speed_ms})
            latest_solar = s.query(m.SolarForecast.issued_at).filter_by(property_id=self.property_id).order_by(m.SolarForecast.issued_at.desc()).first()
            solar = []
            if latest_solar:
                for p in s.query(m.SolarForecast).filter_by(property_id=self.property_id, issued_at=latest_solar[0]).order_by(m.SolarForecast.period_start).all():
                    solar.append({"period_start": as_utc(p.period_start).isoformat(), "pv_kw": p.pv_estimate_kw, "pv10_kw": p.pv_estimate10_kw, "pv90_kw": p.pv_estimate90_kw})
            summary = {c.value: sum(1 for x in slots if x["price_class"] == c.value) for c in PriceClass}
        return {"generated_at": now.isoformat(), "horizon_hours": hours, "tariff_code": tariff_code, "slots": slots,
                "slot_summary": summary, "weather": weather, "solar": solar,
                "optimisation": {"status": "not_available", "reason": "Shadow optimisation arrives in Phase 4."}}

    def system_health(self, provider_health: list[ProviderHealth]) -> dict[str, Any]:
        now = self.clock.now()
        with session_scope(self.settings.database_url) as s:
            latest = repo.latest_measurements(s, self.property_id)
            signals = []
            for cap in (self.capability_report.capabilities if self.capability_report else []):
                r = latest.get(cap.signal.value)
                age = (now - as_utc(r.ts)).total_seconds() / 60 if r else None
                signals.append({"signal": cap.signal.value, "status": cap.status, "quality": cap.quality.value, "source": cap.source_ref,
                                "confidence": cap.confidence, "last_value": (r.value if r else None), "last_text": (r.text if r else None),
                                "age_minutes": round(age, 1) if age is not None else None, "stale": (age is not None and age > 120), "notes": cap.notes})
            events = [{"ts": as_utc(e.ts).isoformat(), "severity": e.severity, "source": e.source, "message": e.message}
                      for e in s.query(m.DataQualityEvent).filter_by(property_id=self.property_id).order_by(m.DataQualityEvent.ts.desc()).limit(25).all()]
            counts = {"measurements": s.query(m.SensorMeasurement).filter_by(property_id=self.property_id).count(),
                      "rate_intervals": s.query(m.TariffRateInterval).count(),
                      "weather_points": s.query(m.WeatherForecast).filter_by(property_id=self.property_id).count(),
                      "solar_points": s.query(m.SolarForecast).filter_by(property_id=self.property_id).count(),
                      "dispatches": s.query(m.SmartChargingDispatch).filter_by(property_id=self.property_id).count(),
                      "control_actions": s.query(m.ControlExecutionLog).filter_by(property_id=self.property_id).count()}
        return {"generated_at": now.isoformat(), "data_mode": self.settings.data_mode.value, "operating_mode": self.settings.operating_mode.value,
                "control_enabled": self.settings.control_enabled,
                "providers": [{"name": h.provider, "ok": h.ok, "last_success_at": h.last_success_at.isoformat() if h.last_success_at else None, "last_error": h.last_error, "details": h.details} for h in provider_health],
                "integrations": self.capability_report.integrations if self.capability_report else [],
                "warnings": self.capability_report.warnings if self.capability_report else [],
                "signals": signals, "missing_for_thermal_model": [x.value for x in (self.capability_report.missing_for_thermal_model() if self.capability_report else [])],
                "recent_events": events, "record_counts": counts, "last_poll": self.last_poll}


def _ri(r: m.TariffRateInterval) -> RateInterval:
    return RateInterval(start=as_utc(r.start), end=as_utc(r.end), rate_p_per_kwh=r.rate_p_per_kwh, tariff_code=r.tariff_code,
                        is_intelligent_adjusted=r.is_intelligent_adjusted, is_capped=r.is_capped, provenance=Provenance(r.provenance))


class _RealClock:
    def now(self) -> datetime:
        return now_utc()


def build_platform(settings: Settings | None = None) -> Platform:
    settings = settings or get_settings()
    if settings.data_mode == DataMode.SIMULATED:
        from ..integrations.simulated import SimConfig, SimulatedHome, SimulatedSolarProvider, SimulatedTariffProvider, SimulatedTelemetryProvider, SimulatedWeatherProvider
        from ..integrations.simulated.providers import SimClock

        # Simulate three extra days so that 48-hour "forecasts" have real future truth to draw on.
        cfg = SimConfig(seed=settings.sim_seed, days=settings.sim_days + 3)
        home = SimulatedHome(cfg)
        clock = SimClock(cfg.start + timedelta(days=settings.sim_days) - timedelta(hours=6))
        return Platform(settings, SimulatedTelemetryProvider(home, clock), SimulatedTariffProvider(home, clock),
                        SimulatedWeatherProvider(home, clock), SimulatedSolarProvider(home, clock), clock, simulated_home=home)
    from ..integrations.homeassistant.client import HAClient
    from ..integrations.homeassistant.providers import HAOctopusTariffProvider, HASolcastProvider, HATelemetryProvider, HAWeatherProvider

    client = HAClient()
    return Platform(settings, HATelemetryProvider(client), HAOctopusTariffProvider(client), HAWeatherProvider(client), HASolcastProvider(client), _RealClock())
