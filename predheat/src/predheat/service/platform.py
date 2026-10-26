"""Wiring: chooses providers for the configured data mode and runs the collector.

The same ``Platform`` object serves the CLI, the API and the tests. It is
read-only with respect to equipment in every mode.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..analytics import allocate_heat_pump_categories, counter_to_intervals, reconcile
from ..config import DataMode, OperatingMode, Settings, get_settings
from ..db import init_db, session_scope
from ..db import models as m
from ..db.session import as_utc
from ..domain import CapabilityReport, Measurement, Provenance, Quality, RateInterval, Signal
from ..integrations.base import ProviderHealth
from ..model import COPModel, fit_cop_model, fit_rc_model, prior_cop_model, survey_to_coefficient
from ..optimiser import MPCInputs, build_mpc_plan, build_shadow_plan
from ..pricing import RATES_AS_OF, REGION_NAMES, PriceClass, SlotPrice, build_rate_book, classify_slots, load_templates, replay, standing_charge_for_period
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
    controller: Any = None
    settings_store: Any = None
    _memo: dict[tuple, Any] = field(default_factory=dict)

    def _cached(self, key: tuple, compute):
        """Cache a derived result until the next poll changes the data. Keeps the
        dashboard tabs instant instead of re-fitting models on every request."""
        stamp = self.last_poll.get("at")
        full = (stamp,) + key
        if full in self._memo:
            return self._memo[full]
        if len(self._memo) > 64:
            self._memo.clear()
        value = compute()
        self._memo[full] = value
        return value

    # ------------------------------------------------------------------ lifecycle
    async def bootstrap(self, backfill_days: int | None = None) -> None:
        init_db(self.settings.database_url)
        if self.settings.data_mode == DataMode.HOME_ASSISTANT and (self.settings.latitude is None or self.settings.longitude is None):
            try:
                cfg = await self.telemetry.client.config()
                self.settings.latitude = cfg.get("latitude", self.settings.latitude)
                self.settings.longitude = cfg.get("longitude", self.settings.longitude)
                if cfg.get("time_zone"):
                    self.settings.timezone = cfg["time_zone"]
                log.info("home location and time zone taken from Home Assistant")
            except Exception as exc:  # noqa: BLE001
                log.warning("could not read home location from Home Assistant: %s", exc)
        with session_scope(self.settings.database_url) as s:
            prop = repo.ensure_property(s, "Pilot property", self.settings.timezone,
                                        latitude=self.settings.latitude, longitude=self.settings.longitude)
            if self.settings.latitude is not None and prop.latitude != self.settings.latitude:
                prop.latitude, prop.longitude = self.settings.latitude, self.settings.longitude
            self.property_id = prop.id
            if self.controller is not None:
                self.controller.property_id = prop.id
            purged = repo.purge_misstamped_history(s, self.property_id)
            if purged:
                log.warning("removed %d mis-stamped history rows from an earlier release; re-backfilling", purged)
                repo.log_quality_event(s, self.property_id, "info", "migration", f"removed {purged} mis-stamped history rows; counters re-derived")
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
            if self.settings.operating_mode != OperatingMode.MONITOR:
                try:
                    result["shadow_run_id"] = self._record_shadow_plan(s)
                except Exception as exc:  # noqa: BLE001
                    result["shadow_error"] = str(exc)
                    repo.log_quality_event(s, self.property_id, "error", "shadow_planner", str(exc))
            if self.controller is not None:
                try:
                    result["control"] = (await self._control_step(s)).status
                except Exception as exc:  # noqa: BLE001
                    result["control_error"] = str(exc)
                    repo.log_quality_event(s, self.property_id, "error", "controller", str(exc))
            try:
                result["building_model_id"] = self._refresh_building_model(s)
            except Exception as exc:  # noqa: BLE001
                result["building_model_error"] = str(exc)
                repo.log_quality_event(s, self.property_id, "error", "thermal_model", str(exc))
            try:
                result["heat_pump_model_id"] = self._refresh_heat_pump_model(s)
            except Exception as exc:  # noqa: BLE001
                result["heat_pump_model_error"] = str(exc)
                repo.log_quality_event(s, self.property_id, "error", "heat_pump_model", str(exc))
        self.last_poll.update(result)
        return result

    # ------------------------------------------------------------------ heat loss (1R1C fit)
    def thermal_estimate(self, s, days: int = 14, now: datetime | None = None) -> dict[str, Any]:
        now = now or self.clock.now()
        return self._cached(("thermal", days, now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)),
                            lambda: self._thermal_estimate(s, days, now))

    def _thermal_estimate(self, s, days: int, now: datetime) -> dict[str, Any]:
        """Fit the single-zone building model to recent history and report the heat loss.

        Inputs are resampled onto a half-hour grid: indoor and outdoor temperature
        as the last reading at each grid point (gaps over 2 h become NaN), heat
        delivered for space heating from the cumulative counter, and PV power as a
        solar proxy when present. The survey heat loss, if recorded on the property,
        acts as a weak prior. Everything here is read-only.
        """
        end = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
        start = end - timedelta(days=days)
        step = timedelta(minutes=30)
        n = int((end - start) / step)
        grid = [start + i * step for i in range(n + 1)]

        def series(sig: Signal) -> list[float]:
            rows = repo.measurements_for(s, self.property_id, sig.value, start - timedelta(hours=3), end)
            pts = [(as_utc(r.ts), r.value) for r in rows if r.value is not None]
            out, j, last = [], 0, None
            for g in grid:
                while j < len(pts) and pts[j][0] <= g:
                    last = pts[j]
                    j += 1
                out.append(last[1] if last and g - last[0] <= timedelta(hours=2) else math.nan)
            return out

        t_in, t_out = series(Signal.INDOOR_TEMPERATURE), series(Signal.OUTDOOR_TEMPERATURE)
        counter = repo.measurements_for(s, self.property_id, Signal.HP_HEAT_HEATING_KWH.value, start - timedelta(hours=3), end + step)
        ints = counter_to_intervals([Measurement(signal=Signal.HP_HEAT_HEATING_KWH, ts=as_utc(r.ts), value=r.value, quality=Quality(r.quality),
                                                 provenance=Provenance(r.provenance), source_ref=r.source_ref) for r in counter], "heating")
        kwh_by_start = {as_utc(i.start): i.kwh for i in ints}
        q_kw = [kwh_by_start.get(g, math.nan) / 0.5 if g in kwh_by_start else math.nan for g in grid]
        pv = series(Signal.PV_POWER)
        have_pv = any(not math.isnan(v) for v in pv)
        usable = sum(1 for a, b, c in zip(t_in, t_out, q_kw) if not (math.isnan(a) or math.isnan(b) or math.isnan(c)))
        base = {"window_start": start.isoformat(), "window_end": end.isoformat(), "samples": usable, "model_type": "1R1C",
                "solar_proxy": "pv_power" if have_pv else None}
        if usable < 48:
            return {**base, "status": "insufficient_data",
                    "reason": f"Only {usable} usable half-hours of indoor, outdoor and heat data in the last {days} days; need at least 48 (one day)."}
        prop = s.get(m.Property, self.property_id)
        survey = None
        if prop and prop.survey_heat_loss_kw and prop.survey_design_indoor_c is not None and prop.survey_design_outdoor_c is not None:
            survey = survey_to_coefficient(prop.survey_heat_loss_kw, prop.survey_design_indoor_c, prop.survey_design_outdoor_c)
        fit = fit_rc_model(t_in, t_out, q_kw, pv if have_pv else None, step_hours=0.5, survey_ua_w_per_k=survey)
        if not math.isfinite(fit.ua_w_per_k) or fit.ua_w_per_k <= 0:
            return {**base, "status": "not_identified", "reason": "The data so far does not pin down a physical heat loss. " + "; ".join(fit.notes or [])}
        ua = fit.ua_w_per_k
        cap = fit.capacity_kwh_per_k if math.isfinite(fit.capacity_kwh_per_k) else None
        ci_low = fit.ua_ci_low if fit.ua_ci_low is not None else -1.0
        if fit.confidence < 0.2 or ci_low <= 0 or ua > 2000:
            return {**base, "status": "learning", "confidence": round(fit.confidence, 2),
                    "reason": f"Fitted so far: {ua:.0f} W/K, but the range still includes zero or the confidence is {fit.confidence:.0%}. "
                              "More days with heating on and varied outdoor temperature will narrow it."
                              + (" " + "; ".join(fit.notes) if fit.notes else "")}
        return {**base, "status": "ok", "heat_loss_w_per_k": round(ua, 1),
                "ci_low_w_per_k": round(fit.ua_ci_low, 1) if fit.ua_ci_low is not None else None,
                "ci_high_w_per_k": round(fit.ua_ci_high, 1) if fit.ua_ci_high is not None else None,
                "design_kw_at_minus_2": round(ua * 23.0 / 1000.0, 2),  # 21 °C inside, -2 °C outside
                "capacity_kwh_per_k": round(cap, 2) if cap else None,
                "time_constant_h": round(cap * 1000.0 / ua, 1) if cap else None,
                "internal_gain_kw": round(fit.internal_gain_kw, 2) if math.isfinite(fit.internal_gain_kw) else None,
                "solar_aperture": round(fit.solar_aperture_m2, 2) if have_pv and math.isfinite(fit.solar_aperture_m2) else None,
                "confidence": round(fit.confidence, 2), "rmse_c": round(fit.rmse_c, 3) if fit.rmse_c is not None else None,
                "survey_w_per_k": round(survey, 1) if survey else None, "notes": fit.notes or []}

    def _refresh_building_model(self, s, min_age: timedelta = timedelta(hours=6)) -> int | None:
        """Persist a new BuildingModelVersion at most every ``min_age``."""
        now = self.clock.now()
        latest = s.query(m.BuildingModelVersion).filter_by(property_id=self.property_id).order_by(m.BuildingModelVersion.created_at.desc()).first()
        if latest and now - as_utc(latest.created_at) < min_age:
            return latest.id
        est = self.thermal_estimate(s, now=now)
        if est["status"] != "ok":
            return latest.id if latest else None
        row = m.BuildingModelVersion(property_id=self.property_id, created_at=now, model_type="1R1C",
                                     heat_loss_w_per_k=est["heat_loss_w_per_k"], heat_loss_ci_low=est["ci_low_w_per_k"],
                                     heat_loss_ci_high=est["ci_high_w_per_k"], thermal_capacity_kwh_per_k=est["capacity_kwh_per_k"],
                                     solar_gain_factor=est["solar_aperture"], internal_gain_kw=est["internal_gain_kw"],
                                     confidence=est["confidence"], training_window_start=datetime.fromisoformat(est["window_start"]),
                                     training_window_end=datetime.fromisoformat(est["window_end"]),
                                     metrics={"rmse_c": est["rmse_c"], "samples": est["samples"], "notes": est["notes"]})
        s.add(row)
        s.flush()
        return row.id

    # ------------------------------------------------------------------ tariff comparison
    def compare_tariffs(self, s, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
        now = now or self.clock.now()
        return self._cached(("tariffs", days, now.date()), lambda: self._compare_tariffs(s, days, now))

    def _compare_tariffs(self, s, days: int, now: datetime) -> dict[str, Any]:
        """Replay the last ``days`` of half-hourly import on each tariff template."""
        today = now.astimezone(LONDON).date()
        start, _ = local_day_bounds(today - timedelta(days=days))
        _, end = local_day_bounds(today - timedelta(days=1))

        def counter(sig: Signal):
            rows = repo.measurements_for(s, self.property_id, sig.value, start - timedelta(hours=1), end + timedelta(hours=1))
            return [Measurement(signal=sig, ts=as_utc(r.ts), value=r.value, quality=Quality(r.quality), provenance=Provenance(r.provenance), source_ref=r.source_ref) for r in rows]

        source = "whole_home_import"
        ints = [i for i in counter_to_intervals(counter(Signal.GRID_IMPORT_KWH), "grid_import") if start <= i.start < end]
        if sum(i.kwh for i in ints) < 1.0:
            source = "heat_pump_only"
            heat_e = counter_to_intervals(counter(Signal.HP_ELEC_HEATING_KWH), "e")
            dhw_e = counter_to_intervals(counter(Signal.HP_ELEC_DHW_KWH), "d")
            by: dict[datetime, float] = {}
            for i in heat_e + dhw_e:
                if start <= i.start < end:
                    by[i.start] = by.get(i.start, 0.0) + i.kwh
            from ..analytics.energy_accounting import EnergyInterval
            ints = [EnergyInterval(k, k + timedelta(minutes=30), v, Quality.MODEL_DERIVED, "hp") for k, v in sorted(by.items())]
        triples = [(as_utc(i.start), as_utc(i.end), i.kwh) for i in ints]
        total_kwh = sum(k for _, _, k in triples)
        if total_kwh < 1.0:
            return {"status": "no_data", "reason": "Not enough consumption history yet.", "days": days, "results": []}
        # what it actually cost on the current tariff, priced slot by slot
        tariff_code = self.last_poll.get("tariff_code")
        actual = None
        if tariff_code:
            book = build_rate_book([_ri(r) for r in repo.rates_for(s, tariff_code, start, end)], tariff_code)
            cost = 0.0
            complete = True
            for st, en, k in triples:
                c, _eff, ok = book.cost_pence(st, en, k)
                cost += c
                complete = complete and ok
            charges = s.query(m.TariffStandingCharge).filter_by(tariff_code=tariff_code).all()
            from ..domain import StandingCharge
            sc = standing_charge_for_period([StandingCharge(start=as_utc(c.start), end=as_utc(c.end), pence_per_day=c.pence_per_day, tariff_code=c.tariff_code) for c in charges], start, end)
            actual = {"code": tariff_code, "name": "Your current tariff (actual prices)", "unit_pence": round(cost, 1), "standing_pence": round(sc, 1),
                      "total_pence": round(cost + sc, 1), "kwh": round(total_kwh, 2), "prices_complete": complete,
                      "effective_p_per_kwh": round(cost / total_kwh, 2)}
        results = []
        for t in load_templates(self.settings.data_dir):
            if t.code == "_error":
                results.append({"code": t.code, "name": t.name, "replayable": False, "note": t.description})
                continue
            if not t.replayable:
                results.append({"code": t.code, "name": t.name, "description": t.description, "replayable": False, "note": t.note, "requires": t.requires})
                continue
            r = replay(t, triples)
            r.update({"description": t.description, "replayable": True, "requires": t.requires, "note": t.note,
                      "annualised_pounds": round(r["total_pence"] / 100 / max(r["days"], 1) * 365, 0)})
            results.append(r)
        ranked = sorted([r for r in results if r.get("replayable")], key=lambda r: r["total_pence"])
        best = ranked[0] if ranked else None
        region = tariff_code.rsplit("-", 1)[-1] if tariff_code and "-" in tariff_code else None
        if actual and best:
            actual["difference_vs_best_pence"] = round(actual["total_pence"] - best["total_pence"], 1)
            actual["product"] = next((r["code"] for r in ranked if r["code"] in (tariff_code or "")), None)
            actual["on_cheapest"] = actual["product"] == best["code"]
        return {"status": "ok", "days": days, "period": {"start": start.isoformat(), "end": end.isoformat()}, "source": source,
                "kwh": round(total_kwh, 2), "rates_as_of": RATES_AS_OF, "region": region, "region_name": REGION_NAMES.get(region or "", None),
                "actual": actual, "results": ranked + [r for r in results if not r.get("replayable")],
                "caveats": ["Replay of your consumption as it happened. A tariff with different cheap windows would change when PredHeat heats, so the optimised figure would be lower still.",
                            f"Unit rates are indicative published prices at the date shown, not your region's exact prices{' (your tariff code says region ' + region + (', ' + REGION_NAMES[region] if region in REGION_NAMES else '') + ')' if region else ''}; check before switching.",
                            "Exit fees, eligibility (EV, smart meter) and export rates are not included."]}

    # ------------------------------------------------------------------ control
    async def _control_step(self, s):
        now = self.clock.now()
        plan = self.shadow_plan(s, now)
        latest = repo.latest_measurements(s, self.property_id)
        r = latest.get(Signal.INDOOR_TEMPERATURE.value)
        indoor = r.value if r else None
        age = (now - as_utc(r.ts)) if r else None
        est = self.thermal_estimate(s, now=now)
        conf = est.get("confidence") if est.get("status") == "ok" else None
        return await self.controller.step(s, now, plan, indoor, age, conf)

    async def release_control(self, reason: str = "shutdown") -> None:
        if self.controller is None:
            return
        with session_scope(self.settings.database_url) as s:
            await self.controller.release(s, self.clock.now(), reason)

    def control_status(self) -> dict[str, Any]:
        if self.controller is None:
            return {"configured": False, "reason": "no controller: control is disabled or no climate entity is configured", "holding": False}
        return self.controller.status()

    # ------------------------------------------------------------------ shadow planning
    def _price_slots(self, s, start: datetime, end: datetime) -> list[SlotPrice]:
        tariff_code = self.last_poll.get("tariff_code")
        if not tariff_code:
            return []
        book = build_rate_book([_ri(r) for r in repo.rates_for(s, tariff_code, start, end)], tariff_code)
        disp = s.query(m.SmartChargingDispatch).filter(m.SmartChargingDispatch.property_id == self.property_id,
                                                        m.SmartChargingDispatch.end > start - timedelta(days=1)).all()
        from ..domain import Dispatch, DispatchKind
        dispatches = [Dispatch(kind=DispatchKind(d.kind), start=as_utc(d.start), end=as_utc(d.end), charge_kwh=d.charge_kwh,
                               source=d.source, location=d.location, observed_at=as_utc(d.observed_at)) for d in disp]
        return classify_slots(start, end, book, dispatches)

    def _latest_weather(self, s) -> list[m.WeatherForecast]:
        latest_issue = s.query(m.WeatherForecast.issued_at).filter_by(property_id=self.property_id).order_by(m.WeatherForecast.issued_at.desc()).first()
        if not latest_issue:
            return []
        return s.query(m.WeatherForecast).filter_by(property_id=self.property_id, issued_at=latest_issue[0]).order_by(m.WeatherForecast.valid_at).all()

    def shadow_plan(self, s, now: datetime | None = None) -> dict[str, Any]:
        """What PredHeat would do over the next 24 hours. Read-only.

        Uses the model-based optimiser once the building model is identified with
        reasonable confidence; until then the rule-based plan, and says which.
        """
        now = now or self.clock.now()
        return self._cached(("plan", now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)),
                            lambda: self._shadow_plan(s, now))

    def _shadow_plan(self, s, now: datetime) -> dict[str, Any]:
        start = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
        slots = self._price_slots(s, start, start + timedelta(hours=25))
        latest = repo.latest_measurements(s, self.property_id)

        def value(sig: Signal) -> float | None:
            r = latest.get(sig.value)
            return r.value if r and r.value is not None and (now - as_utc(r.ts)) < timedelta(hours=3) else None

        forecast = [(as_utc(w.valid_at), w.temperature_c) for w in self._latest_weather(s) if w.temperature_c is not None]
        est = self.thermal_estimate(s, now=now)
        hp = self.heat_pump_estimate(s, now=now)
        t_in0 = value(Signal.INDOOR_TEMPERATURE)
        priced = [sp for sp in slots if not math.isnan(sp.rate_p_per_kwh) and sp.end > now][:48]
        why_not = None
        if est["status"] != "ok":
            why_not = f"building model not ready ({est.get('reason', est['status'])})"
        elif est["confidence"] < 0.3:
            why_not = f"building model confidence {est['confidence']:.0%} is below 30%"
        elif t_in0 is None:
            why_not = "no fresh indoor temperature reading"
        elif len(priced) < 12:
            why_not = "fewer than 6 hours of prices ahead"
        elif not forecast:
            why_not = "no weather forecast stored"
        if why_not:
            plan = build_shadow_plan(now, slots, dhw_tank_c=value(Signal.DHW_TANK_TEMPERATURE),
                                     dhw_setpoint_c=value(Signal.DHW_SETPOINT), outdoor_forecast=forecast)
            plan["assumptions"] = [f"Rule-based plan because the {why_not}. The learned house model takes over automatically once it is ready."] + [
                a for a in plan["assumptions"] if not a.startswith("Rule-based")]
            plan["model"] = {"building": est["status"], "heat_pump": hp["status"]}
            return plan
        prop = s.get(m.Property, self.property_id)
        setpoint = value(Signal.INDOOR_SETPOINT) or (prop.preferred_temp_c if prop else 21.0)
        tolerance = prop.comfort_tolerance_c if prop else 1.0
        fc_sorted = sorted(forecast)

        def t_out_at(t: datetime) -> float:
            # nearest forecast hour; falls back to the latest outdoor reading
            best = min(fc_sorted, key=lambda f: abs((f[0] - t).total_seconds()))
            if abs((best[0] - t).total_seconds()) <= 5400:
                return best[1]
            return value(Signal.OUTDOOR_TEMPERATURE) if value(Signal.OUTDOOR_TEMPERATURE) is not None else best[1]

        cop_model = COPModel(hp["model"]["a"], hp["model"]["b"])
        inp = MPCInputs(starts=[sp.start for sp in priced], rate_p=[sp.rate_p_per_kwh for sp in priced],
                        t_out=[t_out_at(sp.start) for sp in priced], ua_w_per_k=est["heat_loss_w_per_k"],
                        capacity_kwh_per_k=est["capacity_kwh_per_k"] or 5.0, internal_gain_kw=est["internal_gain_kw"] or 0.0,
                        cop_fn=cop_model.cop_at, hp_capacity_kw=hp["capacity_kw"], t_in0=t_in0, setpoint_c=setpoint,
                        tolerance_c=tolerance, dhw_tank_c=value(Signal.DHW_TANK_TEMPERATURE), dhw_setpoint_c=value(Signal.DHW_SETPOINT))
        notes = [f"Building model confidence {est['confidence']:.0%} from {est['samples']} half-hours; heat pump model {hp['status']} "
                 f"({hp['samples']} hours, confidence {hp['confidence']:.0%})."]
        plan = build_mpc_plan(inp, now, model_notes=notes)
        plan["model"] = {"building": est["status"], "heat_pump": hp["status"], "heat_loss_w_per_k": est["heat_loss_w_per_k"],
                         "cop_at_7": round(cop_model.cop_at(7.0), 2), "capacity_kw": hp["capacity_kw"], "setpoint_c": setpoint, "tolerance_c": tolerance}
        return plan

    # ------------------------------------------------------------------ heat pump efficiency (COP model)
    def heat_pump_estimate(self, s, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
        now = now or self.clock.now()
        return self._cached(("heat_pump", days, now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)),
                            lambda: self._heat_pump_estimate(s, days, now))

    def _heat_pump_estimate(self, s, days: int, now: datetime) -> dict[str, Any]:
        """COP against outdoor temperature from hourly electricity and heat, plus observed capacity."""
        start = now - timedelta(days=days)

        def counter(sig: Signal):
            rows = repo.measurements_for(s, self.property_id, sig.value, start - timedelta(hours=3), now)
            return [Measurement(signal=sig, ts=as_utc(r.ts), value=r.value, quality=Quality(r.quality), provenance=Provenance(r.provenance), source_ref=r.source_ref) for r in rows]

        elec = counter_to_intervals(counter(Signal.HP_ELEC_HEATING_KWH), "e")
        heat = counter_to_intervals(counter(Signal.HP_HEAT_HEATING_KWH), "h")
        e_h: dict[datetime, float] = {}
        h_h: dict[datetime, float] = {}
        for i in elec:
            e_h[as_utc(i.start).replace(minute=0)] = e_h.get(as_utc(i.start).replace(minute=0), 0.0) + i.kwh
        for i in heat:
            h_h[as_utc(i.start).replace(minute=0)] = h_h.get(as_utc(i.start).replace(minute=0), 0.0) + i.kwh
        t_h: dict[datetime, list[float]] = {}
        for r in repo.measurements_for(s, self.property_id, Signal.OUTDOOR_TEMPERATURE.value, start, now):
            if r.value is not None:
                t_h.setdefault(as_utc(r.ts).replace(minute=0, second=0, microsecond=0), []).append(r.value)
        hours = sorted(set(e_h) & set(h_h) & set(t_h))
        t_out = [sum(t_h[h]) / len(t_h[h]) for h in hours]
        model = fit_cop_model(t_out, [e_h[h] for h in hours], [h_h[h] for h in hours]) if hours else prior_cop_model()
        half_hour_kw = sorted(i.kwh / 0.5 for i in heat if i.kwh > 0)
        capacity = half_hour_kw[int(len(half_hour_kw) * 0.95)] if len(half_hour_kw) >= 20 else 7.0
        capacity = float(max(3.0, min(16.0, capacity)))
        status = "ok" if model.n_samples >= 24 else "prior" if model.n_samples == 0 else "early"
        return {"status": status, "model_type": "cop_linear_t_out", "cop_at_7": round(model.cop_at(7.0), 2),
                "cop_at_2": round(model.cop_at(2.0), 2), "cop_at_minus_2": round(model.cop_at(-2.0), 2),
                "slope_per_degc": round(model.b, 3), "observed_mean_cop": round(model.observed_mean, 2) if model.observed_mean else None,
                "samples": model.n_samples, "confidence": model.confidence, "rmse": round(model.rmse, 3) if model.rmse is not None else None,
                "t_range": [model.t_min, model.t_max], "capacity_kw": round(capacity, 1),
                "capacity_source": "observed 95th percentile" if len(half_hour_kw) >= 20 else "default 7 kW",
                "window_start": start.isoformat(), "window_end": now.isoformat(), "notes": model.notes, "model": model.as_dict()}

    def _refresh_heat_pump_model(self, s, min_age: timedelta = timedelta(hours=6)) -> int | None:
        now = self.clock.now()
        latest = s.query(m.HeatPumpModelVersion).filter_by(property_id=self.property_id).order_by(m.HeatPumpModelVersion.created_at.desc()).first()
        if latest and now - as_utc(latest.created_at) < min_age:
            return latest.id
        est = self.heat_pump_estimate(s, now=now)
        if est["status"] == "prior":
            return latest.id if latest else None
        row = m.HeatPumpModelVersion(property_id=self.property_id, created_at=now, model_type=est["model_type"],
                                     parameters={"a": est["model"]["a"], "b": est["model"]["b"], "capacity_kw": est["capacity_kw"]},
                                     confidence=est["confidence"], metrics={"samples": est["samples"], "rmse": est["rmse"], "notes": est["notes"]})
        s.add(row)
        s.flush()
        return row.id

    # ------------------------------------------------------------------ forecast accuracy
    def forecast_accuracy(self, s, days: int = 7, now: datetime | None = None) -> dict[str, Any]:
        """How well the model predicted indoor temperature in recorded plans.

        The house runs its own thermostat in monitor and shadow modes, so the
        baseline trajectory (thermostat on the model) is the one to score. The
        optimised trajectory is reported too for completeness.
        """
        now = now or self.clock.now()
        runs = (s.query(m.OptimisationRun).filter(m.OptimisationRun.property_id == self.property_id,
                                                    m.OptimisationRun.issued_at >= now - timedelta(days=days))
                .order_by(m.OptimisationRun.issued_at).all())
        runs = [r for r in runs if isinstance(r.slots, dict) and r.slots.get("predicted")]
        if not runs:
            return {"status": "no_data", "reason": "Nothing to score yet. Plans are recorded in shadow mode and scored once their 24 hours have passed.", "runs": 0, "points": 0}
        earliest = min(as_utc(r.horizon_start) for r in runs)
        meas = [(as_utc(r.ts), r.value) for r in repo.measurements_for(s, self.property_id, Signal.INDOOR_TEMPERATURE.value, earliest, now) if r.value is not None]
        import bisect
        times = [t for t, _ in meas]

        def actual_at(t: datetime) -> float | None:
            if not meas:
                return None
            i = bisect.bisect_left(times, t)
            cands = [meas[j] for j in (i - 1, i) if 0 <= j < len(meas)]
            best = min(cands, key=lambda c: abs((c[0] - t).total_seconds()), default=None)
            return best[1] if best and abs((best[0] - t).total_seconds()) <= 1200 else None

        eb, eo, by_run = [], [], []
        for r in runs:
            rb, ro = [], []
            for p in r.slots["predicted"]:
                t = datetime.fromisoformat(p["start"]) + timedelta(minutes=30)
                if t > now:
                    continue
                a = actual_at(t)
                if a is None:
                    continue
                rb.append(p["t_in_baseline"] - a)
                ro.append(p["t_in"] - a)
            if rb:
                eb += rb
                eo += ro
                by_run.append({"issued_at": as_utc(r.issued_at).isoformat(), "points": len(rb),
                               "rmse_baseline_c": round(math.sqrt(sum(x * x for x in rb) / len(rb)), 2),
                               "bias_baseline_c": round(sum(rb) / len(rb), 2)})
        if not eb:
            return {"status": "waiting", "reason": f"{len(runs)} plans recorded; the first will be scored once its 24 hours have passed.", "runs": len(runs), "points": 0}
        rmse_b = math.sqrt(sum(x * x for x in eb) / len(eb))
        return {"status": "ok", "runs": len(by_run), "points": len(eb),
                "rmse_baseline_c": round(rmse_b, 2), "mae_baseline_c": round(sum(abs(x) for x in eb) / len(eb), 2),
                "bias_baseline_c": round(sum(eb) / len(eb), 2),
                "rmse_optimised_c": round(math.sqrt(sum(x * x for x in eo) / len(eo)), 2),
                "target_rmse_c": 0.5, "meets_target": rmse_b <= 0.5, "by_run": by_run[-14:],
                "note": "Baseline is the model running a plain thermostat, which is what the house actually does until control is enabled."}

    def _record_shadow_plan(self, s) -> int | None:
        plan = self.shadow_plan(s)
        if plan["status"] not in ("indicative", "model_based"):
            return None
        cost = plan.get("cost") or {}
        run = m.OptimisationRun(property_id=self.property_id, issued_at=self.clock.now(),
                                horizon_start=datetime.fromisoformat(plan["horizon_start"]),
                                horizon_end=datetime.fromisoformat(plan["horizon_end"]),
                                mode=self.settings.operating_mode.value, tariff_code=self.last_poll.get("tariff_code"),
                                predicted_cost_pence=cost.get("optimised_p"), baseline_cost_pence=cost.get("baseline_p"),
                                slots={"kind": plan["status"], "actions": plan["actions"], "predicted": plan.get("predicted", [])}, status="shadow")
        s.add(run)
        s.flush()
        return run.id

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
            full = self.shadow_plan(s, now)
            plan = {"status": full["status"], "reason": full["reason"], "actions": full["actions"], "cost": full.get("cost"), "model": full.get("model")}
            heat_loss = self.thermal_estimate(s, now=now)
            heat_pump = self.heat_pump_estimate(s, now=now)
            heat_pump.pop("model", None)
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
                "plan": plan,
                "next_action": full["next_action"],
                "heat_loss": heat_loss,
                "heat_pump": heat_pump,
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
        heat_total_th = heat_kwh_th + dhw_kwh_th
        eff = max(0.5, min(1.0, self.settings.boiler_efficiency))
        gas_kwh = heat_total_th / eff
        gas_cost = round(gas_kwh * self.settings.gas_price_p_per_kwh, 2)
        return {
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "tariff_code": tariff_code,
            "gas_equivalent": {"kwh_gas": round(gas_kwh, 3), "cost_pence": gas_cost, "price_p_per_kwh": self.settings.gas_price_p_per_kwh,
                               "boiler_efficiency": eff, "saving_pence": round(gas_cost - (hc + dc), 2),
                               "note": "Modelled what-if: the same delivered heat from a gas boiler at the configured efficiency and unit price. Standing charges excluded on both sides."},
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
                            "standing_charge_pence": c["standing_charge_pence"], "gas_equivalent": c["gas_equivalent"]})
                d += timedelta(days=1)
        return out

    def timeline(self, hours: int = 48) -> dict[str, Any]:
        """Forecast timeline: prices by class, weather, solar, and the rule-based shadow plan."""
        now = self.clock.now()
        start = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
        end = start + timedelta(hours=hours)
        with session_scope(self.settings.database_url) as s:
            tariff_code = self.last_poll.get("tariff_code")
            slots = []
            for sp in self._price_slots(s, start, end):
                slots.append({"start": sp.start.isoformat(), "end": sp.end.isoformat(), "rate_p": None if sp.rate_p_per_kwh != sp.rate_p_per_kwh else sp.rate_p_per_kwh,
                              "price_class": sp.price_class.value, "assumed": sp.assumed})
            weather = [{"valid_at": as_utc(w.valid_at).isoformat(), "temperature_c": w.temperature_c, "cloud_cover_pct": w.cloud_cover_pct, "wind_speed_ms": w.wind_speed_ms}
                       for w in self._latest_weather(s)]
            plan = self.shadow_plan(s, now)
            latest_solar = s.query(m.SolarForecast.issued_at).filter_by(property_id=self.property_id).order_by(m.SolarForecast.issued_at.desc()).first()
            solar = []
            if latest_solar:
                for p in s.query(m.SolarForecast).filter_by(property_id=self.property_id, issued_at=latest_solar[0]).order_by(m.SolarForecast.period_start).all():
                    solar.append({"period_start": as_utc(p.period_start).isoformat(), "pv_kw": p.pv_estimate_kw, "pv10_kw": p.pv_estimate10_kw, "pv90_kw": p.pv_estimate90_kw})
            summary = {c.value: sum(1 for x in slots if x["price_class"] == c.value) for c in PriceClass}
        return {"generated_at": now.isoformat(), "horizon_hours": hours, "tariff_code": tariff_code, "slots": slots,
                "slot_summary": summary, "weather": weather, "solar": solar, "optimisation": plan}

    def system_health(self, provider_health: list[ProviderHealth]) -> dict[str, Any]:
        now = self.clock.now()
        with session_scope(self.settings.database_url) as s:
            latest = repo.latest_measurements(s, self.property_id)
            signals = []
            for cap in (self.capability_report.capabilities if self.capability_report else []):
                r = latest.get(cap.signal.value)
                age = (now - as_utc(r.ts)).total_seconds() / 60 if r else None
                notes = cap.notes
                if cap.status != "available" and r is not None:
                    notes = (notes + "; " if notes else "") + "value shown is from an earlier mapping, not updated"
                signals.append({"signal": cap.signal.value, "status": cap.status, "quality": cap.quality.value, "source": cap.source_ref,
                                "confidence": cap.confidence, "last_value": (r.value if r else None), "last_text": (r.text if r else None),
                                "age_minutes": round(age, 1) if age is not None else None, "stale": (age is not None and age > 120), "notes": notes})
            events = [{"ts": as_utc(e.ts).isoformat(), "severity": e.severity, "source": e.source, "message": e.message}
                      for e in s.query(m.DataQualityEvent).filter_by(property_id=self.property_id).order_by(m.DataQualityEvent.ts.desc()).limit(25).all()]
            counts = {"measurements": s.query(m.SensorMeasurement).filter_by(property_id=self.property_id).count(),
                      "rate_intervals": s.query(m.TariffRateInterval).count(),
                      "weather_points": s.query(m.WeatherForecast).filter_by(property_id=self.property_id).count(),
                      "solar_points": s.query(m.SolarForecast).filter_by(property_id=self.property_id).count(),
                      "dispatches": s.query(m.SmartChargingDispatch).filter_by(property_id=self.property_id).count(),
                      "shadow_plans": s.query(m.OptimisationRun).filter_by(property_id=self.property_id).count(),
                      "building_models": s.query(m.BuildingModelVersion).filter_by(property_id=self.property_id).count(),
                      "heat_pump_models": s.query(m.HeatPumpModelVersion).filter_by(property_id=self.property_id).count(),
                      "control_actions": s.query(m.ControlExecutionLog).filter_by(property_id=self.property_id).count()}
        return {"generated_at": now.isoformat(), "data_mode": self.settings.data_mode.value, "operating_mode": self.settings.operating_mode.value,
                "control_enabled": self.settings.control_enabled,
                "providers": [{"name": h.provider, "ok": h.ok, "last_success_at": h.last_success_at.isoformat() if h.last_success_at else None, "last_error": h.last_error, "details": h.details} for h in provider_health],
                "integrations": self.capability_report.integrations if self.capability_report else [],
                "warnings": self.capability_report.warnings if self.capability_report else [],
                "signals": signals, "missing_for_thermal_model": [x.value for x in (self.capability_report.missing_for_thermal_model() if self.capability_report else [])],
                "recent_events": events, "record_counts": counts, "last_poll": self.last_poll,
                "forecast_accuracy": self.forecast_accuracy(s, now=now), "control": self.control_status()}


def _ri(r: m.TariffRateInterval) -> RateInterval:
    return RateInterval(start=as_utc(r.start), end=as_utc(r.end), rate_p_per_kwh=r.rate_p_per_kwh, tariff_code=r.tariff_code,
                        is_intelligent_adjusted=r.is_intelligent_adjusted, is_capped=r.is_capped, provenance=Provenance(r.provenance))


class _RealClock:
    def now(self) -> datetime:
        return now_utc()


def build_platform(settings: Settings | None = None, options_file: Path | None = None) -> Platform:
    settings = settings or get_settings()
    from .settings_store import SettingsStore

    store = SettingsStore.load(settings.data_dir, options_file)
    if store.path.is_file() or options_file is not None:
        store.apply(settings)  # a saved choice wins over the process defaults
    else:
        store.values.update({"operating_mode": settings.operating_mode.value, "gas_price_p_per_kwh": settings.gas_price_p_per_kwh,
                             "boiler_efficiency": settings.boiler_efficiency, "control_max_offset_c": settings.control_max_offset_c})
    if settings.data_mode == DataMode.SIMULATED:
        from ..integrations.simulated import SimConfig, SimulatedHome, SimulatedSolarProvider, SimulatedTariffProvider, SimulatedTelemetryProvider, SimulatedWeatherProvider
        from ..integrations.simulated.providers import SimClock

        # Simulate three extra days so that 48-hour "forecasts" have real future truth to draw on.
        cfg = SimConfig(seed=settings.sim_seed, days=settings.sim_days + 3)
        home = SimulatedHome(cfg)
        clock = SimClock(cfg.start + timedelta(days=settings.sim_days) - timedelta(hours=6))
        p = Platform(settings, SimulatedTelemetryProvider(home, clock), SimulatedTariffProvider(home, clock),
                     SimulatedWeatherProvider(home, clock), SimulatedSolarProvider(home, clock), clock, simulated_home=home)
        if settings.control_enabled and settings.control_climate_entity:
            from ..control import Controller
            from ..integrations.homeassistant.control import RecordingControlAdapter

            p.controller = Controller(settings, RecordingControlAdapter(initial=cfg.setpoint_c), property_id=0)
        p.settings_store = store
        return p
    from ..integrations.homeassistant.client import HAClient
    from ..integrations.homeassistant.providers import HAOctopusTariffProvider, HASolcastProvider, HATelemetryProvider, HAWeatherProvider

    client = HAClient()
    p = Platform(settings, HATelemetryProvider(client), HAOctopusTariffProvider(client), HAWeatherProvider(client), HASolcastProvider(client), _RealClock())
    if settings.control_enabled and settings.control_climate_entity:
        from ..control import Controller
        from ..integrations.homeassistant.control import HAControlAdapter

        p.controller = Controller(settings, HAControlAdapter(client), property_id=0)
        log.warning("Control is possible on %s (mode %s, max offset %.1f degC); it acts only in balanced or max_savings mode",
                    settings.control_climate_entity, settings.operating_mode.value, settings.control_max_offset_c)
    p.settings_store = store
    return p
