"""Rule-based shadow planner.

This is the stand-in for the learned optimiser that arrives in Phase 4. It looks
at the next day of classified prices, the weather forecast and the latest
readings, and writes down what Heat Pump Pilot *would* do if it were in charge: when it
would reheat the hot water cylinder, when it would pre-heat the house ahead of
expensive electricity, and when it would let the house coast.

It never acts. In monitor mode the plan is only displayed. In shadow mode each
plan is also recorded so that, once the thermal model exists, the advice can be
scored against what actually happened.

The rules are deliberately simple and explained in every action's ``reason`` so
a reader can check them against the price chart.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from ..pricing import PriceClass, SlotPrice
from ..timeutils import LONDON

CHEAP_CLASSES = {PriceClass.GUARANTEED_OFF_PEAK, PriceClass.CONFIRMED_DISPATCH}
HEATING_OUTDOOR_LIMIT_C = 15.0  # above this the house is assumed not to need space heating
FLAT_TARIFF_SPREAD_P = 2.0  # less than this between cheapest and dearest slot: nothing to shift
MIN_SHIFT_BENEFIT_P = 2.0  # pre-heat only if it saves at least this per kWh
DHW_SLOTS = 2  # 1 hour at the end of the cheap window
PREHEAT_SLOTS = 3  # 90 minutes
PREHEAT_LOOKBACK = timedelta(hours=4)
MAX_COAST = timedelta(hours=3)
MIN_EXPENSIVE_WINDOW = timedelta(hours=1)


@dataclass
class PlanAction:
    kind: str  # heat_hot_water | preheat_space | coast_space | run_normally
    start: datetime
    end: datetime
    rate_p: float | None
    reason: str
    saving_p_per_kwh: float | None = None
    saving_basis: str | None = None  # vs_dear_period | vs_average

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        d["label"] = ACTION_LABEL[self.kind]
        return d


ACTION_LABEL = {
    "heat_hot_water": "Heat hot water",
    "preheat_space": "Pre-heat the house",
    "coast_space": "Let the house coast",
    "run_normally": "Run normally",
}


def _hhmm(dt: datetime) -> str:
    return dt.astimezone(LONDON).strftime("%H:%M")


def _runs(slots: list[SlotPrice], keep) -> list[list[SlotPrice]]:
    """Group consecutive slots for which ``keep(slot)`` is true."""
    out: list[list[SlotPrice]] = []
    cur: list[SlotPrice] = []
    for s in slots:
        if keep(s) and (not cur or cur[-1].end == s.start):
            cur.append(s)
        else:
            if cur:
                out.append(cur)
            cur = [s] if keep(s) else []
    if cur:
        out.append(cur)
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def build_shadow_plan(
    now: datetime,
    slots: list[SlotPrice],
    *,
    horizon_hours: int = 24,
    dhw_tank_c: float | None = None,
    dhw_setpoint_c: float | None = None,
    outdoor_forecast: list[tuple[datetime, float]] | None = None,
) -> dict[str, Any]:
    """Return the plan as a JSON-friendly dict. See module docstring for the rules."""
    horizon_end = now + timedelta(hours=horizon_hours)
    from ..pricing import planning_prices

    priced = [s for s in planning_prices(slots) if not math.isnan(s.rate_p_per_kwh) and s.end > now and s.start < horizon_end]
    assumptions = [
        "Rule-based plan: the learned house model is not in use yet.",
        "Setpoint changes of about 1 °C are assumed. This is what Heat Pump Pilot would do; it only acts if control is switched on.",
    ]
    base = {"horizon_start": now.isoformat(), "horizon_end": horizon_end.isoformat(), "assumptions": assumptions}
    if not priced:
        return {**base, "status": "not_available", "reason": "No tariff prices are available for the next 24 hours yet.",
                "actions": [], "next_action": {"kind": "none", "reason": "Waiting for tariff prices."}}

    rates = [s.rate_p_per_kwh for s in priced]
    lo, hi = min(rates), max(rates)
    spread = hi - lo
    mean_rate = _mean(rates)
    if spread < FLAT_TARIFF_SPREAD_P:
        act = PlanAction("run_normally", priced[0].start, priced[-1].end, mean_rate,
                         f"Prices are flat at about {mean_rate:.1f} p/kWh for the next {horizon_hours} hours, "
                         "so there is nothing to gain by shifting heating in time.")
        return {**base, "status": "indicative", "reason": "Flat prices: no time shifting is worthwhile.",
                "actions": [act.as_dict()], "next_action": _next(act, now)}

    cheap_cut = lo + 0.25 * spread
    dear_cut = hi - 0.25 * spread

    def is_cheap(s: SlotPrice) -> bool:
        # only prices that are certain: the fixed cheap hours, confirmed dispatches, or published low rates
        return s.price_class in CHEAP_CLASSES or (not s.assumed and s.rate_p_per_kwh <= cheap_cut)

    def is_dear(s: SlotPrice) -> bool:
        return not is_cheap(s) and s.rate_p_per_kwh >= dear_cut

    actions: list[PlanAction] = []
    reserved: set[datetime] = set()  # slot starts already used by an action

    # --- 1. Hot water at the end of the next cheap window
    cheap_windows = _runs(priced, is_cheap)
    if cheap_windows:
        w = cheap_windows[0]
        dhw = w[-DHW_SLOTS:]
        rate = _mean([s.rate_p_per_kwh for s in dhw])
        tank = ""
        if dhw_tank_c is not None:
            tank = f" The cylinder is at {dhw_tank_c:.0f} °C"
            tank += f" against a {dhw_setpoint_c:.0f} °C target." if dhw_setpoint_c is not None else "."
        actions.append(PlanAction(
            "heat_hot_water", dhw[0].start, dhw[-1].end, rate,
            f"Cheapest electricity of the day at {rate:.1f} p/kWh ({_hhmm(w[0].start)} to {_hhmm(w[-1].end)}). "
            "Reheat the cylinder at the end of the window so it is hot for the morning with the least standing loss."
            + tank,
            saving_p_per_kwh=round(mean_rate - rate, 2), saving_basis="vs_average",
        ))
        reserved.update(s.start for s in dhw)

    # --- 2. Pre-heat before each expensive window, then coast
    forecast = sorted(outdoor_forecast or [])

    def outdoor_during(start: datetime, end: datetime) -> float | None:
        temps = [t for (at, t) in forecast if start <= at < end]
        return _mean(temps) if temps else None

    by_start = {s.start: s for s in priced}
    for window in _runs(priced, is_dear):
        w_start, w_end = window[0].start, window[-1].end
        if w_end - w_start < MIN_EXPENSIVE_WINDOW or w_start <= now:
            continue
        w_rate = _mean([s.rate_p_per_kwh for s in window])
        outdoor = outdoor_during(w_start, min(w_end, w_start + MAX_COAST))
        if outdoor is not None and outdoor > HEATING_OUTDOOR_LIMIT_C:
            assumptions.append(f"No pre-heat before {_hhmm(w_start)}: forecast {outdoor:.0f} °C outside, warmer than the "
                               f"{HEATING_OUTDOOR_LIMIT_C:.0f} °C heating limit.")
            continue
        # cheapest run of PREHEAT_SLOTS consecutive unreserved slots in the lookback period
        t = max(now, w_start - PREHEAT_LOOKBACK)
        candidates: list[list[SlotPrice]] = []
        while t + timedelta(minutes=30 * PREHEAT_SLOTS) <= w_start:
            run = [by_start.get(t + timedelta(minutes=30 * i)) for i in range(PREHEAT_SLOTS)]
            if all(r is not None and r.start not in reserved for r in run):
                candidates.append(run)  # type: ignore[arg-type]
            t += timedelta(minutes=30)
        if not candidates:
            continue
        best = min(candidates, key=lambda run: _mean([s.rate_p_per_kwh for s in run]))
        p_rate = _mean([s.rate_p_per_kwh for s in best])
        if w_rate - p_rate < MIN_SHIFT_BENEFIT_P:
            continue
        coast_end = min(w_end, w_start + MAX_COAST)
        actions.append(PlanAction(
            "preheat_space", best[0].start, best[-1].end, p_rate,
            f"Raise the room setpoint by about 1 °C ahead of the {_hhmm(w_start)} to {_hhmm(w_end)} expensive period "
            f"({w_rate:.1f} p/kWh), using cheaper electricity at {p_rate:.1f} p/kWh."
            + (f" Forecast {outdoor:.0f} °C outside." if outdoor is not None else ""),
            saving_p_per_kwh=round(w_rate - p_rate, 2), saving_basis="vs_dear_period",
        ))
        actions.append(PlanAction(
            "coast_space", w_start, coast_end, w_rate,
            f"The dear rate applies from {_hhmm(w_start)} to {_hhmm(w_end)}. Lower the setpoint by about 1 °C and let the heat stored "
            f"by the earlier pre-heat carry the house until {_hhmm(coast_end)}, then resume normal heating.",
        ))
        reserved.update(s.start for s in best)

    actions.sort(key=lambda a: a.start)
    upcoming = next((a for a in actions if a.end > now), None)
    return {**base, "status": "indicative",
            "reason": f"Shadow plan from prices between {lo:.1f} and {hi:.1f} p/kWh over the next {horizon_hours} hours.",
            "actions": [a.as_dict() for a in actions],
            "next_action": _next(upcoming, now) if upcoming else {"kind": "none", "reason": "Nothing worth shifting in the next 24 hours."}}


def _next(a: PlanAction, now: datetime) -> dict[str, Any]:
    state = "now" if a.start <= now < a.end else "upcoming"
    return {"kind": a.kind, "label": ACTION_LABEL[a.kind], "start": a.start.isoformat(), "end": a.end.isoformat(),
            "rate_p": a.rate_p, "reason": a.reason, "state": state}
