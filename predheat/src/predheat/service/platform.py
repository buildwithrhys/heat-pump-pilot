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
from ..domain import SIGNAL_CATALOGUE, CapabilityReport, Measurement, Provenance, Quality, RateInterval, Signal
from ..integrations.base import ProviderHealth
from ..model import DESIGN_INDOOR_C, COPModel, design_outdoor_c, fit_cop_model, fit_rc_model, prior_cop_model, survey_to_coefficient
from ..optimiser import MPCInputs, build_mpc_plan, build_shadow_plan
from ..pricing import RATES_AS_OF, REGION_NAMES, PriceClass, SlotPrice, build_rate_book, classify_slots, load_templates, replay, replay_with_book, scenario_intervals, standing_charge_for_period
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
    openmeteo: Any = None  # irradiance provider when a location is known
    octopus_api: Any = None  # direct rates for the tariff comparison
    seen_at: dict[str, datetime] = field(default_factory=dict)  # signal -> when its entity was last seen live
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
            if self.openmeteo is None and self.settings.data_mode == DataMode.HOME_ASSISTANT and prop.latitude is not None and prop.longitude is not None:
                from ..integrations.openmeteo import OpenMeteoProvider

                self.openmeteo = OpenMeteoProvider(prop.latitude, prop.longitude)
            if self.settings.survey_heat_loss_kw:
                prop.survey_heat_loss_kw = self.settings.survey_heat_loss_kw
                prop.survey_design_indoor_c = DESIGN_INDOOR_C
                prop.survey_design_outdoor_c = self.settings.survey_outdoor_c if self.settings.survey_outdoor_c is not None else design_outdoor_c(prop.latitude, prop.longitude)[0]
            self.property_id = prop.id
            if self.controller is not None:
                self.controller.property_id = prop.id
            relabelled = repo.relabel_statistics_rows(s, self.property_id)
            if relabelled:
                log.warning("tagged %d statistics rows as a separate source so they are never differenced against live readings", relabelled)
            purged = repo.purge_misstamped_history(s, self.property_id)
            if purged:
                log.warning("removed %d mis-stamped history rows from an earlier release; re-backfilling", purged)
                repo.log_quality_event(s, self.property_id, "info", "migration", f"removed {purged} mis-stamped history rows; counters re-derived")
        with session_scope(self.settings.database_url) as s:
            ov = repo.mapping_overrides(s, self.property_id)
            if hasattr(self.telemetry, "set_overrides"):
                self.telemetry.set_overrides({k: {"source_ref": r.source_ref, "unit_scale": r.unit_scale} for k, r in ov.items()})
        self.capability_report = await self.telemetry.discover()
        if self.settings_store is not None and not self.settings.control_climate_entity:
            suggested = self.climate_entities().get("suggested")
            if suggested:
                self.settings_store.update({"control_climate_entity": suggested}, self.settings)
                log.info("climate entity preselected: %s (control stays off until allowed)", suggested)
        with session_scope(self.settings.database_url) as s:
            for cap in self.capability_report.capabilities:
                if cap.status != "available":
                    continue
                existing = s.query(m.SensorMapping).filter_by(property_id=self.property_id, signal=cap.signal.value).one_or_none()
                if existing is None:
                    s.add(m.SensorMapping(property_id=self.property_id, signal=cap.signal.value, source_ref=cap.source_ref or "",
                                          provenance=self.capability_report.provenance.value, quality=cap.quality.value,
                                          confidence=cap.confidence, notes=cap.notes))
        if backfill_days is not None:
            days = backfill_days
        elif self.settings.data_mode == DataMode.SIMULATED:
            days = self.settings.sim_days
        else:
            # first start: the full window; later starts: top up the last two days
            with session_scope(self.settings.database_url) as s:
                earliest = s.query(m.SensorMeasurement.ts).filter_by(property_id=self.property_id).order_by(m.SensorMeasurement.ts).first()
            days = self.settings.backfill_days if earliest is None or (self.clock.now() - as_utc(earliest[0])) < timedelta(days=self.settings.backfill_days - 2) else 2
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
                now_seen = self.clock.now()
                for x in meas:
                    self.seen_at[x.signal.value] = now_seen
            except Exception as exc:  # noqa: BLE001
                result["measurements_error"] = str(exc)
                repo.log_quality_event(s, self.property_id, "error", self.telemetry.name, str(exc))
            try:
                snap = await self.tariff.snapshot()
                result["rates"] = repo.upsert_rates(s, snap.rates, snap.observed_at)
                repo.upsert_standing_charges(s, snap.standing_charges, snap.observed_at)
                result["dispatches"] = repo.record_dispatches(s, self.property_id, snap.dispatches)
                if hasattr(self.tariff, "metered_intervals"):
                    try:
                        result["metered_intervals"] = repo.upsert_metered_intervals(s, self.property_id, await self.tariff.metered_intervals())
                    except Exception as exc:  # noqa: BLE001
                        result["metered_error"] = str(exc)
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
            if self.openmeteo is not None:
                try:
                    result["irradiance"] = repo.upsert_measurements(s, self.property_id, await self.openmeteo.irradiance_history(1))
                except Exception as exc:  # noqa: BLE001
                    result["irradiance_error"] = str(exc)
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
                result["compacted"] = self._maybe_compact(s)
            except Exception as exc:  # noqa: BLE001
                result["compaction_error"] = str(exc)
            try:
                result["heat_pump_model_id"] = self._refresh_heat_pump_model(s)
            except Exception as exc:  # noqa: BLE001
                result["heat_pump_model_error"] = str(exc)
                repo.log_quality_event(s, self.property_id, "error", "heat_pump_model", str(exc))
        self.last_poll.update(result)
        return result

    def _maybe_compact(self, s, keep_full_days: int = 90) -> int:
        """Once a day, thin readings older than ``keep_full_days`` to hourly."""
        now = self.clock.now()
        prop = s.get(m.Property, self.property_id)
        extra = dict(prop.extra or {})
        last = extra.get("last_compaction")
        if last and now - datetime.fromisoformat(last) < timedelta(hours=24):
            return 0
        n = repo.compact_measurements(s, self.property_id, now - timedelta(days=keep_full_days))
        extra["last_compaction"] = now.isoformat()
        prop.extra = extra
        if n:
            log.info("compacted %d readings older than %d days to hourly", n, keep_full_days)
        return n

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
        solar proxy when present. A surveyed heat loss, if entered, acts as a weak
        prior. Below 30% confidence the result is a "waiting" state that says what
        is missing; no numbers are reported.
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
        irr = series(Signal.SOLAR_IRRADIANCE)
        have_irr = any(not math.isnan(v) for v in irr)
        pv = [v / 1000.0 for v in irr] if have_irr else series(Signal.PV_POWER)  # kW/m2 proxy or PV kW proxy
        have_pv = have_irr or any(not math.isnan(v) for v in pv)
        usable_idx = [i for i, (a, b, c) in enumerate(zip(t_in, t_out, q_kw)) if not (math.isnan(a) or math.isnan(b) or math.isnan(c))]
        usable = len(usable_idx)
        days_with_data = len({grid[i].astimezone(LONDON).date() for i in usable_idx})
        outdoor_seen = [t_out[i] for i in usable_idx]
        heat_on_hours = sum(0.5 for i in usable_idx if q_kw[i] > 0.2)
        prop = s.get(m.Property, self.property_id)
        design_c, region = (self.settings.design_outdoor_c, "your setting") if self.settings.design_outdoor_c is not None else design_outdoor_c(
            prop.latitude if prop else self.settings.latitude, prop.longitude if prop else self.settings.longitude)
        survey_kw = self.settings.survey_heat_loss_kw
        survey_at = self.settings.survey_outdoor_c if self.settings.survey_outdoor_c is not None else design_c
        survey_ua = survey_to_coefficient(survey_kw, DESIGN_INDOOR_C, survey_at) if survey_kw else None
        survey = ({"kw": survey_kw, "outdoor_c": survey_at, "ua_w_per_k": round(survey_ua, 1),
                   "kw_at_design": round(survey_ua * (DESIGN_INDOOR_C - design_c) / 1000.0, 2)} if survey_ua else None)
        base = {"window_start": start.isoformat(), "window_end": end.isoformat(), "lookback_days": days, "samples": usable,
                "days_with_data": days_with_data, "days_needed": 7, "model_type": "1R1C", "solar_proxy": "irradiance" if have_irr else ("pv_power" if have_pv else None),
                "design_outdoor_c": design_c, "design_indoor_c": DESIGN_INDOOR_C, "design_region": region, "survey": survey,
                "outdoor_range_c": [round(min(outdoor_seen), 1), round(max(outdoor_seen), 1)] if outdoor_seen else None,
                "heating_hours": round(heat_on_hours, 1), "rated_kw": self.settings.heat_pump_rated_kw}

        def waiting(_detail: str = "") -> dict[str, Any]:
            so_far = f"So far: {days_with_data} {'day' if days_with_data == 1 else 'days'}"
            if outdoor_seen:
                so_far += f", outdoor temperatures {min(outdoor_seen):.0f} to {max(outdoor_seen):.0f} °C"
            reason = ("PredHeat works out your home's heat loss from how fast it cools between heating cycles. "
                      "It needs about 7 days with the heating running and some colder nights. " + so_far + ".")
            return {**base, "status": "waiting", "reason": reason}

        if usable < 48:
            return waiting(f"Only {usable} usable half-hours of indoor, outdoor and heating data so far; it needs at least one full day.")
        fit = fit_rc_model(t_in, t_out, q_kw, pv if have_pv else None, step_hours=0.5, survey_ua_w_per_k=survey_ua)
        if not math.isfinite(fit.ua_w_per_k) or fit.ua_w_per_k <= 0:
            return waiting("The readings so far do not pin down a physical value yet. " + "; ".join(fit.notes or []))
        ua = fit.ua_w_per_k
        ci_low = fit.ua_ci_low if fit.ua_ci_low is not None else -1.0
        if fit.confidence < 0.3 or ci_low <= 0 or ua > 2000:
            return waiting(f"A first estimate exists but its confidence is only {fit.confidence:.0%}; PredHeat does not report a figure below 30%."
                           + (" " + "; ".join(fit.notes) if fit.notes else ""))
        cap = fit.capacity_kwh_per_k if math.isfinite(fit.capacity_kwh_per_k) and fit.capacity_kwh_per_k > 0 else None
        tau_h = cap * 1000.0 / ua if cap else None
        if tau_h is not None and not (2.0 <= tau_h <= 300.0):
            cap, tau_h = None, None  # not a credible capacity; report heat loss only
        gain = fit.internal_gain_kw if math.isfinite(fit.internal_gain_kw) and 0.0 <= fit.internal_gain_kw <= 3.0 else None
        ua_kw = ua / 1000.0
        design_kw = ua_kw * (DESIGN_INDOOR_C - design_c)
        cooling_at_5 = ((ua_kw * (DESIGN_INDOOR_C - 5.0) - (gain or 0.0)) / cap) if cap else None
        out = {**base, "status": "ok", "heat_loss_w_per_k": round(ua, 1),
               "ci_low_w_per_k": round(fit.ua_ci_low, 1), "ci_high_w_per_k": round(fit.ua_ci_high, 1),
               "design_kw": round(design_kw, 2),
               "design_kw_low": round(fit.ua_ci_low / 1000.0 * (DESIGN_INDOOR_C - design_c), 2),
               "design_kw_high": round(fit.ua_ci_high / 1000.0 * (DESIGN_INDOOR_C - design_c), 2),
               "capacity_kwh_per_k": round(cap, 2) if cap else None, "time_constant_h": round(tau_h, 1) if tau_h else None,
               "cooling_rate_c_per_h_at_5c": round(max(cooling_at_5, 0.0), 2) if cooling_at_5 is not None else None,
               "internal_gain_kw": round(gain, 2) if gain is not None else None,
               "confidence": round(fit.confidence, 2), "rmse_c": round(fit.rmse_c, 3) if fit.rmse_c is not None else None, "notes": fit.notes or []}
        if survey:
            diff = (design_kw - survey["kw_at_design"]) / survey["kw_at_design"]
            out["survey_difference_pct"] = round(diff * 100.0, 0)
            out["survey_disagrees"] = abs(diff) > 0.25
        rated = self.settings.heat_pump_rated_kw
        if rated:
            out["rated_margin_kw"] = round(rated - design_kw, 2)
        return out

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
                                     solar_gain_factor=None, internal_gain_kw=est["internal_gain_kw"],
                                     confidence=est["confidence"], training_window_start=datetime.fromisoformat(est["window_start"]),
                                     training_window_end=datetime.fromisoformat(est["window_end"]),
                                     metrics={"rmse_c": est["rmse_c"], "samples": est["samples"], "notes": est["notes"]})
        s.add(row)
        s.flush()
        return row.id

    def _cost_backtest(self, s, runs, now: datetime) -> dict[str, Any]:
        """Predicted thermostat cost for elapsed slots against what the heating actually cost, using the
        prices PredHeat had stored at the time. Scores the plan on what it knew then."""
        hp = counter_to_intervals([Measurement(signal=Signal.HP_ELEC_HEATING_KWH, ts=as_utc(r.ts), value=r.value, quality=Quality(r.quality),
                                               provenance=Provenance(r.provenance), source_ref=r.source_ref)
                                   for r in repo.measurements_for(s, self.property_id, Signal.HP_ELEC_HEATING_KWH.value,
                                                                  min(as_utc(r.horizon_start) for r in runs) - timedelta(hours=2), now)], "e")
        kwh_by_slot = {as_utc(i.start): i.kwh for i in hp}
        pred_p, actual_p, pred_kwh, actual_kwh, n = 0.0, 0.0, 0.0, 0.0, 0
        for r in runs:
            for p in r.slots.get("predicted", []):
                st = datetime.fromisoformat(p["start"])
                if st + timedelta(minutes=30) > now or "cost_p_baseline" not in p:
                    continue
                k = kwh_by_slot.get(st)
                if k is None:
                    continue
                pred_p += p["cost_p_baseline"]
                actual_p += k * p["rate_p"]
                pred_kwh += p["heat_kw_baseline"] / max(p.get("cop", 1.0) if "cop" in p else 3.0, 1.0) * 0.5
                actual_kwh += k
                n += 1
        if n == 0:
            return {"status": "waiting", "slots": 0}
        return {"status": "ok", "slots": n, "predicted_pence": round(pred_p, 1), "actual_pence": round(actual_p, 1),
                "predicted_kwh": round(pred_kwh, 2), "actual_kwh": round(actual_kwh, 2),
                "error_pct": round((pred_p - actual_p) / actual_p * 100.0, 1) if actual_p else None}

    # ------------------------------------------------------------------ annual forecast from history and climate normals
    def _annual_profile(self, s, now: datetime) -> dict[str, Any]:
        """Regress daily import on heating degree-days over the stored history and apply it to a
        typical year of monthly mean temperatures for the home's latitude. Indicative."""
        from ..pricing.climate import annual_degree_days, typical_year

        today = now.astimezone(LONDON).date()
        xs, ys = [], []
        for i in range(60, 0, -1):
            d = today - timedelta(days=i)
            start, end = local_day_bounds(d)
            temps = [r.value for r in repo.measurements_for(s, self.property_id, Signal.OUTDOOR_TEMPERATURE.value, start, end) if r.value is not None]
            if len(temps) < 12:
                continue
            hdd = sum(max(0.0, 15.5 - t) for t in temps) / len(temps)
            metered = repo.metered_intervals_for(s, self.property_id, start, end)
            if metered:
                kwh = sum(r.kwh for r in metered)
            else:
                c = self._energy_costs(s, d, d + timedelta(days=1))
                kwh = c["whole_home_import"]["kwh"] if c["whole_home_import"]["kwh"] > 0 and c["whole_home_import"].get("plausible", True) else c["heat_pump_total"]["kwh_electric"]
            if kwh > 0:
                xs.append(hdd)
                ys.append(kwh)
        if len(xs) < 7:
            return {"status": "waiting", "reason": f"Needs at least 7 days of consumption and outdoor temperature; has {len(xs)}.", "days": len(xs)}
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx > 1e-9 else 0.0
        intercept = max(0.0, my - slope * mx)
        prop = s.get(m.Property, self.property_id)
        year = typical_year(prop.latitude if prop else None)
        hdd_year = annual_degree_days(year)
        annual_kwh = intercept * 365 + max(slope, 0.0) * hdd_year
        return {"status": "ok", "days": n, "kwh_per_day_no_heating": round(intercept, 2), "kwh_per_degree_day": round(max(slope, 0.0), 3),
                "annual_degree_days": round(hdd_year, 0), "annual_kwh": round(annual_kwh, 0), "climate": year["label"],
                "note": "From a straight-line fit of daily electricity against how cold each day was, applied to a typical year for your area. Indicative; a mild or cold winter moves it by a fifth either way."}

    # ------------------------------------------------------------------ trial report
    def trial_report(self, s, days: int = 28, now: datetime | None = None) -> dict[str, Any]:
        """Alternating-day trial: heating cost per degree-day on optimised days against baseline days.

        Degree-days use a 15.5 °C base from the hourly outdoor temperature. The saving is the
        difference of the two means, with a standard error from the day-to-day spread, so the
        reader can see whether it is real or noise.
        """
        from ..control import Controller

        now = now or self.clock.now()
        today = now.astimezone(LONDON).date()
        rows = []
        for i in range(days, 0, -1):
            d = today - timedelta(days=i)
            start, end = local_day_bounds(d)
            temps = [r.value for r in repo.measurements_for(s, self.property_id, Signal.OUTDOOR_TEMPERATURE.value, start, end) if r.value is not None]
            if len(temps) < 12:
                continue
            hdd = sum(max(0.0, 15.5 - t) for t in temps) / len(temps)
            c = self._energy_costs(s, d, d + timedelta(days=1))
            kwh = c["space_heating"]["kwh_electric"]
            cost = c["space_heating"]["cost_pence"]
            if kwh <= 0.05 or hdd <= 0.5:
                continue
            baseline = Controller.trial_day_is_baseline(datetime(d.year, d.month, d.day, 12, tzinfo=LONDON))
            rows.append({"date": d.isoformat(), "group": "baseline" if baseline else "optimised", "degree_days": round(hdd, 2),
                         "kwh": round(kwh, 2), "cost_pence": round(cost, 1), "pence_per_degree_day": round(cost / hdd, 2), "kwh_per_degree_day": round(kwh / hdd, 3)})
        groups = {"baseline": [r["pence_per_degree_day"] for r in rows if r["group"] == "baseline"],
                  "optimised": [r["pence_per_degree_day"] for r in rows if r["group"] == "optimised"]}

        def stats(xs):
            if not xs:
                return {"n": 0, "mean": None, "se": None}
            mean = sum(xs) / len(xs)
            var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1) if len(xs) > 1 else 0.0
            return {"n": len(xs), "mean": round(mean, 2), "se": round(math.sqrt(var / len(xs)), 2) if len(xs) > 1 else None}

        b, o = stats(groups["baseline"]), stats(groups["optimised"])
        out: dict[str, Any] = {"enabled": self.settings.trial_mode, "days": rows, "baseline": b, "optimised": o,
                               "note": "Cost of space heating per degree-day (15.5 °C base) so cold and mild days compare fairly. Needs at least five days in each group before the difference means much."}
        if b["n"] >= 2 and o["n"] >= 2 and b["mean"]:
            diff = b["mean"] - o["mean"]
            se = math.sqrt((b["se"] or 0) ** 2 + (o["se"] or 0) ** 2)
            out.update({"saving_pence_per_degree_day": round(diff, 2), "saving_pct": round(diff / b["mean"] * 100.0, 1), "saving_se_pct": round(se / b["mean"] * 100.0, 1),
                        "credible": b["n"] >= 5 and o["n"] >= 5 and abs(diff) > 2 * se})
        return out

    # ------------------------------------------------------------------ control gate support
    def climate_entities(self) -> dict[str, Any]:
        """climate.* entities seen at discovery, with the myVAILLANT zone first and suggested when it is the only one."""
        states = getattr(self.telemetry, "last_states", {}) or {}
        rows = []
        for eid, st in states.items():
            if st.domain != "climate":
                continue
            rows.append({"entity_id": eid, "name": st.attributes.get("friendly_name") or eid, "platform": st.platform,
                         "state": st.state, "current_temperature": st.attributes.get("current_temperature"), "target": st.attributes.get("temperature")})
        rows.sort(key=lambda r: (r["platform"] != "mypyllant", r["entity_id"]))
        vaillant = [r for r in rows if r["platform"] == "mypyllant"]
        suggested = vaillant[0]["entity_id"] if len(vaillant) == 1 else (rows[0]["entity_id"] if len(rows) == 1 else None)
        heaters = [{"entity_id": eid, "name": st.attributes.get("friendly_name") or eid, "platform": st.platform, "state": st.state,
                    "current_temperature": st.attributes.get("current_temperature"), "target": st.attributes.get("temperature")}
                   for eid, st in states.items() if st.domain == "water_heater"]
        heaters.sort(key=lambda r: (r["platform"] != "mypyllant", r["entity_id"]))
        return {"entities": rows, "suggested": suggested, "water_heaters": heaters}

    def control_preconditions(self, s, now: datetime | None = None) -> dict[str, Any]:
        """What a person should have in place before switching Allow control on."""
        now = now or self.clock.now()
        shadow_days = len({as_utc(r.issued_at).astimezone(LONDON).date() for r in s.query(m.OptimisationRun.issued_at)
                          .filter(m.OptimisationRun.property_id == self.property_id, m.OptimisationRun.mode != "monitor").all()})
        est = self.thermal_estimate(s, now=now)
        acc = self.forecast_accuracy(s, now=now)
        entity = self.settings.control_climate_entity
        checks = [
            {"key": "shadow_days", "label": "At least 7 days in Shadow mode", "met": shadow_days >= 7, "detail": f"{shadow_days} days of recorded plans so far"},
            {"key": "heat_loss", "label": "Heat loss confidence 50% or above", "met": est.get("status") == "ok" and (est.get("confidence") or 0) >= 0.5,
             "detail": f"{(est.get('confidence') or 0):.0%}" if est.get("status") == "ok" else "still waiting for data"},
            {"key": "accuracy", "label": "Forecast accuracy under 0.5 °C", "met": acc.get("status") == "ok" and (acc.get("rmse_baseline_c") or 9) < 0.5,
             "detail": f"{acc.get('rmse_baseline_c')} °C RMSE" if acc.get("status") == "ok" else "no scored plans yet"},
            {"key": "entity", "label": "A climate entity chosen", "met": bool(entity), "detail": entity or "none"},
        ]
        return {"checks": checks, "all_met": all(c["met"] for c in checks), "max_offset_c": self.settings.control_max_offset_c}

    def log_gate_change(self, s, enabled: bool, acknowledged: bool, unmet: list[str]) -> None:
        s.add(m.ControlExecutionLog(property_id=self.property_id, ts=self.clock.now(), action="gate", target=self.settings.control_climate_entity,
                                    requested_value="on" if enabled else "off", previous_value=None, status="changed",
                                    reason=("Allow control switched on" + (f" with unmet preconditions acknowledged: {', '.join(unmet)}" if unmet else "")) if enabled else "Allow control switched off"))

    # ------------------------------------------------------------------ sensor mapping overrides
    def mapping_view(self, s) -> dict[str, Any]:
        overrides = repo.mapping_overrides(s, self.property_id)
        candidates = getattr(self.telemetry, "last_candidates", {}) or {}
        rows = []
        for cap in (self.capability_report.capabilities if self.capability_report else []):
            rows.append({"signal": cap.signal.value, "description": SIGNAL_CATALOGUE[cap.signal].description, "unit": SIGNAL_CATALOGUE[cap.signal].unit,
                         "source": cap.source_ref, "status": cap.status, "confidence": cap.confidence, "notes": cap.notes,
                         "overridden": cap.signal.value in overrides, "candidates": candidates.get(cap.signal.value, [])})
        return {"editable": hasattr(self.telemetry, "set_overrides"), "signals": rows}

    async def set_mapping(self, s, signal: str, entity_id: str | None) -> dict[str, Any]:
        if not hasattr(self.telemetry, "set_overrides"):
            raise ValueError("mapping can only be changed with a Home Assistant data source")
        sig = Signal(signal)
        spec = SIGNAL_CATALOGUE[sig]
        if entity_id:
            states = getattr(self.telemetry, "last_states", {}) or {}
            st = states.get(entity_id)
            if st is None:
                fresh = {x.entity_id: x for x in await self.telemetry.client.states()}
                st = fresh.get(entity_id)
                if st is None:
                    raise ValueError(f"{entity_id} is not a Home Assistant entity")
                states[entity_id] = st
            scale = spec.unit_scale.get(st.unit or "", 1.0) if spec.unit_scale else 1.0
            if spec.unit_scale and st.unit and st.unit not in spec.unit_scale:
                raise ValueError(f"{entity_id} reports {st.unit}, which cannot be converted to {spec.unit}")
            repo.set_mapping_override(s, self.property_id, signal, entity_id, scale, Provenance.HOME_ASSISTANT.value, spec.default_quality.value if spec.default_quality.value != "unavailable" else "sensor")
        else:
            repo.clear_mapping_override(s, self.property_id, signal)
        ov = repo.mapping_overrides(s, self.property_id)
        self.capability_report = self.telemetry.set_overrides({k: {"source_ref": r.source_ref, "unit_scale": r.unit_scale} for k, r in ov.items()}, self.capability_report)
        if not entity_id:
            # back to discovery for this signal: rerun discovery for an accurate report
            self.capability_report = await self.telemetry.discover()
        self._memo.clear()
        return self.mapping_view(s)

    # ------------------------------------------------------------------ tariff comparison
    def compare_tariffs(self, s, days: int = 30, now: datetime | None = None, ev_kwh_per_day: float = 0.0, battery_kwh_per_day: float = 0.0) -> dict[str, Any]:
        now = now or self.clock.now()
        return self._cached(("tariffs", days, now.date(), ev_kwh_per_day, battery_kwh_per_day),
                            lambda: self._compare_tariffs(s, days, now, ev_kwh_per_day, battery_kwh_per_day))

    async def refresh_live_rates(self, s, days: int, now: datetime | None = None) -> dict[str, Any]:
        """Fetch each template's real unit rates for your region from the Octopus API and keep them
        as rate intervals, so the replay uses published prices rather than indicative ones. Also
        makes Agile replayable."""
        if self.octopus_api is None:
            return {"status": "unavailable", "reason": "no API client"}
        now = now or self.clock.now()
        tariff_code = self.last_poll.get("tariff_code") or ""
        region = tariff_code.rsplit("-", 1)[-1] if "-" in tariff_code else None
        if not region or len(region) != 1:
            return {"status": "unavailable", "reason": "region unknown until the Octopus tariff code is seen"}
        from ..pricing.octopus_api import expand_to_half_hours
        start = now - timedelta(days=days + 1)
        fetched = {}
        for t in load_templates(self.settings.data_dir):
            if t.code == "_error":
                continue
            product = await self.octopus_api.latest_product_code(t.code)
            if not product:
                continue
            try:
                rates = await self.octopus_api.unit_rates(product, region, start, now)
                code = f"E-1R-{product}-{region}"
                n = repo.upsert_rates(s, expand_to_half_hours(rates, start, now), now)
                sc = await self.octopus_api.standing_charge(product, region)
                fetched[t.code] = {"product": product, "tariff_code": code, "rows": n, "standing_p_per_day": sc}
            except Exception as exc:  # noqa: BLE001
                fetched[t.code] = {"error": str(exc)}
        self.last_poll["live_rates"] = fetched
        self._memo = {k: v for k, v in self._memo.items() if k[1:2] != ("tariffs",)}
        return {"status": "ok", "region": region, "fetched": fetched}

    def _compare_tariffs(self, s, days: int, now: datetime, ev_kwh_per_day: float = 0.0, battery_kwh_per_day: float = 0.0) -> dict[str, Any]:
        """Replay the last ``days`` of half-hourly import on each tariff template."""
        today = now.astimezone(LONDON).date()
        start, _ = local_day_bounds(today - timedelta(days=days))
        _, end = local_day_bounds(today - timedelta(days=1))

        def counter(sig: Signal):
            rows = repo.measurements_for(s, self.property_id, sig.value, start - timedelta(hours=1), end + timedelta(hours=1))
            return [Measurement(signal=sig, ts=as_utc(r.ts), value=r.value, quality=Quality(r.quality), provenance=Provenance(r.provenance), source_ref=r.source_ref) for r in rows]

        source = "whole_home_import"
        ints = [i for i in counter_to_intervals(counter(Signal.GRID_IMPORT_KWH), "grid_import") if start <= i.start < end]
        metered = repo.metered_intervals_for(s, self.property_id, start, end)
        if len(metered) >= 0.9 * max(1, int((end - start).total_seconds() // 1800)):
            from ..analytics.energy_accounting import EnergyInterval
            ints = [EnergyInterval(as_utc(r.start), as_utc(r.end), r.kwh, Quality.METERED, "grid_import") for r in metered]
            source = "smart_meter_half_hourly"
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
        if total_kwh > _import_limit_kwh(days):
            return {"status": "implausible", "reason": f"Data looks wrong: {total_kwh:,.0f} kWh of import in {days} days. See System health.", "days": days, "results": [], "kwh": round(total_kwh, 1)}
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
        live = self.last_poll.get("live_rates") or {}
        annual = self._annual_profile(s, now)
        for t in load_templates(self.settings.data_dir):
            if t.code == "_error":
                results.append({"code": t.code, "name": t.name, "replayable": False, "note": t.description})
                continue
            lr = live.get(t.code) or {}
            if lr.get("tariff_code"):
                book = build_rate_book([_ri(r) for r in repo.rates_for(s, lr["tariff_code"], start, end)], lr["tariff_code"])
                if book.intervals:
                    r = replay_with_book(book, t, triples, lr.get("standing_p_per_day"), ev_kwh_per_day, battery_kwh_per_day)
                    r.update({"description": t.description, "replayable": True, "requires": t.requires, "note": (t.note + " " if t.note else "") + f"Priced with published rates for product {lr['product']}.",
                              "rates_source": "octopus_api", "annualised_pounds": _annualise(r, annual)})
                    results.append(r)
                    continue
            if not t.replayable:
                results.append({"code": t.code, "name": t.name, "description": t.description, "replayable": False, "note": t.note, "requires": t.requires})
                continue
            r = replay(t, scenario_intervals(t, triples, ev_kwh_per_day, battery_kwh_per_day))
            r.update({"description": t.description, "replayable": True, "requires": t.requires, "note": t.note, "rates_source": "indicative",
                      "annualised_pounds": _annualise(r, annual)})
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
                "scenario": {"ev_kwh_per_day": ev_kwh_per_day, "battery_kwh_per_day": battery_kwh_per_day},
                "annual": annual, "live_rates": {k: v.get("product") for k, v in live.items() if v.get("product")},
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
        slots = self._price_slots(s, start, start + timedelta(hours=49))
        latest = repo.latest_measurements(s, self.property_id)

        def value(sig: Signal) -> float | None:
            r = latest.get(sig.value)
            return r.value if r and r.value is not None and (now - as_utc(r.ts)) < timedelta(hours=3) else None

        forecast = [(as_utc(w.valid_at), w.temperature_c) for w in self._latest_weather(s) if w.temperature_c is not None]
        est = self.thermal_estimate(s, now=now)
        hp = self.heat_pump_estimate(s, now=now)
        t_in0 = value(Signal.INDOOR_TEMPERATURE)
        # up to 48 hours ahead; beyond tomorrow's published prices the slots are "assumed", which classify_slots marks
        priced = [sp for sp in slots if not math.isnan(sp.rate_p_per_kwh) and sp.end > now][:96]
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
            plan["assumptions"] = [f"Simple rule-based plan for now, because the {why_not}. The learned house model takes over on its own once it is ready."] + [
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
        """Observed COP over the period (the headline), plus a COP-against-outdoor model
        per end use. Space heating and hot water are fitted separately."""
        start = now - timedelta(days=days)

        def counter(sig: Signal):
            rows = repo.measurements_for(s, self.property_id, sig.value, start - timedelta(hours=3), now)
            return [Measurement(signal=sig, ts=as_utc(r.ts), value=r.value, quality=Quality(r.quality), provenance=Provenance(r.provenance), source_ref=r.source_ref) for r in rows]

        t_h: dict[datetime, list[float]] = {}
        for r in repo.measurements_for(s, self.property_id, Signal.OUTDOOR_TEMPERATURE.value, start, now):
            if r.value is not None:
                t_h.setdefault(as_utc(r.ts).replace(minute=0, second=0, microsecond=0), []).append(r.value)

        def hourly(ints) -> dict[datetime, float]:
            out: dict[datetime, float] = {}
            for i in ints:
                k = as_utc(i.start).replace(minute=0)
                out[k] = out.get(k, 0.0) + i.kwh
            return out

        def end_use(elec_sig: Signal, heat_sig: Signal, label: str) -> dict[str, Any]:
            elec = counter_to_intervals(counter(elec_sig), "e")
            heat = counter_to_intervals(counter(heat_sig), "h")
            e_h, h_h = hourly(elec), hourly(heat)
            hours = sorted(set(e_h) & set(h_h) & set(t_h))
            t_out = [sum(t_h[h]) / len(t_h[h]) for h in hours]
            e_tot = sum(e_h[h] for h in hours)
            h_tot = sum(h_h[h] for h in hours)
            model = fit_cop_model(t_out, [e_h[h] for h in hours], [h_h[h] for h in hours]) if hours else prior_cop_model()
            running = [h for h in hours if e_h[h] >= 0.3]
            days_seen = len({h.astimezone(LONDON).date() for h in running})
            near7 = any(4.0 <= t <= 10.0 for t, h in zip(t_out, hours) if e_h[h] >= 0.3)
            below2 = any(t < 2.0 for t, h in zip(t_out, hours) if e_h[h] >= 0.3)
            observed = round(h_tot / e_tot, 2) if e_tot >= 1.0 else None
            status = "ok" if len(running) >= 24 and observed else "waiting"
            out = {"label": label, "status": status, "observed_cop": observed, "kwh_electric": round(e_tot, 1), "kwh_heat": round(h_tot, 1),
                   "running_hours": len(running), "days_with_data": days_seen,
                   "outdoor_range_c": [round(min(t_out), 1), round(max(t_out), 1)] if t_out else None,
                   "cop_at_7": round(model.cop_at(7.0), 2) if status == "ok" and near7 else None,
                   "cop_at_minus_2": round(model.cop_at(-2.0), 2) if status == "ok" and below2 else None,
                   "slope_per_degc": round(model.b, 3), "model_confidence": model.confidence, "notes": model.notes,
                   "model": model.as_dict()}
            if status != "ok":
                out["reason"] = (f"PredHeat works out {label.lower()} efficiency from the electricity used and the heat delivered while the heat pump runs. "
                                 f"It needs about a day of running. So far: {len(running)} running {'hour' if len(running) == 1 else 'hours'} over {days_seen} {'day' if days_seen == 1 else 'days'}.")
            return out

        heating = end_use(Signal.HP_ELEC_HEATING_KWH, Signal.HP_HEAT_HEATING_KWH, "Space heating")
        dhw = end_use(Signal.HP_ELEC_DHW_KWH, Signal.HP_HEAT_DHW_KWH, "Hot water")
        heat_all = counter_to_intervals(counter(Signal.HP_HEAT_HEATING_KWH), "h")
        half_hour_kw = sorted(i.kwh / 0.5 for i in heat_all if i.kwh > 0)
        capacity = half_hour_kw[int(len(half_hour_kw) * 0.95)] if len(half_hour_kw) >= 20 else (self.settings.heat_pump_rated_kw or 7.0)
        capacity = float(max(3.0, min(16.0, capacity)))
        model = heating["model"]
        return {"status": heating["status"], "model_type": "cop_linear_t_out", "lookback_days": days,
                "heating": heating, "hot_water": dhw,
                "cop_at_7": heating["cop_at_7"], "cop_at_2": round(COPModel(model["a"], model["b"]).cop_at(2.0), 2) if heating["cop_at_7"] else None,
                "cop_at_minus_2": heating["cop_at_minus_2"], "slope_per_degc": heating["slope_per_degc"],
                "observed_mean_cop": heating["observed_cop"], "samples": heating["running_hours"], "days_with_data": heating["days_with_data"],
                "confidence": heating["model_confidence"], "rmse": model.get("rmse"), "t_range": heating["outdoor_range_c"] or [None, None],
                "capacity_kw": round(capacity, 1), "capacity_source": "observed 95th percentile" if len(half_hour_kw) >= 20 else ("rated output" if self.settings.heat_pump_rated_kw else "default 7 kW"),
                "rated_kw": self.settings.heat_pump_rated_kw, "reason": heating.get("reason"),
                "why_differs": "This is PredHeat's own calculation from the Vaillant sensors; the myVAILLANT app averages over a different period and may include hot water.",
                "window_start": start.isoformat(), "window_end": now.isoformat(), "notes": model.get("notes", []), "model": model}

    def _refresh_heat_pump_model(self, s, min_age: timedelta = timedelta(hours=6)) -> int | None:
        now = self.clock.now()
        latest = s.query(m.HeatPumpModelVersion).filter_by(property_id=self.property_id).order_by(m.HeatPumpModelVersion.created_at.desc()).first()
        if latest and now - as_utc(latest.created_at) < min_age:
            return latest.id
        est = self.heat_pump_estimate(s, now=now)
        if est["status"] != "ok":
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
            return {"status": "waiting", "reason": f"{len(runs)} plans recorded; the first will be scored once its horizon has passed.", "runs": len(runs), "points": 0}
        rmse_b = math.sqrt(sum(x * x for x in eb) / len(eb))
        backtest = self._cost_backtest(s, runs, now)
        return {"status": "ok", "runs": len(by_run), "points": len(eb), "cost_backtest": backtest,
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

            stale_after = max(timedelta(seconds=2 * self.settings.ha_poll_seconds), timedelta(minutes=15))

            mapped = {c.signal.value for c in (self.capability_report.capabilities if self.capability_report else []) if c.status != "unavailable"}

            def val(sig: Signal) -> dict | None:
                r = latest.get(sig.value)
                if not r or (self.capability_report and sig.value not in mapped):
                    return None
                seen = self.seen_at.get(sig.value)
                confirmed = max(as_utc(r.ts), seen) if seen else as_utc(r.ts)
                age = now - confirmed
                return {"value": r.value, "text": r.text, "ts": as_utc(r.ts).isoformat(), "confirmed_at": confirmed.isoformat(),
                        "age_minutes": round(age.total_seconds() / 60, 1), "quality": r.quality, "stale": age > stale_after,
                        "stale_after_minutes": int(stale_after.total_seconds() // 60)}

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
            heat_pump = dict(self.heat_pump_estimate(s, now=now))
            heat_pump.pop("model", None)
            heat_pump["heating"] = {k: v for k, v in heat_pump["heating"].items() if k != "model"}
            heat_pump["hot_water"] = {k: v for k, v in heat_pump["hot_water"].items() if k != "model"}
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
        metered = repo.metered_intervals_for(s, self.property_id, start, end)
        slots_in_period = max(1, int((end - start).total_seconds() // 1800))
        import_source = "counter"
        if len(metered) >= 0.9 * slots_in_period or (metered and not grid_imp):
            from ..analytics.energy_accounting import EnergyInterval
            grid_imp = [EnergyInterval(as_utc(r.start), as_utc(r.end), r.kwh, Quality.METERED, "grid_import") for r in metered]
            import_source = "smart_meter_half_hourly"
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
            "whole_home_import": {"kwh": gk, "cost_pence": gc, "quality": q_of(grid_imp), "prices_complete": gok, "source": import_source,
                                  "plausible": gk <= _import_limit_kwh((end_day - start_day).days),
                                  "limit_kwh": _import_limit_kwh((end_day - start_day).days)},
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

    def grouped_series(self, start_day: date, end_day: date, group: str = "day") -> list[dict[str, Any]]:
        """Daily series rolled up by ISO week or calendar month. kWh and pence sum; COP is recomputed."""
        daily = self.daily_series(start_day, end_day)
        if group == "day":
            for d in daily:
                d["period_start"], d["period_end"], d["label"] = d["date"], d["date"], d["date"]
            return daily

        def key(d: str):
            dt = date.fromisoformat(d)
            if group == "week":
                iso = dt.isocalendar()
                monday = dt - timedelta(days=dt.weekday())
                return (monday.isoformat(), f"Week {iso[1]}, {monday.strftime('%d %b')}")
            return (dt.replace(day=1).isoformat(), dt.strftime("%B %Y"))

        groups: dict[str, dict[str, Any]] = {}
        for d in daily:
            k, label = key(d["date"])
            g = groups.setdefault(k, {"period_start": k, "period_end": d["date"], "label": label, "days": 0,
                                      "space_heating": {"kwh_electric": 0.0, "kwh_heat": 0.0, "cost_pence": 0.0, "quality": d["space_heating"]["quality"], "prices_complete": True},
                                      "hot_water": {"kwh_electric": 0.0, "kwh_heat": 0.0, "cost_pence": 0.0, "quality": d["hot_water"]["quality"], "prices_complete": True},
                                      "heat_pump_total": {"kwh_electric": 0.0, "cost_pence": 0.0},
                                      "whole_home_import": {"kwh": 0.0, "cost_pence": 0.0}, "standing_charge_pence": 0.0,
                                      "gas_equivalent": {"kwh_gas": 0.0, "cost_pence": 0.0, "saving_pence": 0.0}})
            g["period_end"] = d["date"]
            g["days"] += 1
            for cat in ("space_heating", "hot_water"):
                for f in ("kwh_electric", "kwh_heat", "cost_pence"):
                    g[cat][f] += d[cat].get(f) or 0.0
                g[cat]["prices_complete"] = g[cat]["prices_complete"] and d[cat].get("prices_complete", True)
            g["heat_pump_total"]["kwh_electric"] += d["heat_pump_total"]["kwh_electric"]
            g["heat_pump_total"]["cost_pence"] += d["heat_pump_total"]["cost_pence"]
            g["whole_home_import"]["kwh"] += d["whole_home_import"]["kwh"]
            g["whole_home_import"]["cost_pence"] += d["whole_home_import"]["cost_pence"]
            g["standing_charge_pence"] += d["standing_charge_pence"]
            for f in ("kwh_gas", "cost_pence", "saving_pence"):
                g["gas_equivalent"][f] += d["gas_equivalent"][f]
        out = []
        for g in groups.values():
            for cat in ("space_heating", "hot_water"):
                e, h = g[cat]["kwh_electric"], g[cat]["kwh_heat"]
                g[cat]["cop"] = round(h / e, 2) if e else None
                g[cat]["pence_per_kwh_heat"] = round(g[cat]["cost_pence"] / h, 2) if h else None
                for f in ("kwh_electric", "kwh_heat", "cost_pence"):
                    g[cat][f] = round(g[cat][f], 3)
            g["date"] = g["period_start"]
            out.append(g)
        return out

    def costs_csv(self, rows: list[dict[str, Any]]) -> str:
        import csv
        import io

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["period_start", "period_end", "heating_kwh_electric", "heating_kwh_heat", "heating_cop", "heating_cost_gbp",
                    "hot_water_kwh_electric", "hot_water_kwh_heat", "hot_water_cop", "hot_water_cost_gbp", "heat_pump_total_kwh", "heat_pump_total_cost_gbp",
                    "home_import_kwh", "home_import_cost_gbp", "standing_charge_gbp", "gas_equivalent_cost_gbp"])
        for d in rows:
            w.writerow([d.get("period_start", d["date"]), d.get("period_end", d["date"]),
                        d["space_heating"]["kwh_electric"], d["space_heating"].get("kwh_heat"), d["space_heating"].get("cop"), round(d["space_heating"]["cost_pence"] / 100, 2),
                        d["hot_water"]["kwh_electric"], d["hot_water"].get("kwh_heat"), d["hot_water"].get("cop"), round(d["hot_water"]["cost_pence"] / 100, 2),
                        d["heat_pump_total"]["kwh_electric"], round(d["heat_pump_total"]["cost_pence"] / 100, 2),
                        d["whole_home_import"]["kwh"], round(d["whole_home_import"]["cost_pence"] / 100, 2),
                        round(d["standing_charge_pence"] / 100, 2), round(d["gas_equivalent"]["cost_pence"] / 100, 2)])
        return buf.getvalue()

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
                r = latest.get(cap.signal.value) if cap.status != "unavailable" else None  # no leftovers from an earlier mapping
                age = (now - as_utc(r.ts)).total_seconds() / 60 if r else None
                notes = cap.notes
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


def _annualise(r: dict[str, Any], annual: dict[str, Any]) -> float:
    """Annual cost: the typical-year kWh at this tariff's effective rate when the profile is ready, else scaled from the period."""
    if annual.get("status") == "ok" and r.get("effective_p_per_kwh") and annual.get("annual_kwh"):
        return round((annual["annual_kwh"] * r["effective_p_per_kwh"] + r["standing_pence"] / max(r["days"], 1) * 365) / 100.0, 0)
    return round(r["total_pence"] / 100 / max(r["days"], 1) * 365, 0)


def _import_limit_kwh(days: int) -> float:
    """Domestic import sanity bound: 150 kWh a day, 1,000 kWh a week."""
    days = max(1, days)
    return 150.0 * days if days <= 6 else 1000.0 / 7.0 * days


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
        from ..control import Controller
        from ..integrations.homeassistant.control import RecordingControlAdapter

        p.controller = Controller(settings, RecordingControlAdapter(initial=cfg.setpoint_c), property_id=0)
        p.settings_store = store
        return p
    from ..integrations.homeassistant.client import HAClient
    from ..integrations.homeassistant.providers import HAOctopusTariffProvider, HASolcastProvider, HATelemetryProvider, HAWeatherProvider

    client = HAClient()
    p = Platform(settings, HATelemetryProvider(client), HAOctopusTariffProvider(client), HAWeatherProvider(client), HASolcastProvider(client), _RealClock())
    from ..pricing.octopus_api import OctopusAPI

    p.octopus_api = OctopusAPI()
    from ..control import Controller
    from ..integrations.homeassistant.control import HAControlAdapter

    # The controller always exists; whether it may act is decided by the gate on every cycle.
    p.controller = Controller(settings, HAControlAdapter(client), property_id=0)
    if settings.control_enabled and settings.control_climate_entity:
        log.warning("Control is allowed on %s (mode %s, max offset %.1f degC); it acts only in balanced or max_savings mode",
                    settings.control_climate_entity, settings.operating_mode.value, settings.control_max_offset_c)
    p.settings_store = store
    return p
