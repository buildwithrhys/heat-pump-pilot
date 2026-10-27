"""Tariff comparison by replaying your own half-hourly consumption.

Templates describe each tariff's unit rates by local time window and its
standing charge. Rates are published prices for one region at the date noted
and are *indicative*: Octopus prices vary by region and change a few times a
year. Put a ``tariffs.yaml`` in the data directory to override or add tariffs.

Agile cannot be replayed without its half-hourly history, so it is listed as
needing the Octopus API (a later release).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..timeutils import LONDON

RATES_AS_OF = "published October 2025, indicative national averages"

# Octopus tariff code suffix -> grid supply area
REGION_NAMES = {"A": "Eastern England", "B": "East Midlands", "C": "London", "D": "Merseyside and North Wales", "E": "West Midlands",
                "F": "North East England", "G": "North West England", "H": "Southern England", "J": "South East England",
                "K": "South Wales", "L": "South West England", "M": "Yorkshire", "N": "South Scotland", "P": "North Scotland"}


@dataclass
class Window:
    start: str  # "HH:MM" local
    end: str
    rate_p: float

    def covers(self, local_hhmm: int) -> bool:
        s = int(self.start[:2]) * 60 + int(self.start[3:])
        e = int(self.end[:2]) * 60 + int(self.end[3:])
        if s <= e:
            return s <= local_hhmm < e
        return local_hhmm >= s or local_hhmm < e  # crosses midnight


@dataclass
class TariffTemplate:
    code: str
    name: str
    description: str
    default_rate_p: float
    standing_p_per_day: float
    windows: list[Window] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)  # e.g. ["ev", "smart_meter"]
    replayable: bool = True
    note: str = ""

    def rate_at(self, when: datetime) -> float:
        lt = when.astimezone(LONDON)
        hhmm = lt.hour * 60 + lt.minute
        for w in self.windows:
            if w.covers(hhmm):
                return w.rate_p
        return self.default_rate_p


DEFAULT_TEMPLATES: list[TariffTemplate] = [
    TariffTemplate("INTELLI-VAR", "Intelligent Octopus Go", "Six cheap hours overnight plus extra cheap slots when your EV is scheduled to charge.",
                   27.7, 53.0, [Window("23:30", "05:30", 7.0)], requires=["ev", "smart_meter"],
                   note="Dispatch slots outside 23:30 to 05:30 are not modelled, so this slightly overstates the cost for EV owners."),
    TariffTemplate("GO-VAR", "Octopus Go", "Five cheap hours overnight for EV charging.", 28.6, 53.0, [Window("00:30", "05:30", 8.5)], requires=["ev", "smart_meter"]),
    TariffTemplate("COSY", "Cosy Octopus", "Three cheap windows a day for heat pumps, with a dear evening peak.", 25.2, 53.0,
                   [Window("04:00", "07:00", 12.6), Window("13:00", "16:00", 12.6), Window("22:00", "00:00", 12.6), Window("16:00", "19:00", 36.9)],
                   requires=["heat_pump", "smart_meter"], note="Designed for heat pumps that can pre-heat before the 16:00 to 19:00 peak."),
    TariffTemplate("FLEX", "Flexible Octopus", "Single flat rate, no smart meter needed.", 25.7, 53.0),
    TariffTemplate("AGILE", "Agile Octopus", "Half-hourly prices set the day before, following the wholesale market.", 0.0, 53.0,
                   replayable=False, note="Needs the half-hourly price history from the Octopus API, which a later release will fetch."),
]


def load_templates(data_dir: Path | None = None) -> list[TariffTemplate]:
    """Defaults, overlaid with ``<data_dir>/tariffs.yaml`` when present."""
    out = {t.code: t for t in DEFAULT_TEMPLATES}
    path = (data_dir or Path(".")) / "tariffs.yaml"
    if path.is_file():
        try:
            import yaml

            doc = yaml.safe_load(path.read_text()) or {}
            for item in doc.get("tariffs", []):
                t = TariffTemplate(code=item["code"], name=item.get("name", item["code"]), description=item.get("description", ""),
                                   default_rate_p=float(item.get("default_rate_p", 0)), standing_p_per_day=float(item.get("standing_p_per_day", 0)),
                                   windows=[Window(w["start"], w["end"], float(w["rate_p"])) for w in item.get("windows", [])],
                                   requires=list(item.get("requires", [])), replayable=bool(item.get("replayable", True)), note=item.get("note", ""))
                out[t.code] = t
        except Exception as exc:  # noqa: BLE001
            out["_error"] = TariffTemplate("_error", "tariffs.yaml not loaded", str(exc), 0, 0, replayable=False)
    return list(out.values())


def replay(template: TariffTemplate, intervals: list[tuple[datetime, datetime, float]]) -> dict[str, Any]:
    """Cost of ``intervals`` (start, end, kWh) on this template. Standing charge by calendar days covered."""
    unit = 0.0
    kwh = 0.0
    cheap_kwh = 0.0
    cheap_cut = min([w.rate_p for w in template.windows] + [template.default_rate_p])
    for start, _end, k in intervals:
        r = template.rate_at(start)
        unit += k * r
        kwh += k
        if r <= cheap_cut + 1e-9 and template.windows:
            cheap_kwh += k
    days = _whole_days(intervals)  # the standing charge is per day, never a fraction of one
    standing = template.standing_p_per_day * days
    return {"code": template.code, "name": template.name, "unit_pence": round(unit, 1), "standing_pence": round(standing, 1),
            "total_pence": round(unit + standing, 1), "kwh": round(kwh, 2), "days": round(days, 2),
            "cheap_share": round(cheap_kwh / kwh, 3) if kwh and template.windows else None,
            "effective_p_per_kwh": round(unit / kwh, 2) if kwh else None}


def scenario_intervals(template: TariffTemplate, intervals: list[tuple[datetime, datetime, float]], ev_kwh_per_day: float = 0.0,
                       battery_kwh_per_day: float = 0.0) -> list[tuple[datetime, datetime, float]]:
    """Adjust the consumption for what-ifs on this tariff.

    EV: ``ev_kwh_per_day`` added in each day's cheapest slots (where you would charge).
    Battery: ``battery_kwh_per_day`` moved from each day's dearest slots into its cheapest ones.
    """
    if ev_kwh_per_day <= 0 and battery_kwh_per_day <= 0:
        return intervals
    by_day: dict[Any, list[int]] = {}
    for i, (start, _end, _k) in enumerate(intervals):
        by_day.setdefault(start.astimezone(LONDON).date(), []).append(i)
    rows = [list(x) for x in intervals]
    per_slot_cap = 3.5  # kWh a 7 kW charger or inverter can move in half an hour
    for idxs in by_day.values():
        ranked = sorted(idxs, key=lambda i: (template.rate_at(rows[i][0]), rows[i][0]))
        left = ev_kwh_per_day
        for i in ranked:
            if left <= 0:
                break
            add = min(per_slot_cap, left)
            rows[i][2] += add
            left -= add
        shift = battery_kwh_per_day
        dear = sorted(idxs, key=lambda i: (-template.rate_at(rows[i][0]), rows[i][0]))
        taken = 0.0
        for i in dear:
            if taken >= shift:
                break
            t = min(rows[i][2], shift - taken)
            rows[i][2] -= t
            taken += t
        left = taken
        for i in ranked:
            if left <= 0:
                break
            add = min(per_slot_cap, left)
            rows[i][2] += add
            left -= add
    return [(a, b, k) for a, b, k in rows]


def replay_with_book(book, template: TariffTemplate, intervals: list[tuple[datetime, datetime, float]], standing_p_per_day: float | None,
                     ev_kwh_per_day: float = 0.0, battery_kwh_per_day: float = 0.0) -> dict[str, Any]:
    """Replay on published half-hourly rates (a RateBook) rather than the template's windows."""
    class _Shim:
        windows = template.windows
        default_rate_p = template.default_rate_p

        @staticmethod
        def rate_at(when):
            r = book.rate_at(when)
            return r.rate_p_per_kwh if r else template.rate_at(when)

    rows = scenario_intervals(_Shim(), intervals, ev_kwh_per_day, battery_kwh_per_day)  # type: ignore[arg-type]
    unit = kwh = cheap = 0.0
    rates_seen = []
    for start, end, k in rows:
        r = book.rate_at(start)
        rate = r.rate_p_per_kwh if r else template.rate_at(start)
        rates_seen.append(rate)
        unit += k * rate
        kwh += k
    if rates_seen:
        lo = min(rates_seen)
        cheap_cut = lo + 0.25 * (max(rates_seen) - lo)
        cheap = sum(k for (st, _e, k), rate in zip(rows, rates_seen) if rate <= cheap_cut)
    days = _whole_days(rows)
    standing = (standing_p_per_day if standing_p_per_day is not None else template.standing_p_per_day) * days
    return {"code": template.code, "name": template.name, "unit_pence": round(unit, 1), "standing_pence": round(standing, 1),
            "total_pence": round(unit + standing, 1), "kwh": round(kwh, 2), "days": round(days, 2),
            "cheap_share": round(cheap / kwh, 3) if kwh else None, "effective_p_per_kwh": round(unit / kwh, 2) if kwh else None}


