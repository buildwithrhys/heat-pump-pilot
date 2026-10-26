"""A synthetic house, heat pump, tariff and weather for development without hardware.

The physics is deliberately simple and *known*, so that later learning code
can be validated against ground truth:

- Building: single-zone 1R1C model. dT_in/dt = (Q_heat + Q_solar + Q_internal - UA (T_in - T_out)) / C
- Heat pump: weather-compensated flow setpoint from a Vaillant-style heating curve,
  COP from a Carnot fraction that falls with lift (T_flow - T_out), capacity limited,
  simple on/off hysteresis controller around the room setpoint, DHW priority.
- Tariff: Intelligent Octopus Go, 6.6p 23:30 to 05:30, 36.1p otherwise, plus random
  daytime dispatch windows when the "car" is plugged in.
- Weather: seasonal diurnal temperature with autocorrelated noise; cloud cover drives solar.

Everything produced is tagged ``Quality.SIMULATED`` so it can never be confused with
real data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from ...timeutils import LONDON


@dataclass
class SimConfig:
    seed: int = 42
    start: datetime = field(default_factory=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc))
    days: int = 14
    step_minutes: int = 5  # internal physics step
    # Ground truth building
    ua_w_per_k: float = 180.0  # heat loss coefficient (W/K)  ~ 5.4 kW at -2/21
    capacity_kwh_per_k: float = 6.0  # effective thermal capacity
    internal_gain_kw: float = 0.35
    solar_aperture_m2: float = 6.0  # effective glazing x transmittance
    # Heat pump
    rated_kw: float = 7.0
    heating_curve: float = 0.6
    min_flow_c: float = 25.0
    max_flow_c: float = 55.0
    carnot_fraction: float = 0.42
    standby_kw: float = 0.015
    # Comfort / schedule
    setpoint_c: float = 21.0
    setback_c: float = 19.0
    hysteresis_c: float = 0.4
    # DHW
    dhw_volume_l: float = 200.0
    dhw_setpoint_c: float = 48.0
    dhw_hysteresis_c: float = 7.0
    dhw_draw_l_per_day: float = 120.0
    # Tariff (pence)
    peak_rate: float = 36.0965
    offpeak_rate: float = 6.6
    standing_charge: float = 54.3619
    export_rate: float = 12.0
    # Household
    base_load_kw: float = 0.45
    pv_kwp: float = 10.0
    lat_deg: float = 52.0


@dataclass
class SimState:
    t_in: float
    t_tank: float
    hp_on: bool = False
    hp_mode: str = "STANDBY"  # HEATING / DHW / STANDBY
    elec_heating_kwh: float = 0.0
    elec_dhw_kwh: float = 0.0
    heat_heating_kwh: float = 0.0
    heat_dhw_kwh: float = 0.0
    env_heating_kwh: float = 0.0
    env_dhw_kwh: float = 0.0
    grid_import_kwh: float = 0.0
    grid_export_kwh: float = 0.0
    pv_kwh: float = 0.0
    cycles: int = 0
    run_hours: float = 0.0


class SimulatedHome:
    """Generates a dense record of the synthetic home. Deterministic for a seed."""

    def __init__(self, cfg: SimConfig | None = None):
        self.cfg = cfg or SimConfig()
        self.rng = np.random.default_rng(self.cfg.seed)
        self.records: list[dict] = []
        self.dispatch_windows: list[tuple[datetime, datetime, float]] = []
        self._run()

    # ------------------------------------------------------------------ weather
    def outdoor_temperature(self, t: datetime, noise: float) -> float:
        doy = t.timetuple().tm_yday
        hour = t.astimezone(LONDON).hour + t.minute / 60
        seasonal = 9.5 - 6.5 * math.cos(2 * math.pi * (doy - 20) / 365)
        diurnal = -3.0 * math.cos(2 * math.pi * (hour - 15) / 24)
        return seasonal + diurnal + noise

    def clear_sky_ghi(self, t: datetime) -> float:
        """Very rough clear-sky GHI in W/m2 from solar elevation."""
        doy = t.timetuple().tm_yday
        decl = math.radians(23.44) * math.sin(2 * math.pi * (284 + doy) / 365)
        lat = math.radians(self.cfg.lat_deg)
        solar_hour = (t.hour + t.minute / 60) - 12  # UTC ~ solar time for UK
        ha = math.radians(15 * solar_hour)
        elev = math.asin(math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(ha))
        if elev <= 0:
            return 0.0
        return 990 * math.sin(elev) * 0.75

    # ------------------------------------------------------------------ tariff
    def is_offpeak(self, t: datetime) -> bool:
        lt = t.astimezone(LONDON)
        mins = lt.hour * 60 + lt.minute
        if mins >= 23 * 60 + 30 or mins < 5 * 60 + 30:
            return True
        return any(s <= t < e for s, e, _ in self.dispatch_windows)

    def rate(self, t: datetime) -> float:
        return self.cfg.offpeak_rate if self.is_offpeak(t) else self.cfg.peak_rate

    def _plan_dispatches(self) -> None:
        """Random IOG daytime/evening dispatch windows on some days (car plugged in)."""
        day0 = self.cfg.start.astimezone(LONDON).date()
        for d in range(self.cfg.days + 2):
            day = day0 + timedelta(days=d)
            if self.rng.random() < 0.45:
                start_h = float(self.rng.choice([13.0, 14.0, 18.5, 20.0, 21.0]))
                dur_h = float(self.rng.choice([1.0, 1.5, 2.0, 3.0]))
                s = datetime(day.year, day.month, day.day, tzinfo=LONDON) + timedelta(hours=start_h)
                e = s + timedelta(hours=dur_h)
                self.dispatch_windows.append((s.astimezone(timezone.utc), e.astimezone(timezone.utc), 7.4 * dur_h))

    # ------------------------------------------------------------------ heat pump
    def flow_setpoint(self, t_out: float) -> float:
        """Vaillant-like heating curve: flow rises as it gets colder."""
        cfg = self.cfg
        base = 20.0 + cfg.heating_curve * (20.0 - t_out) * 1.6 + 8.0
        return float(min(cfg.max_flow_c, max(cfg.min_flow_c, base)))

    def cop(self, t_flow: float, t_out: float, dhw: bool = False) -> float:
        t_cond = t_flow + 3.0
        lift = max(5.0, t_cond - (t_out - 5.0))
        carnot = (t_cond + 273.15) / lift
        cop = self.cfg.carnot_fraction * carnot
        if t_out < 4.0:  # frosting / defrost penalty
            cop *= 0.9
        if dhw:
            cop *= 0.92
        return float(max(1.6, min(cop, 5.5)))

    def capacity_kw(self, t_out: float) -> float:
        return float(self.cfg.rated_kw * max(0.55, min(1.1, 0.85 + 0.03 * (t_out - 0))))

    def setpoint(self, t: datetime) -> float:
        lt = t.astimezone(LONDON)
        return self.cfg.setpoint_c if 6 <= lt.hour < 22 else self.cfg.setback_c

    # ------------------------------------------------------------------ main loop
    def _run(self) -> None:
        cfg = self.cfg
        self._plan_dispatches()
        dt_h = cfg.step_minutes / 60.0
        n = int(cfg.days * 24 * 60 / cfg.step_minutes)
        st = SimState(t_in=cfg.setpoint_c - 0.3, t_tank=cfg.dhw_setpoint_c - 2)
        noise = 0.0
        cloud = 0.5
        t = cfg.start
        c_wh_per_k = cfg.capacity_kwh_per_k * 1000.0
        tank_kwh_per_k = cfg.dhw_volume_l * 4.186 / 3600.0
        for _ in range(n):
            noise = 0.985 * noise + self.rng.normal(0, 0.35)
            cloud = min(1.0, max(0.0, 0.97 * cloud + self.rng.normal(0, 0.06)))
            t_out = self.outdoor_temperature(t, noise)
            ghi = self.clear_sky_ghi(t) * (1.0 - 0.75 * cloud)
            pv_kw = min(cfg.pv_kwp * 0.8, cfg.pv_kwp * ghi / 1000.0 * 0.85)
            q_solar_kw = cfg.solar_aperture_m2 * ghi / 1000.0 * 0.6

            # DHW draw: morning and evening peaks
            lt = t.astimezone(LONDON)
            draw_weight = 1.0 if lt.hour in (7, 8) else 0.8 if lt.hour in (19, 20, 21) else 0.1
            draw_l = cfg.dhw_draw_l_per_day * draw_weight / (24 * 60 / cfg.step_minutes) * 2.2
            st.t_tank -= draw_l / cfg.dhw_volume_l * (st.t_tank - 12.0)
            st.t_tank -= 0.08 * dt_h  # standing loss degC/h

            # Controller
            sp = self.setpoint(t)
            want_dhw = st.t_tank < cfg.dhw_setpoint_c - cfg.dhw_hysteresis_c or (st.hp_mode == "DHW" and st.t_tank < cfg.dhw_setpoint_c)
            if st.hp_mode == "HEATING":
                want_heat = st.t_in < sp + cfg.hysteresis_c
            else:
                want_heat = st.t_in < sp - cfg.hysteresis_c
            t_flow = self.flow_setpoint(t_out)
            mode = "DHW" if want_dhw else "HEATING" if want_heat and t_out < 17 else "STANDBY"
            if mode != "STANDBY" and st.hp_mode == "STANDBY":
                st.cycles += 1
            st.hp_mode = mode

            q_heat_kw = 0.0
            elec_kw = cfg.standby_kw
            if mode == "HEATING":
                cap = self.capacity_kw(t_out)
                demand = cfg.ua_w_per_k * (sp + 0.5 - t_out) / 1000.0 * 1.15
                q_heat_kw = max(0.0, min(cap, demand))
                c = self.cop(t_flow, t_out)
                elec_kw += q_heat_kw / c
                st.elec_heating_kwh += q_heat_kw / c * dt_h
                st.heat_heating_kwh += q_heat_kw * dt_h
                st.env_heating_kwh += q_heat_kw * (1 - 1 / c) * dt_h
                st.run_hours += dt_h
            elif mode == "DHW":
                cap = self.capacity_kw(t_out) * 0.9
                c = self.cop(cfg.dhw_setpoint_c + 5, t_out, dhw=True)
                elec_kw += cap / c
                st.t_tank += cap * dt_h / tank_kwh_per_k
                st.elec_dhw_kwh += cap / c * dt_h
                st.heat_dhw_kwh += cap * dt_h
                st.env_dhw_kwh += cap * (1 - 1 / c) * dt_h
                st.run_hours += dt_h

            # Building
            q_net_w = (q_heat_kw + q_solar_kw + cfg.internal_gain_kw) * 1000.0 - cfg.ua_w_per_k * (st.t_in - t_out)
            st.t_in += q_net_w / c_wh_per_k * dt_h

            # Household electricity and grid (no battery in the simulator for clarity)
            house_kw = cfg.base_load_kw + (0.9 if lt.hour in (7, 8, 18, 19, 20) else 0.0) * self.rng.uniform(0.6, 1.4)
            ev_kw = 7.4 if any(s <= t < e for s, e, _ in self.dispatch_windows) else 0.0
            load_kw = house_kw + elec_kw + ev_kw
            net_kw = load_kw - pv_kw
            if net_kw >= 0:
                st.grid_import_kwh += net_kw * dt_h
            else:
                st.grid_export_kwh += -net_kw * dt_h
            st.pv_kwh += pv_kw * dt_h

            self.records.append(
                {
                    "ts": t,
                    "t_out": round(t_out, 2),
                    "t_in": round(st.t_in, 3),
                    "t_tank": round(st.t_tank, 2),
                    "setpoint": sp,
                    "flow_setpoint": round(t_flow, 1) if mode == "HEATING" else None,
                    "flow_temp": round(t_flow - (0.0 if mode == "HEATING" else 8.0), 1),
                    "mode": mode,
                    "hp_elec_kw": round(elec_kw, 3),
                    "heat_kw": round(q_heat_kw, 3),
                    "ghi": round(ghi, 1),
                    "pv_kw": round(pv_kw, 3),
                    "ev_kw": ev_kw,
                    "house_kw": round(house_kw, 3),
                    "grid_kw": round(net_kw, 3),
                    "rate_p": self.rate(t),
                    "elec_heating_kwh": round(st.elec_heating_kwh, 4),
                    "elec_dhw_kwh": round(st.elec_dhw_kwh, 4),
                    "heat_heating_kwh": round(st.heat_heating_kwh, 4),
                    "heat_dhw_kwh": round(st.heat_dhw_kwh, 4),
                    "env_heating_kwh": round(st.env_heating_kwh, 4),
                    "env_dhw_kwh": round(st.env_dhw_kwh, 4),
                    "grid_import_kwh": round(st.grid_import_kwh, 4),
                    "grid_export_kwh": round(st.grid_export_kwh, 4),
                    "pv_kwh": round(st.pv_kwh, 4),
                    "cycles": st.cycles,
                    "run_hours": round(st.run_hours, 3),
                }
            )
            t += timedelta(minutes=cfg.step_minutes)

    # ------------------------------------------------------------------ helpers
    def at(self, when: datetime) -> dict:
        """Latest record at or before *when*."""
        idx = int((when - self.cfg.start).total_seconds() // (self.cfg.step_minutes * 60))
        idx = max(0, min(idx, len(self.records) - 1))
        return self.records[idx]

    def ground_truth(self) -> dict:
        return {
            "ua_w_per_k": self.cfg.ua_w_per_k,
            "capacity_kwh_per_k": self.cfg.capacity_kwh_per_k,
            "internal_gain_kw": self.cfg.internal_gain_kw,
            "solar_aperture_m2": self.cfg.solar_aperture_m2,
        }
