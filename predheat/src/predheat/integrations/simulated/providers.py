"""Provider implementations backed by ``SimulatedHome``.

They mimic the *shape* of the live adapters, including hourly energy buckets
(as Vaillant publishes them) and IOG dispatch windows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ...domain import (
    CapabilityReport,
    DiscoveredCapability,
    Dispatch,
    DispatchKind,
    Measurement,
    Provenance,
    Quality,
    RateInterval,
    SIGNAL_CATALOGUE,
    Signal,
    SolarPoint,
    StandingCharge,
    TariffSnapshot,
    WeatherPoint,
)
from ...timeutils import LONDON, half_hour_slots
from ..base import ProviderHealth
from .home import SimulatedHome

_Q = Quality.SIMULATED
_P = Provenance.SIMULATOR

# signal -> record key, kind
_SIGNAL_MAP: dict[Signal, str] = {
    Signal.INDOOR_TEMPERATURE: "t_in",
    Signal.INDOOR_SETPOINT: "setpoint",
    Signal.OUTDOOR_TEMPERATURE: "t_out",
    Signal.FLOW_TEMPERATURE: "flow_temp",
    Signal.FLOW_TEMPERATURE_SETPOINT: "flow_setpoint",
    Signal.HP_ELECTRICAL_POWER: "hp_elec_kw",
    Signal.DHW_TANK_TEMPERATURE: "t_tank",
    Signal.HP_ELEC_HEATING_KWH: "elec_heating_kwh",
    Signal.HP_ELEC_DHW_KWH: "elec_dhw_kwh",
    Signal.HP_HEAT_HEATING_KWH: "heat_heating_kwh",
    Signal.HP_HEAT_DHW_KWH: "heat_dhw_kwh",
    Signal.HP_ENV_HEATING_KWH: "env_heating_kwh",
    Signal.HP_ENV_DHW_KWH: "env_dhw_kwh",
    Signal.GRID_IMPORT_KWH: "grid_import_kwh",
    Signal.GRID_EXPORT_KWH: "grid_export_kwh",
    Signal.PV_GENERATION_KWH: "pv_kwh",
    Signal.PV_POWER: "pv_kw",
    Signal.GRID_POWER: "grid_kw",
    Signal.EV_CHARGER_POWER: "ev_kw",
    Signal.HP_ON_OFF_CYCLES: "cycles",
    Signal.HP_OPERATION_HOURS: "run_hours",
    Signal.IMPORT_RATE_NOW: "rate_p",
}


class SimulatedTelemetryProvider:
    name = "simulated_telemetry"

    def __init__(self, home: SimulatedHome, clock: "SimClock"):
        self.home = home
        self.clock = clock

    async def discover(self) -> CapabilityReport:
        caps = []
        for sig in SIGNAL_CATALOGUE:
            if sig in _SIGNAL_MAP or sig == Signal.HP_OPERATING_STATE:
                caps.append(DiscoveredCapability(signal=sig, status="available", quality=_Q,
                                                 source_ref=f"sim:{_SIGNAL_MAP.get(sig, 'mode')}",
                                                 unit=SIGNAL_CATALOGUE[sig].unit, confidence=1.0,
                                                 notes="simulated"))
            else:
                caps.append(DiscoveredCapability(signal=sig, status="unavailable", quality=Quality.UNAVAILABLE,
                                                 confidence=0.0, notes="not simulated"))
        return CapabilityReport(generated_at=self.clock.now(), provenance=_P, capabilities=caps,
                                writable_controls=[{"entity_id": "sim:zone_setpoint", "domain": "climate", "min": 7, "max": 30, "step": 0.5}],
                                integrations=[{"platform": "simulator", "present": True, "entities": len(_SIGNAL_MAP), "live_entities": len(_SIGNAL_MAP)}],
                                warnings=["Simulated data: physics parameters are known and listed in ground_truth()."])

    def _measurements_at(self, when: datetime) -> list[Measurement]:
        rec = self.home.at(when)
        out = []
        for sig, key in _SIGNAL_MAP.items():
            v = rec.get(key)
            if v is None:
                continue
            out.append(Measurement(signal=sig, ts=rec["ts"], value=float(v), quality=_Q, provenance=_P, source_ref=f"sim:{key}"))
        out.append(Measurement(signal=Signal.HP_OPERATING_STATE, ts=rec["ts"], text=rec["mode"], quality=_Q, provenance=_P, source_ref="sim:mode"))
        return out

    async def read_measurements(self) -> list[Measurement]:
        return self._measurements_at(self.clock.now())

    async def read_history(self, start: datetime, end: datetime) -> list[Measurement]:
        """Mimic HA long-term statistics: hourly means for instants, end-of-hour state for counters."""
        out: list[Measurement] = []
        t = start.replace(minute=0, second=0, microsecond=0)
        while t < end:
            recs = [r for r in self.home.records if t <= r["ts"] < t + timedelta(hours=1)]
            if recs:
                last = recs[-1]
                for sig, key in _SIGNAL_MAP.items():
                    spec = SIGNAL_CATALOGUE[sig]
                    vals = [r[key] for r in recs if r.get(key) is not None]
                    if not vals:
                        continue
                    v = last[key] if spec.kind.value == "cumulative" else sum(vals) / len(vals)
                    out.append(Measurement(signal=sig, ts=t, value=float(v), quality=_Q, provenance=_P, source_ref=f"sim:{key}"))
            t += timedelta(hours=1)
        return out

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, self.clock.now(), None, {"records": len(self.home.records)})


class SimulatedTariffProvider:
    name = "simulated_intelligent_go"

    def __init__(self, home: SimulatedHome, clock: "SimClock", tariff_code: str = "E-1R-INTELLI-VAR-24-10-29-K"):
        self.home = home
        self.clock = clock
        self.tariff_code = tariff_code

    async def snapshot(self) -> TariffSnapshot:
        now = self.clock.now()
        day_start = now.astimezone(LONDON).replace(hour=0, minute=0, second=0, microsecond=0)
        start = (day_start - timedelta(days=1)).astimezone(timezone.utc)
        end = (day_start + timedelta(days=2)).astimezone(timezone.utc)
        rates = []
        for s, e in half_hour_slots(start, end):
            fixed = self.home.is_offpeak(s) and not any(ds <= s < de for ds, de, _ in self.home.dispatch_windows)
            dispatched = any(ds <= s < de for ds, de, _ in self.home.dispatch_windows)
            rates.append(RateInterval(start=s, end=e, rate_p_per_kwh=self.home.rate(s), tariff_code=self.tariff_code,
                                      is_intelligent_adjusted=dispatched and not fixed, provenance=_P))
        dispatches = []
        for ds, de, kwh in self.home.dispatch_windows:
            if de < now - timedelta(days=3) or ds > now + timedelta(days=1):
                continue
            kind = DispatchKind.COMPLETED if de <= now else DispatchKind.STARTED if ds <= now else DispatchKind.PLANNED
            dispatches.append(Dispatch(kind=kind, start=ds, end=de, charge_kwh=kwh, source="SMART", location="AT_HOME", observed_at=now))
        return TariffSnapshot(observed_at=now, import_tariff_code=self.tariff_code, export_tariff_code="E-1R-OUTGOING-VAR-24-10-26-K",
                              region="K", product="INTELLI-VAR-24-10-29", rates=rates,
                              standing_charges=[StandingCharge(start=self.home.cfg.start, end=None, pence_per_day=self.home.cfg.standing_charge, tariff_code=self.tariff_code)],
                              dispatches=dispatches)

    async def rate_history(self, start: datetime, end: datetime) -> list[RateInterval]:
        rates = []
        for s, e in half_hour_slots(start, end):
            dispatched = any(ds <= s < de for ds, de, _ in self.home.dispatch_windows)
            rates.append(RateInterval(start=s, end=e, rate_p_per_kwh=self.home.rate(s), tariff_code=self.tariff_code,
                                      is_intelligent_adjusted=dispatched, provenance=_P))
        return rates

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, self.clock.now())


class SimulatedWeatherProvider:
    name = "simulated_weather"

    def __init__(self, home: SimulatedHome, clock: "SimClock"):
        self.home = home
        self.clock = clock

    async def forecast(self, hours: int = 48) -> list[WeatherPoint]:
        """A 'forecast' = the simulator's future truth plus error growing with lead time."""
        now = self.clock.now()
        import numpy as np

        rng = np.random.default_rng(int(now.timestamp()) // 3600)
        out = []
        for h in range(hours):
            valid = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=h + 1)
            rec = self.home.at(valid)
            err = rng.normal(0, 0.3 + 0.05 * h)
            out.append(WeatherPoint(issued_at=now, valid_at=valid, temperature_c=round(rec["t_out"] + err, 2),
                                    wind_speed_ms=4.0, humidity_pct=80.0, cloud_cover_pct=round(100 * (1 - rec["ghi"] / max(1.0, self.home.clear_sky_ghi(valid))), 0) if rec["ghi"] > 0 else 60.0,
                                    condition="cloudy", provenance=_P))
        return out

    async def observation(self) -> WeatherPoint | None:
        now = self.clock.now()
        rec = self.home.at(now)
        return WeatherPoint(issued_at=now, valid_at=now, temperature_c=rec["t_out"], wind_speed_ms=4.0, humidity_pct=80.0,
                            cloud_cover_pct=None, condition=None, quality=_Q, provenance=_P)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, self.clock.now())


class SimulatedSolarProvider:
    name = "simulated_solar"

    def __init__(self, home: SimulatedHome, clock: "SimClock"):
        self.home = home
        self.clock = clock

    async def forecast(self, hours: int = 48) -> list[SolarPoint]:
        now = self.clock.now()
        out = []
        for s, _e in half_hour_slots(now, now + timedelta(hours=hours)):
            rec = self.home.at(s)
            pv = rec["pv_kw"]
            out.append(SolarPoint(issued_at=now, period_start=s, pv_estimate_kw=round(pv, 3), pv_estimate10_kw=round(pv * 0.6, 3),
                                  pv_estimate90_kw=round(pv * 1.25, 3), ghi_w_m2=rec["ghi"], provenance=_P))
        return out

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, self.clock.now())


class SimClock:
    """A controllable clock so simulated runs are reproducible."""

    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, when: datetime) -> None:
        self._now = when