def _whole_days(intervals) -> int:
    """Local days that have any usage in them: each pays one full day of standing charge."""
    return len({st.astimezone(LONDON).date() for st, _e, _k in intervals})


def price_actual(book, triples: list[tuple[datetime, datetime, float]]) -> dict[str, Any]:
    """What you actually paid, on the intervals that have a stored price.

    Intervals without a price are excluded from the cost *and* from the kWh, so the
    effective rate is never understated; ``coverage_pct`` says how much was priced.
    """
    priced, unit, kwh_priced, kwh_total = [], 0.0, 0.0, 0.0
    for st, en, k in triples:
        kwh_total += k
        c, _eff, complete = book.cost_pence(st, en, k)
        if complete:
            priced.append((st, en, k))
            unit += c
            kwh_priced += k
    days = len({st.astimezone(LONDON).date() for st, _e, _k in priced})
    return {"unit_pence": round(unit, 1), "kwh_priced": round(kwh_priced, 2), "kwh_total": round(kwh_total, 2),
            "coverage_pct": round(kwh_priced / kwh_total * 100.0, 1) if kwh_total else 0.0, "days_priced": days,
            "effective_p_per_kwh": round(unit / kwh_priced, 2) if kwh_priced else None, "priced_triples": priced}


def reconcile_template(template: TariffTemplate, actual: dict[str, Any]) -> dict[str, Any]:
    """Replay your current tariff's template on exactly the priced intervals and compare with
    what you paid. A large difference means the template's rates (or the dispatch slots it
    cannot model) do not match your bill, and the other replays should be read with that in mind."""
    r = replay(template, actual["priced_triples"])
    diff = (r["unit_pence"] - actual["unit_pence"]) / actual["unit_pence"] * 100.0 if actual["unit_pence"] else None
    return {"template_unit_pence": r["unit_pence"], "actual_unit_pence": actual["unit_pence"], "difference_pct": round(diff, 1) if diff is not None else None,
            "template_effective_p_per_kwh": r["effective_p_per_kwh"], "actual_effective_p_per_kwh": actual["effective_p_per_kwh"]}
