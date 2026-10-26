"""Energy accounting: cumulative counters -> interval energy -> categories -> reconciliation.

Rules
- Counters are monotonic. A decrease to near zero (under half the previous
  value) is a meter reset or a Vaillant day rollover and the delta is taken from
  zero, flagged as such. Any other decrease is a glitch (an out-of-order or
  stale reading) and contributes nothing, flagged as such. Never add a lifetime
  register reading as if it were an interval.
- Interval energy is attributed uniformly across the time between two readings,
  then re-bucketed into half-hour slots. Vaillant publishes hourly buckets so
  each hour is split 50/50 and labelled model_derived at half-hour resolution.
- Categories must reconcile to the heat pump total within a tolerance; any
  residual is reported as ``hp_unclassified`` rather than hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..domain import Measurement, Quality
from ..timeutils import SLOT, floor_to_slot, overlap_seconds


@dataclass
class EnergyInterval:
    start: datetime
    end: datetime
    kwh: float
    quality: Quality
    category: str
    notes: list[str] = field(default_factory=list)


def counter_to_intervals(
    readings: list[Measurement],
    category: str,
    slot: timedelta = SLOT,
    max_gap: timedelta = timedelta(hours=3),
    resolution_downgrade: bool = True,
) -> list[EnergyInterval]:
    """Convert cumulative counter readings into half-hour energy intervals."""
    pts = sorted({(m.ts, m.value) for m in readings if m.value is not None})
    if len(pts) < 2:
        return []
    quality_in = readings[0].quality
    buckets: dict[datetime, EnergyInterval] = {}

    def bucket(s: datetime) -> EnergyInterval:
        b = buckets.get(s)
        if b is None:
            b = EnergyInterval(s, s + slot, 0.0, quality_in, category)
            buckets[s] = b
        return b

    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t1 <= t0:
            continue
        gap = t1 - t0
        delta = v1 - v0
        notes = []
        if delta < 0:
            if v1 < 0.5 * v0:
                delta = v1  # reset: the counter restarted at (or near) zero
                notes.append("counter_reset")
            else:
                delta = 0.0  # non-monotonic glitch: ignore rather than invent energy
                notes.append("non_monotonic_ignored")
        if gap > max_gap:
            notes.append(f"gap_{int(gap.total_seconds() // 60)}min")
        # Spread uniformly across [t0, t1)
        s = floor_to_slot(t0)
        while s < t1:
            e = s + slot
            frac = overlap_seconds(s, e, t0, t1) / gap.total_seconds()
            if frac > 0:
                b = bucket(s)
                b.kwh += delta * frac
                if resolution_downgrade and gap > slot + timedelta(minutes=1):
                    b.quality = Quality.MODEL_DERIVED if quality_in != Quality.SIMULATED else quality_in
                    if "resampled_from_coarser_readings" not in b.notes:
                        b.notes.append("resampled_from_coarser_readings")
                for n in notes:
                    if n not in b.notes:
                        b.notes.append(n)
            s = e
    out = sorted(buckets.values(), key=lambda b: b.start)
    for b in out:
        b.kwh = round(b.kwh, 6)
    return out


def allocate_heat_pump_categories(
    hp_elec_heating: list[EnergyInterval],
    hp_elec_dhw: list[EnergyInterval],
    aux_heating: list[EnergyInterval] | None = None,
    aux_dhw: list[EnergyInterval] | None = None,
    hp_total: list[EnergyInterval] | None = None,
    standby_kw: float = 0.015,
) -> dict[datetime, dict[str, EnergyInterval]]:
    """Build the per-slot category table.

    If an independent ``hp_total`` (e.g. a CT clamp) is available, the residual
    after heating/DHW/aux is split into standby (up to standby_kw) and unclassified.
    Without it, standby is *estimated* from the standby_kw assumption and labelled so.
    """
    by_slot: dict[datetime, dict[str, EnergyInterval]] = {}

    def put(ints: list[EnergyInterval] | None, name: str) -> None:
        for i in ints or []:
            by_slot.setdefault(i.start, {})[name] = EnergyInterval(i.start, i.end, i.kwh, i.quality, name, list(i.notes))

    put(hp_elec_heating, "hp_elec_heating")
    put(hp_elec_dhw, "hp_elec_dhw")
    put(aux_heating, "aux_elec_heating")
    put(aux_dhw, "aux_elec_dhw")
    put(hp_total, "hp_total_metered")

    for start, cats in by_slot.items():
        end = start + SLOT
        known = sum(c.kwh for n, c in cats.items() if n != "hp_total_metered")
        slot_h = (end - start).total_seconds() / 3600
        if "hp_total_metered" in cats:
            residual = cats["hp_total_metered"].kwh - known
            standby = max(0.0, min(residual, standby_kw * slot_h))
            unclassified = residual - standby
            cats["hp_standby"] = EnergyInterval(start, end, round(standby, 6), Quality.MODEL_DERIVED, "hp_standby", ["residual_split"])
            cats["hp_unclassified"] = EnergyInterval(start, end, round(unclassified, 6), Quality.MODEL_DERIVED, "hp_unclassified", ["residual"])
            total = cats["hp_total_metered"].kwh
            total_q = cats["hp_total_metered"].quality
        else:
            standby = standby_kw * slot_h if known == 0 else 0.0
            cats["hp_standby"] = EnergyInterval(start, end, round(standby, 6), Quality.MODEL_DERIVED, "hp_standby", ["assumed_standby_no_meter"])
            total = known + standby
            total_q = Quality.MODEL_DERIVED
        cats["hp_total"] = EnergyInterval(start, end, round(total, 6), total_q, "hp_total")
    return by_slot


def reconcile(by_slot: dict[datetime, dict[str, EnergyInterval]], tolerance_kwh: float = 0.01) -> dict:
    """Check categories sum to total. Returns a summary dict (never raises)."""
    worst = 0.0
    bad_slots = 0
    totals: dict[str, float] = {}
    for cats in by_slot.values():
        parts = sum(c.kwh for n, c in cats.items() if n not in ("hp_total", "hp_total_metered"))
        total = cats["hp_total"].kwh
        diff = abs(parts - total)
        worst = max(worst, diff)
        if diff > tolerance_kwh:
            bad_slots += 1
        for n, c in cats.items():
            totals[n] = totals.get(n, 0.0) + c.kwh
    return {
        "slots": len(by_slot),
        "slots_outside_tolerance": bad_slots,
        "worst_difference_kwh": round(worst, 6),
        "tolerance_kwh": tolerance_kwh,
        "totals_kwh": {k: round(v, 4) for k, v in totals.items()},
        "reconciled": bad_slots == 0,
    }
