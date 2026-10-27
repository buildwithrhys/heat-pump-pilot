"""Read-only use of Predbat's published plan.

Predbat (https://github.com/springfall2008/batpred) plans the home battery and publishes the
plan as Home Assistant entities, each with a ``results`` attribute of 5-minute points keyed by
timestamp (repeated values are dropped, so a series is a step function):

- ``<prefix>.grid_power_best``: kW, positive importing, negative exporting
- ``<prefix>.battery_power_best``: kW, positive discharging, negative charging
- ``<prefix>.soc_kw_best``: kWh in the battery; attributes ``value_per_kwh_now`` (what Predbat
  values a stored kWh at, in pence) and ``soc_max``
- ``<prefix>.rates`` and ``<prefix>.rates_export``: p/kWh

PredHeat uses it to answer one question per half hour: if the heat pump used a little more
electricity now, where would it come from and what would it cost? Less solar export, the
battery, or the grid. Nothing here writes anything.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

STALE_AFTER = timedelta(minutes=30)
_SERIES = ("grid_power_best", "battery_power_best", "soc_kw_best", "rates", "rates_export")


def _ts(key: str) -> datetime | None:
    for parse in (lambda k: datetime.strptime(k, "%Y-%m-%dT%H:%M:%S%z"), datetime.fromisoformat):
        try:
            t = parse(key)
            return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


@dataclass
class Series:
    """A step function: the value at t is the value of the latest point at or before t."""

    times: list[datetime] = field(default_factory=list)
    values: list[float] = field(default_factory=list)

    @classmethod
    def parse(cls, results: Any) -> "Series":
        pts = []
        if isinstance(results, dict):
            for k, v in results.items():
                t = _ts(str(k))
                if t is None:
                    continue
                try:
                    pts.append((t, float(v)))
                except (TypeError, ValueError):
                    continue
        pts.sort()
        return cls([p[0] for p in pts], [p[1] for p in pts])

    def at(self, t: datetime) -> float | None:
        i = bisect.bisect_right(self.times, t) - 1
        return self.values[i] if i >= 0 else None

    def mean(self, start: datetime, end: datetime, step: timedelta = timedelta(minutes=5)) -> float | None:
        vals, t = [], start
        while t < end:
            v = self.at(t)
            if v is not None:
                vals.append(v)
            t += step
        return sum(vals) / len(vals) if vals else None

    @property
    def end(self) -> datetime | None:
        return self.times[-1] if self.times else None


@dataclass
class PredbatPlan:
    prefix: str
    updated_at: datetime | None
    grid_kw: Series
    battery_kw: Series
    soc_kwh: Series
    import_p: Series
    export_p: Series
    battery_value_p: float | None
    soc_max_kwh: float | None
    entities: dict[str, str]

    def fresh(self, now: datetime) -> bool:
        return self.updated_at is not None and now - self.updated_at <= STALE_AFTER and bool(self.grid_kw.times)

    @property
    def horizon_end(self) -> datetime | None:
        return self.grid_kw.end


def find_predbat(states: dict[str, Any]) -> dict[str, str] | None:
    """Entity ids of Predbat's plan, whatever prefix it was given. ``None`` when Predbat is not installed."""
    prefixes = sorted({eid.rsplit(".", 1)[0] for eid in states if eid.endswith(".soc_kw_best")}, key=lambda p: (p != "predbat", p))
    for p in prefixes:
        found = {k: f"{p}.{k}" for k in _SERIES if f"{p}.{k}" in states}
        if "grid_power_best" in found and "battery_power_best" in found:
            if f"{p}.status" in states:
                found["status"] = f"{p}.status"
            return found
    return None


def read_plan(states: dict[str, Any]) -> PredbatPlan | None:
    ids = find_predbat(states)
    if ids is None:
        return None

    def series(key: str) -> Series:
        st = states.get(ids.get(key, ""))
        return Series.parse((st.attributes or {}).get("results")) if st is not None else Series()

    soc = states.get(ids.get("soc_kw_best", ""))
    attrs = (soc.attributes or {}) if soc is not None else {}

    def num(v: Any) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    grid = states[ids["grid_power_best"]]
    updated = grid.last_updated or grid.last_changed
    return PredbatPlan(prefix=ids["grid_power_best"].rsplit(".", 1)[0], updated_at=updated, grid_kw=series("grid_power_best"),
                       battery_kw=series("battery_power_best"), soc_kwh=series("soc_kw_best"), import_p=series("rates"),
                       export_p=series("rates_export"), battery_value_p=num(attrs.get("value_per_kwh_now")), soc_max_kwh=num(attrs.get("soc_max")),
                       entities=ids)


def marginal_cost(plan: PredbatPlan, start: datetime, end: datetime, grid_rate_p: float, extra_kw: float) -> dict[str, Any] | None:
    """What ``extra_kw`` more electricity between start and end would cost, given Predbat's plan.

    Served first from solar that would otherwise be exported (losing the export rate), then from
    the battery if Predbat's plan leaves it room (at Predbat's own value of a stored kWh), and the
    rest from the grid at ``grid_rate_p``. Never more than the grid rate. ``None`` outside the plan.
    """
    if plan.horizon_end is None or start >= plan.horizon_end:
        return None
    grid = plan.grid_kw.mean(start, end)
    batt = plan.battery_kw.mean(start, end)
    if grid is None or batt is None:
        return None
    hours = (end - start).total_seconds() / 3600.0
    need = max(extra_kw, 0.1)
    from_export = min(need, max(0.0, -grid))
    rest = need - from_export
    from_battery = 0.0
    if rest > 0 and grid <= 0.1 and plan.battery_value_p is not None:
        # Room the battery has: up to the fastest discharge Predbat itself plans, and down to the
        # lowest level Predbat plans to reach (its reserve), never below it.
        max_dis = max([v for v in plan.battery_kw.values if v > 0] or [0.0])
        headroom = max(0.0, max_dis - batt)
        soc = plan.soc_kwh.at(start)
        floor = min(plan.soc_kwh.values) if plan.soc_kwh.values else None
        energy_kw = (soc - floor) / hours if soc is not None and floor is not None else 0.0
        from_battery = max(0.0, min(rest, headroom, energy_kw))
    from_grid = need - from_export - from_battery
    export_p = plan.export_p.at(start)
    if from_export > 0 and export_p is None:
        export_p = 0.0
    cost = (from_export * (export_p or 0.0) + from_battery * (plan.battery_value_p or 0.0) + from_grid * grid_rate_p) / need
    rate = min(cost, grid_rate_p)
    shares = {"solar": from_export / need, "battery": from_battery / need, "grid": from_grid / need}
    source = max(shares, key=lambda k: shares[k])
    return {"rate_p": round(rate, 2), "grid_rate_p": round(grid_rate_p, 2), "source": source,
            "shares": {k: round(v, 2) for k, v in shares.items()}, "predbat_grid_kw": round(grid, 2), "predbat_battery_kw": round(batt, 2)}


def apply_to_slots(plan: PredbatPlan, slots: list, extra_kw: float) -> tuple[list, list[dict[str, Any]]]:
    """Replace each slot's grid price with the marginal cost from Predbat's plan where the plan covers it."""
    out, detail = [], []
    for sp in slots:
        m = marginal_cost(plan, sp.start, sp.end, sp.rate_p_per_kwh, extra_kw)
        if m is None:
            out.append(sp)
            continue
        out.append(replace(sp, rate_p_per_kwh=m["rate_p"]))
        detail.append({"start": sp.start.isoformat(), "end": sp.end.isoformat(), **m})
    return out, detail
