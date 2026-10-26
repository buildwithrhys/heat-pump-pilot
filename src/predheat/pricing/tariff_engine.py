"""Tariff pricing engine.

Principles (see DATA_MODEL.md and the spec, section 9):
- Price every kWh at the rate in force when it was consumed.
- Keep historic rates; never re-price the past from today's rate card.
- For Intelligent Octopus Go distinguish the guaranteed window, confirmed
  (completed/started) dispatches and merely planned dispatches.
- Pro-rate an energy interval that straddles a rate boundary.
- Standing charges are per local day, pro-rated by elapsed fraction of the day.
- Work on clock-change days (46 or 50 slots) because everything is UTC.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from ..domain import Dispatch, DispatchKind, RateInterval, StandingCharge
from ..timeutils import LONDON, half_hour_slots, overlap_seconds, to_utc


class PriceClass(StrEnum):
    GUARANTEED_OFF_PEAK = "guaranteed_off_peak"  # fixed 23:30-05:30 window (IOG/Go)
    CONFIRMED_DISPATCH = "confirmed_dispatch"  # completed or started smart-charge window
    PLANNED_DISPATCH = "planned_dispatch"  # planned only; may not materialise
    PEAK = "peak"
    VARIABLE = "variable"  # Agile-style half-hourly price
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SlotPrice:
    start: datetime
    end: datetime
    rate_p_per_kwh: float
    price_class: PriceClass
    assumed: bool = False  # True if the rate relies on an unconfirmed dispatch


@dataclass
class RateBook:
    tariff_code: str
    intervals: list[RateInterval]

    def __post_init__(self) -> None:
        self.intervals = sorted(self.intervals, key=lambda r: r.start)
        self._starts = [r.start for r in self.intervals]

    def rate_at(self, when: datetime) -> RateInterval | None:
        when = to_utc(when)
        i = bisect.bisect_right(self._starts, when) - 1
        if i < 0:
            return None
        r = self.intervals[i]
        return r if r.start <= when < r.end else None

    def coverage_gaps(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        gaps = []
        for s, e in half_hour_slots(start, end):
            if self.rate_at(s) is None:
                gaps.append((s, e))
        return gaps

    def cost_pence(self, start: datetime, end: datetime, kwh: float) -> tuple[float, float, bool]:
        """Cost of *kwh* consumed uniformly between start and end.

        Returns (cost_pence, effective_rate, complete). ``complete`` is False when
        part of the interval had no rate (that part is costed at 0 and flagged).
        """
        start, end = to_utc(start), to_utc(end)
        total_s = (end - start).total_seconds()
        if total_s <= 0 or kwh == 0:
            r = self.rate_at(start)
            return 0.0, (r.rate_p_per_kwh if r else 0.0), r is not None
        cost = 0.0
        covered = 0.0
        t = start
        while t < end:
            r = self.rate_at(t)
            if r is None:
                # jump to next interval start if any, else finish
                i = bisect.bisect_right(self._starts, t)
                nxt = self._starts[i] if i < len(self._starts) else end
                t = min(nxt, end)
                continue
            seg_end = min(r.end, end)
            frac = (seg_end - t).total_seconds() / total_s
            cost += kwh * frac * r.rate_p_per_kwh
            covered += (seg_end - t).total_seconds()
            t = seg_end
        complete = covered >= total_s - 1
        eff = cost / kwh if kwh else 0.0
        return round(cost, 6), round(eff, 6), complete


def build_rate_book(rates: list[RateInterval], tariff_code: str | None = None, is_export: bool = False) -> RateBook:
    """De-duplicate overlapping observations, preferring the latest observation of a slot."""
    chosen: dict[datetime, RateInterval] = {}
    for r in rates:
        if r.is_export != is_export:
            continue
        if tariff_code and r.tariff_code != tariff_code:
            continue
        prev = chosen.get(r.start)
        # Prefer intelligent-adjusted rows (they are the integration's latest view)
        if prev is None or (r.is_intelligent_adjusted and not prev.is_intelligent_adjusted):
            chosen[r.start] = r
    code = tariff_code or (next(iter(chosen.values())).tariff_code if chosen else "unknown")
    return RateBook(code, list(chosen.values()))


def _in_fixed_window(when: datetime, tz: ZoneInfo = LONDON, start_min: int = 23 * 60 + 30, end_min: int = 5 * 60 + 30) -> bool:
    lt = when.astimezone(tz)
    m = lt.hour * 60 + lt.minute
    return m >= start_min or m < end_min


def classify_slots(
    start: datetime,
    end: datetime,
    book: RateBook,
    dispatches: list[Dispatch] | None = None,
    family: str = "intelligent_go",
    off_peak_rate: float | None = None,
    peak_rate: float | None = None,
) -> list[SlotPrice]:
    """Classify each half-hour slot in [start, end) for an IOG-style tariff.

    Confirmed dispatches (started or completed) qualify for the off-peak rate.
    Planned dispatches are reported as planned and *assumed*: callers must not
    treat them as savings until they are confirmed.
    """
    dispatches = dispatches or []
    confirmed = [(d.start, d.end) for d in dispatches if d.kind in (DispatchKind.COMPLETED, DispatchKind.STARTED)]
    planned = [(d.start, d.end) for d in dispatches if d.kind == DispatchKind.PLANNED]
    rates = [r.rate_p_per_kwh for r in book.intervals]
    if rates:
        off_peak_rate = off_peak_rate if off_peak_rate is not None else min(rates)
        peak_rate = peak_rate if peak_rate is not None else max(rates)
    out: list[SlotPrice] = []
    for s, e in half_hour_slots(start, end):
        r = book.rate_at(s)
        if family in ("agile",):
            out.append(SlotPrice(s, e, r.rate_p_per_kwh if r else float("nan"), PriceClass.VARIABLE if r else PriceClass.UNKNOWN))
            continue
        if family in ("fixed", "flexible"):
            out.append(SlotPrice(s, e, r.rate_p_per_kwh if r else float("nan"), PriceClass.PEAK if r else PriceClass.UNKNOWN))
            continue
        if _in_fixed_window(s):
            out.append(SlotPrice(s, e, off_peak_rate if off_peak_rate is not None else (r.rate_p_per_kwh if r else float("nan")), PriceClass.GUARANTEED_OFF_PEAK))
        elif any(overlap_seconds(s, e, ds, de) > 0 for ds, de in confirmed):
            out.append(SlotPrice(s, e, off_peak_rate if off_peak_rate is not None else float("nan"), PriceClass.CONFIRMED_DISPATCH))
        elif any(overlap_seconds(s, e, ds, de) > 0 for ds, de in planned):
            out.append(SlotPrice(s, e, off_peak_rate if off_peak_rate is not None else float("nan"), PriceClass.PLANNED_DISPATCH, assumed=True))
        elif r is not None:
            cls = PriceClass.CONFIRMED_DISPATCH if r.is_intelligent_adjusted else PriceClass.PEAK
            out.append(SlotPrice(s, e, r.rate_p_per_kwh, cls))
        elif peak_rate is not None:
            # Beyond the published rate card: project the tariff's fixed structure and
            # mark the price as assumed so forecasts show it as such.
            out.append(SlotPrice(s, e, peak_rate, PriceClass.PEAK, assumed=True))
        else:
            out.append(SlotPrice(s, e, float("nan"), PriceClass.UNKNOWN))
    return out


def cost_energy_intervals(
    intervals: list[tuple[datetime, datetime, float]], book: RateBook
) -> list[dict]:
    """Cost a list of (start, end, kwh). Returns dicts ready to persist."""
    rows = []
    for s, e, kwh in intervals:
        cost, eff, complete = book.cost_pence(s, e, kwh)
        rows.append({"start": to_utc(s), "end": to_utc(e), "kwh": kwh, "rate_p_per_kwh": eff,
                     "cost_pence": cost, "complete": complete, "tariff_code": book.tariff_code})
    return rows


def standing_charge_for_period(charges: list[StandingCharge], start: datetime, end: datetime, tz: ZoneInfo = LONDON) -> float:
    """Pence of standing charge accrued between start and end, pro-rated per local day."""
    start, end = to_utc(start), to_utc(end)
    total = 0.0
    day = start.astimezone(tz).date()
    while True:
        d0 = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(start.tzinfo)
        d1 = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(start.tzinfo)
        if d0 >= end:
            break
        frac = overlap_seconds(d0, d1, start, end) / (d1 - d0).total_seconds()
        sc = _standing_charge_at(charges, d0)
        if sc is not None:
            total += sc.pence_per_day * frac
        day = day + timedelta(days=1)
    return round(total, 4)


def _standing_charge_at(charges: list[StandingCharge], when: datetime) -> StandingCharge | None:
    best = None
    for c in charges:
        if c.start <= when and (c.end is None or when < c.end):
            if best is None or c.start > best.start:
                best = c
    return best


def local_days(start: date, end: date) -> list[date]:
    out = []
    d = start
    while d < end:
        out.append(d)
        d += timedelta(days=1)
    return out
