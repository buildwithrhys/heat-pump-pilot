"""Model-based optimiser: a dynamic programme over the next 24 hours.

Given the fitted building model (heat loss UA, capacity C, internal gains), the
heat pump COP model, half-hourly prices and the outdoor forecast, it chooses the
heat output in every half hour that minimises electricity cost while keeping the
indoor temperature inside the comfort band, and leaves the house no colder at
the end than the setpoint. It also compares the result with a plain thermostat
running the same model, so the saving it claims is against a like-for-like
baseline, not against reality.

State: indoor temperature on a 0.1 degC grid. Action: heat output as a fraction
of the heat pump's capacity. Everything is explained per slot. Hot water is
scheduled separately into the cheapest slots (per unit of delivered heat) before
the morning.

This is deliberately a small, transparent optimiser. It runs in milliseconds and
needs no solver.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

import numpy as np

from ..timeutils import LONDON
from .shadow import ACTION_LABEL, PlanAction

STEP_H = 0.5
FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)
T_GRID_STEP = 0.1
SHORTFALL_PENALTY_P_PER_DEG_H = 200.0  # being below the comfort band
OVERHEAT_PENALTY_P_PER_DEG_H = 20.0  # drifting above the band (waste)
TERMINAL_PENALTY_P_PER_DEG = 150.0  # ending the horizon below the setpoint
DHW_TANK_KWH_PER_K = 0.2  # about 170 litres
DHW_DAILY_DRAW_KWH = 3.0
DHW_COP_FACTOR = 0.85  # hot water needs a hotter flow than heating


@dataclass
class MPCInputs:
    starts: list[datetime]  # slot starts, half-hourly, ascending
    rate_p: list[float]
    t_out: list[float]
    ua_w_per_k: float
    capacity_kwh_per_k: float
    internal_gain_kw: float
    cop_fn: Callable[[float], float]
    hp_capacity_kw: float
    t_in0: float
    setpoint_c: float
    tolerance_c: float = 1.0
    dhw_tank_c: float | None = None
    dhw_setpoint_c: float | None = None
    solar_gain_kw: list[float] = field(default_factory=list)  # optional per-slot gains


def _hhmm(dt: datetime) -> str:
    return dt.astimezone(LONDON).strftime("%H:%M")


def step_temperature(t_in: float, t_out: float, q_kw: float, inp: MPCInputs, gain_kw: float = 0.0) -> float:
    ua_kw = inp.ua_w_per_k / 1000.0
    return t_in + STEP_H / inp.capacity_kwh_per_k * (q_kw + inp.internal_gain_kw + gain_kw - ua_kw * (t_in - t_out))


def thermostat_baseline(inp: MPCInputs) -> tuple[list[float], list[float], float]:
    """Plain on/off thermostat at the setpoint with 0.2 degC hysteresis on the same model."""
    t = inp.t_in0
    temps, heats, cost = [], [], 0.0
    heating = t < inp.setpoint_c
    for k, (rate, t_out) in enumerate(zip(inp.rate_p, inp.t_out)):
        gain = inp.solar_gain_kw[k] if k < len(inp.solar_gain_kw) else 0.0
        if t < inp.setpoint_c - 0.2:
            heating = True
        elif t > inp.setpoint_c + 0.2:
            heating = False
        q = 0.0
        if heating:
            # enough to reach setpoint + 0.2 this slot, capped at capacity
            ua_kw = inp.ua_w_per_k / 1000.0
            need = (inp.setpoint_c + 0.2 - t) * inp.capacity_kwh_per_k / STEP_H - inp.internal_gain_kw - gain + ua_kw * (t - t_out)
            q = float(np.clip(need, 0.0, inp.hp_capacity_kw))
        t = step_temperature(t, t_out, q, inp, gain)
        cost += q / inp.cop_fn(t_out) * STEP_H * rate
        temps.append(t)
        heats.append(q)
    return temps, heats, cost


def optimise(inp: MPCInputs) -> dict[str, Any]:
    n = len(inp.starts)
    if n == 0:
        return {"status": "not_available", "reason": "No priced slots to plan over."}
    lo = inp.setpoint_c - inp.tolerance_c
    hi = inp.setpoint_c + inp.tolerance_c
    grid = np.round(np.arange(lo - 2.0, hi + 2.0 + 1e-9, T_GRID_STEP), 1)
    m = len(grid)
    # map a temperature to a grid index
    def idx(t: float) -> int:
        return int(np.clip(round((t - grid[0]) / T_GRID_STEP), 0, m - 1))

    cost_to_go = np.zeros(m)
    # terminal penalty: ending below the setpoint
    cost_to_go = np.where(grid < inp.setpoint_c, (inp.setpoint_c - grid) * TERMINAL_PENALTY_P_PER_DEG, 0.0)
    policy = np.zeros((n, m), dtype=np.int8)
    ua_kw = inp.ua_w_per_k / 1000.0
    for k in range(n - 1, -1, -1):
        rate, t_out = inp.rate_p[k], inp.t_out[k]
        gain = inp.solar_gain_kw[k] if k < len(inp.solar_gain_kw) else 0.0
        cop = inp.cop_fn(t_out)
        new_ctg = np.full(m, np.inf)
        for a, frac in enumerate(FRACTIONS):
            q = frac * inp.hp_capacity_kw
            t_next = grid + STEP_H / inp.capacity_kwh_per_k * (q + inp.internal_gain_kw + gain - ua_kw * (grid - t_out))
            j = np.clip(np.round((t_next - grid[0]) / T_GRID_STEP).astype(int), 0, m - 1)
            elec_cost = q / cop * STEP_H * rate
            short = np.clip(lo - t_next, 0, None) * STEP_H * SHORTFALL_PENALTY_P_PER_DEG_H
            over = np.clip(t_next - hi, 0, None) * STEP_H * OVERHEAT_PENALTY_P_PER_DEG_H
            total = elec_cost + short + over + cost_to_go[j]
            better = total < new_ctg
            new_ctg = np.where(better, total, new_ctg)
            policy[k] = np.where(better, a, policy[k])
        cost_to_go = new_ctg

    # roll forward
    t = inp.t_in0
    slots, cost = [], 0.0
    for k in range(n):
        a = int(policy[k][idx(t)])
        q = FRACTIONS[a] * inp.hp_capacity_kw
        t_out, rate = inp.t_out[k], inp.rate_p[k]
        gain = inp.solar_gain_kw[k] if k < len(inp.solar_gain_kw) else 0.0
        t_next = step_temperature(t, t_out, q, inp, gain)
        elec = q / inp.cop_fn(t_out) * STEP_H
        c = elec * rate
        cost += c
        slots.append({"start": inp.starts[k], "end": inp.starts[k] + timedelta(minutes=30), "rate_p": rate, "t_out": t_out,
                      "heat_kw": round(q, 2), "elec_kwh": round(elec, 3), "cost_p": round(c, 2), "t_in": round(t_next, 2),
                      "cop": round(inp.cop_fn(t_out), 2)})
        t = t_next
    base_t, base_q, base_cost = thermostat_baseline(inp)
    for k, s in enumerate(slots):
        s["t_in_baseline"] = round(base_t[k], 2)
        s["heat_kw_baseline"] = round(base_q[k], 2)
    return {"status": "model_based", "slots": slots, "cost_p": round(cost, 1), "baseline_cost_p": round(base_cost, 1),
            "comfort_band": [lo, hi]}


def classify_slots(slots: list[dict[str, Any]], inp: MPCInputs) -> list[str]:
    """Label each slot: boost (heating harder than needed to hold), hold, coast (off and cooling), off (no heat needed)."""
    ua_kw = inp.ua_w_per_k / 1000.0
    modes = []
    prev = inp.t_in0
    for s in slots:
        hold_q = max(0.0, ua_kw * (prev - s["t_out"]) - inp.internal_gain_kw)  # heat to stay level
        q = s["heat_kw"]
        if q > hold_q * 1.25 + 0.3 and s["t_in"] > prev + 0.05:
            modes.append("boost")
        elif q == 0.0 and hold_q > 0.2:
            modes.append("coast")
        elif q == 0.0:
            modes.append("off")
        else:
            modes.append("hold")
        prev = s["t_in"]
    return modes


def schedule_hot_water(inp: MPCInputs, now: datetime) -> list[PlanAction]:
    """Cheapest slots per kWh of hot water heat before 07:00 local tomorrow (or within the horizon)."""
    if inp.dhw_setpoint_c is None:
        return []
    tank = inp.dhw_tank_c if inp.dhw_tank_c is not None else inp.dhw_setpoint_c - 10.0
    need_kwh = max(0.0, inp.dhw_setpoint_c - tank) * DHW_TANK_KWH_PER_K + DHW_DAILY_DRAW_KWH
    if need_kwh <= 0.3:
        return []
    local = now.astimezone(LONDON)
    deadline = (local + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0) if local.hour >= 7 else local.replace(hour=7, minute=0, second=0, microsecond=0)
    candidates = [(inp.rate_p[k] / (inp.cop_fn(inp.t_out[k]) * DHW_COP_FACTOR), k) for k, st in enumerate(inp.starts) if st < deadline]
    if not candidates:
        candidates = [(inp.rate_p[k] / (inp.cop_fn(inp.t_out[k]) * DHW_COP_FACTOR), k) for k in range(len(inp.starts))]
    candidates.sort()
    per_slot_kwh = inp.hp_capacity_kw * STEP_H
    chosen: list[int] = []
    got = 0.0
    for _, k in candidates:
        chosen.append(k)
        got += per_slot_kwh
        if got >= need_kwh:
            break
    chosen.sort()
    actions: list[PlanAction] = []
    run: list[int] = []
    def flush():
        if not run:
            return
        ks = run[:]
        rate = sum(inp.rate_p[k] for k in ks) / len(ks)
        cop = sum(inp.cop_fn(inp.t_out[k]) for k in ks) / len(ks) * DHW_COP_FACTOR
        actions.append(PlanAction("heat_hot_water", inp.starts[ks[0]], inp.starts[ks[-1]] + timedelta(minutes=30), round(rate, 2),
                                  f"Cheapest hot water of the day: {rate:.1f} p/kWh of electricity at a hot-water COP of about {cop:.1f}, "
                                  f"so roughly {rate / cop:.1f} p per kWh of heat. Needs about {need_kwh:.1f} kWh"
                                  + (f" (cylinder {tank:.0f} degC, target {inp.dhw_setpoint_c:.0f} degC)." if inp.dhw_tank_c is not None else "."),
                                  saving_p_per_kwh=None))
    for k in chosen:
        if run and k != run[-1] + 1:
            flush()
            run = []
        run.append(k)
    flush()
    return actions


def build_mpc_plan(inp: MPCInputs, now: datetime, model_notes: list[str] | None = None) -> dict[str, Any]:
    res = optimise(inp)
    horizon_end = inp.starts[-1] + timedelta(minutes=30) if inp.starts else now
    base = {"horizon_start": now.isoformat(), "horizon_end": horizon_end.isoformat()}
    if res["status"] != "model_based":
        return {**base, "status": "not_available", "reason": res["reason"], "actions": [],
                "next_action": {"kind": "none", "reason": res["reason"]}, "assumptions": []}
    slots = res["slots"]
    modes = classify_slots(slots, inp)
    actions: list[PlanAction] = []
    # contiguous runs of boost / coast become explained actions
    i = 0
    while i < len(slots):
        j = i
        while j + 1 < len(slots) and modes[j + 1] == modes[i]:
            j += 1
        mode = modes[i]
        run = slots[i:j + 1]
        if mode in ("boost", "coast"):
            rate = sum(s["rate_p"] for s in run) / len(run)
            t_out = sum(s["t_out"] for s in run) / len(run)
            # what follows (for the reason)
            nxt = slots[j + 1:j + 7]
            nxt_rate = (sum(s["rate_p"] for s in nxt) / len(nxt)) if nxt else None
            if mode == "boost":
                reason = (f"Warm the house from {run[0]['t_in'] if i == 0 else slots[i-1]['t_in']:.1f} to {run[-1]['t_in']:.1f} degC at {rate:.1f} p/kWh"
                          + (f" before prices rise to about {nxt_rate:.1f} p/kWh." if nxt_rate and nxt_rate > rate + 2 else ".")
                          + f" Outside {t_out:.0f} degC, COP about {run[0]['cop']:.1f}.")
                kind = "preheat_space"
            else:
                reason = (f"Heat pump off; the house drifts from {slots[i-1]['t_in'] if i else inp.t_in0:.1f} to {run[-1]['t_in']:.1f} degC on stored heat "
                          f"while electricity is {rate:.1f} p/kWh, staying inside the {res['comfort_band'][0]:.0f} to {res['comfort_band'][1]:.0f} degC band.")
                kind = "coast_space"
            actions.append(PlanAction(kind, run[0]["start"], run[-1]["end"], round(rate, 2), reason,
                                      saving_p_per_kwh=round(nxt_rate - rate, 2) if (mode == "boost" and nxt_rate) else None))
        i = j + 1
    actions += schedule_hot_water(inp, now)
    actions.sort(key=lambda a: a.start)
    upcoming = next((a for a in actions if a.end > now), None)
    cost, base_cost = res["cost_p"], res["baseline_cost_p"]
    tau_h = inp.capacity_kwh_per_k * 1000.0 / inp.ua_w_per_k if inp.ua_w_per_k else 0.0
    assumptions = [
        f"Based on your learned house (it loses about {inp.ua_w_per_k * 24 / 1000:.1f} kW at -3 °C outside and holds its heat for about {tau_h:.0f} hours) "
        f"and a heat pump efficiency of about {inp.cop_fn(7.0):.1f} at 7 °C outside.",
        f"Keeps the house between {res['comfort_band'][0]:.1f} and {res['comfort_band'][1]:.1f} °C around your {inp.setpoint_c:.1f} °C setpoint, and no colder than the setpoint at the end of the day.",
        "The comparison is the same house with an ordinary on/off thermostat, so the saving is like for like.",
    ] + list(model_notes or [])
    predicted = [{"start": s["start"].isoformat(), "t_in": s["t_in"], "t_in_baseline": s["t_in_baseline"], "heat_kw": s["heat_kw"],
                  "heat_kw_baseline": s["heat_kw_baseline"], "rate_p": s["rate_p"], "t_out": s["t_out"], "cost_p": s["cost_p"], "mode": modes[k]}
                 for k, s in enumerate(slots)]
    return {**base, "status": "model_based",
            "reason": f"Optimised space heating costs about {cost / 100:.2f} GBP over the next 24 hours against {base_cost / 100:.2f} GBP for a plain thermostat.",
            "actions": [a.as_dict() for a in actions],
            "next_action": _next(upcoming, now) if upcoming else {"kind": "none", "reason": "Hold the setpoint; nothing worth shifting."},
            "assumptions": assumptions, "predicted": predicted,
            "cost": {"optimised_p": cost, "baseline_p": base_cost, "saving_p": round(base_cost - cost, 1)}}


def _next(a: PlanAction, now: datetime) -> dict[str, Any]:
    state = "now" if a.start <= now < a.end else "upcoming"
    return {"kind": a.kind, "label": ACTION_LABEL[a.kind], "start": a.start.isoformat(), "end": a.end.isoformat(),
            "rate_p": a.rate_p, "reason": a.reason, "state": state}
