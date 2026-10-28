"""Heating curve check: is the controller running hotter water than the house needs?

Method, all from the home's own data:
1. Radiator output: in hours when the heat pump runs on space heating, heat delivered equals
   what the radiators gave out. Radiator output follows the standard emitter law
   Q = K * (T_mean_water - T_room) ** 1.3, so K (how big the radiators effectively are) is
   fitted as the median of Q / (T_mean - T_room) ** 1.3. T_mean is (flow + return) / 2 when
   the return temperature is reported, else flow minus half an assumed 5 degC difference.
2. Heat needed at an outdoor temperature comes from the learned heat loss:
   Q = UA * (T_set - T_out) - internal gains.
3. Flow needed = T_set + (Q / K) ** (1 / 1.3) + half the flow-return difference, plus a 1 degC
   margin for recovery after setbacks and estimation error.
4. The controller's current flow target is read per 2 degC outdoor band. Vaillant curves fan out
   from the room setpoint, so the target's excess over the setpoint scales with the curve
   value; the suggested curve is the current value scaled by the heat-weighted median of
   (needed excess / current excess).
5. Cross-checks per band: room above setpoint with the compressor starting more than twice an
   hour means the curve is higher than needed there; room below setpoint with the heat pump
   running almost all the time means it is too low. A "too low" band overrides a suggestion
   to go lower.
6. Saving: the Carnot-based efficiency at the current and suggested flow temperatures, weighted
   by the heat delivered in each band.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any

N_EMITTER = 1.3
ASSUMED_DT = 5.0
MARGIN_C = 1.0
CURVE_STEP = 0.05


@dataclass
class HourRow:
    t_out: float | None
    t_in: float | None
    t_set: float | None
    flow: float | None
    flow_set: float | None
    ret: float | None
    heat_kwh: float
    dhw_kwh: float
    elec_kwh: float
    cycles: float | None = None
    op_hours: float | None = None


def _cop(flow_c: float, t_out: float) -> float:
    tc = flow_c + 3.0 + 273.15
    te = t_out - 5.0 + 273.15
    return tc / max(5.0, tc - te)


def _band(t: float) -> int:
    return int(math.floor(t / 2.0) * 2)


def _wmedian(values: list[float], weights: list[float]) -> float:
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]


def analyse_curve(hours: list[HourRow], ua_w_per_k: float, internal_gain_kw: float | None, current_curve: float | None,
                  min_flow_c: float | None = None, cutoff_c: float | None = None, margin_c: float = MARGIN_C) -> dict[str, Any]:
    gain = max(0.0, internal_gain_kw or 0.0)
    ua_kw = ua_w_per_k / 1000.0
    usable = [h for h in hours if h.t_out is not None and h.t_in is not None and h.dhw_kwh < 0.1]
    running = [h for h in usable if h.heat_kwh >= 1.0 and h.flow is not None]
    if len(running) < 12:
        return {"status": "waiting", "running_hours": len(running),
                "reason": "Heat Pump Pilot checks the curve from hours when the heat pump is heating the house steadily. "
                          f"It needs about 12 such hours; so far it has {len(running)}. Expect a result after a day or two of heating."}
    have_return = any(h.ret is not None for h in running)

    def tmean(h: HourRow) -> float:
        return (h.flow + h.ret) / 2.0 if h.ret is not None else h.flow - ASSUMED_DT / 2.0

    ratios = [h.heat_kwh / max(tmean(h) - h.t_in, 1.0) ** N_EMITTER for h in running]
    k = statistics.median(ratios)
    q1, q3 = (statistics.quantiles(ratios, n=4)[0], statistics.quantiles(ratios, n=4)[2]) if len(ratios) >= 4 else (k, k)
    spread = (q3 - q1) / k if k > 0 else 1.0
    dt_half = ASSUMED_DT / 2.0

    def needed(t_out: float, t_set: float) -> float | None:
        q = ua_kw * (t_set - t_out) - gain
        if q <= 0.05:
            return None
        return t_set + (q / k) ** (1.0 / N_EMITTER) + dt_half

    bands: dict[int, list[HourRow]] = {}
    for h in usable:
        bands.setdefault(_band(h.t_out), []).append(h)
    rows: list[dict[str, Any]] = []
    for b in sorted(bands):
        hs = bands[b]
        run = [h for h in hs if h.heat_kwh >= 1.0 and h.flow is not None]
        if len(run) < 2:
            continue
        sets = [h.flow_set for h in run if h.flow_set is not None]
        flow_now = statistics.median(sets) if len(sets) >= 2 else statistics.median([h.flow for h in run])
        t_set = statistics.median([h.t_set for h in hs if h.t_set is not None] or [21.0])
        t_mid = statistics.median([h.t_out for h in run])
        need = needed(t_mid, t_set)
        if need is None:
            continue
        need_m = need + margin_c
        offset = statistics.median([h.t_in - (h.t_set if h.t_set is not None else t_set) for h in hs])
        op = [h.op_hours for h in run if h.op_hours is not None]
        cyc = [h.cycles for h in run if h.cycles is not None]
        cycles_per_h = (sum(cyc) / sum(op)) if op and sum(op) >= 1.0 and cyc else None
        all_op = [h.op_hours for h in hs if h.op_hours is not None]
        run_frac = (sum(all_op) / len(all_op)) if all_op else len(run) / len(hs)
        flag = None
        if offset > 0.5 and cycles_per_h is not None and cycles_per_h > 2.0:
            flag = "hot"
        elif offset < -0.5 and run_frac > 0.85:
            flag = "cold"
        rows.append({"band": f"{b} to {b + 2} °C", "t_out": round(t_mid, 1), "t_set": round(t_set, 1), "hours": len(hs), "running_hours": len(run),
                     "heat_kwh": round(sum(h.heat_kwh for h in run), 2), "flow_now": round(flow_now, 1), "flow_needed": round(need_m, 1),
                     "room_offset": round(offset, 2), "cycles_per_running_hour": round(cycles_per_h, 1) if cycles_per_h is not None else None,
                     "run_fraction": round(run_frac, 2), "flag": flag})
    if not rows:
        return {"status": "waiting", "running_hours": len(running),
                "reason": "There is heating data, but not yet enough of it in any single band of outdoor temperature. Expect a result after a few more days of heating."}

    t_set_all = statistics.median([r["t_set"] for r in rows])
    # current curve as a straight line through the room setpoint: excess over setpoint = slope * (setpoint - outdoor)
    xs = [r["t_set"] - r["t_out"] for r in rows]
    ys = [r["flow_now"] - r["t_set"] for r in rows]
    slope_now = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs) if sum(x * x for x in xs) > 0 else None
    weights = [max(r["heat_kwh"], 0.1) for r in rows]
    scale_ratios = [(r["flow_needed"] - r["t_set"]) / (r["flow_now"] - r["t_set"]) for r in rows if r["flow_now"] - r["t_set"] > 2.0]
    ratio = _wmedian(scale_ratios, [w for w, r in zip(weights, rows) if r["flow_now"] - r["t_set"] > 2.0]) if scale_ratios else 1.0
    hot = [r for r in rows if r["flag"] == "hot"]
    cold = [r for r in rows if r["flag"] == "cold"]
    suggested = None
    if current_curve:
        suggested = min(4.0, max(0.1, round(current_curve * ratio / CURVE_STEP) * CURVE_STEP))
        if cold and suggested < current_curve:
            suggested = current_curve
        if cold and not hot and suggested <= current_curve:
            suggested = round((current_curve + 0.1) / CURVE_STEP) * CURVE_STEP
        suggested = round(suggested, 2)
        delta = suggested - current_curve
        direction = "lower" if delta <= -CURVE_STEP + 1e-9 else "raise" if delta >= CURVE_STEP - 1e-9 else "keep"
        new_scale = suggested / current_curve
    else:
        mean_gap = sum((r["flow_needed"] - r["flow_now"]) * w for r, w in zip(rows, weights)) / sum(weights)
        direction = "raise" if cold else "lower" if mean_gap <= -2.0 else "raise" if mean_gap >= 2.0 else "keep"
        new_scale = ratio if direction != "keep" else 1.0
    # saving per band at the suggested curve
    tot_heat, tot_save = 0.0, 0.0
    for r in rows:
        f_new = r["t_set"] + (r["flow_now"] - r["t_set"]) * new_scale
        r["flow_suggested"] = round(f_new, 1)
        sav = 1.0 - _cop(r["flow_now"], r["t_out"]) / _cop(f_new, r["t_out"])
        r["saving_pct"] = round(sav * 100.0, 1)
        tot_heat += r["heat_kwh"]
        tot_save += r["heat_kwh"] * sav
    saving_pct = round(tot_save / tot_heat * 100.0, 1) if tot_heat else 0.0
    notes: list[str] = []
    for r in hot:
        notes.append(f"At {r['band']} outside the room sat {r['room_offset']:+.1f} °C from your setpoint and the compressor started "
                     f"{r['cycles_per_running_hour']:.0f} times an hour: the water is hotter than the house needs there.")
    for r in cold:
        notes.append(f"At {r['band']} outside the room sat {abs(r['room_offset']):.1f} °C below your setpoint with the heat pump running "
                     f"{r['run_fraction'] * 100:.0f}% of the time: the water is not hot enough there.")
    # outdoor cut-off and minimum flow
    settings: list[dict[str, Any]] = []
    if current_curve is not None or suggested is not None:
        settings.append({"key": "heating_curve", "label": "Heating curve", "now": current_curve, "suggested": suggested if suggested != current_curve else None,
                         "why": {"lower": f"about {saving_pct:.0f}% cheaper space heating in weather like the last fortnight",
                                 "raise": "keeps the house at your setpoint in the colder bands",
                                 "keep": "already close to what the house needs"}[direction]})
    t_cut = round(t_set_all - gain / ua_kw) if gain > 0 else None
    if cutoff_c is not None and t_cut is not None:
        t_cut = max(10, min(25, t_cut))
        settings.append({"key": "cutoff", "label": "Heat demand limit (outdoor cut-off)", "now": cutoff_c, "suggested": t_cut if cutoff_c - t_cut >= 1 else None,
                         "why": f"above about {t_cut} °C outside your house's own heat gains keep it warm, so heating can stop"
                                if cutoff_c - t_cut >= 1 else f"already at or below about {t_cut} °C, where your house's own heat gains take over"})
    if min_flow_c is not None:
        warm = needed(min(14.0, (t_cut or 16) - 1), t_set_all)
        sug = max(20, round(warm + margin_c)) if warm is not None else None
        settings.append({"key": "min_flow", "label": "Minimum flow temperature", "now": min_flow_c,
                         "suggested": sug if sug is not None and min_flow_c > sug + 3 else None,
                         "why": f"on mild days the house needs only about {round(warm + margin_c) if warm else '?'} °C water" if warm else "mild-day need not known yet"})
    t_seen = [h.t_out for h in running]
    span = max(t_seen) - min(t_seen)
    confidence = round(max(0.0, min(1.0, min(1.0, len(running) / 48.0) * (1.0 - min(spread, 1.0) * 0.5) * min(1.0, span / 8.0 + 0.25))), 2)
    x0 = int(min(-5, math.floor(min(t_seen))))
    chart = []
    for t in range(x0, 19):
        row: dict[str, Any] = {"t": t}
        if slope_now is not None:
            row["now"] = round(t_set_all + slope_now * (t_set_all - t), 1)
            row["suggested"] = round(t_set_all + slope_now * new_scale * (t_set_all - t), 1)
        n = needed(t, t_set_all)
        if n is not None:
            row["needed"] = round(n + margin_c, 1)
        chart.append(row)
    return {"status": "ok", "direction": direction, "current_curve": current_curve, "suggested_curve": suggested, "saving_pct": saving_pct,
            "confidence": confidence, "notes": notes, "bands": rows, "settings": settings, "chart": chart,
            "emitter": {"k_kw_per_k13": round(k, 4), "spread": round(spread, 2), "hours": len(running), "return_reported": have_return,
                        "assumed_flow_return_difference_c": None if have_return else ASSUMED_DT, "margin_c": margin_c,
                        "output_at_50c_flow_kw": round(k * max(50.0 - dt_half - t_set_all, 1.0) ** N_EMITTER, 1)},
            "coverage": {"t_min": round(min(t_seen), 1), "t_max": round(max(t_seen), 1), "running_hours": len(running), "cold_end_checked": min(t_seen) < 3.0}}
