"""Energy accounting: cumulative counters -> interval energy -> categories -> reconciliation.

Rules
- Counters are differenced against a running maximum; see ``counter_to_intervals``
  for the rules on resets, restatements, late lumps and re-baselines. Each counter
  has a ``CounterProfile`` saying how fast it can rise and how late its data arrives.
  Never add a lifetime register reading as if it were an interval.
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


MAX_RATE_KWH_PER_H = 25.0  # default plausibility bound for an unknown counter


@dataclass(frozen=True)
class CounterProfile:
    """How a particular cumulative counter behaves, so its readings are differenced correctly.

    ``max_rate_kwh_per_h``: the most energy the thing can use or make in an hour.
    ``lag_h``: data may arrive this late, in lumps (myVAILLANT publishes hourly buckets one to
        two hours late and sometimes several at once). A lump is plausible if it fits in
        ``max_rate * (gap + lag)``, and it is spread over at least an hour before it arrived.
    ``daily_reset``: the counter is a daily total that restarts at local midnight.
    ``max_gap_h``: beyond this gap between readings the energy is not attributed at all
        (missing data), because a step across a long hole cannot be told apart from a re-baseline.
    """

    max_rate_kwh_per_h: float = MAX_RATE_KWH_PER_H
    lag_h: float = 0.0
    daily_reset: bool = False
    max_gap_h: float | None = None


_VAILLANT_ELEC = CounterProfile(max_rate_kwh_per_h=10.0, lag_h=3.0, daily_reset=True)
_VAILLANT_HEAT = CounterProfile(max_rate_kwh_per_h=30.0, lag_h=3.0, daily_reset=True)
_METER = CounterProfile(max_rate_kwh_per_h=24.0, lag_h=0.0, daily_reset=False, max_gap_h=2.0)  # 100 A at 240 V
COUNTER_PROFILES: dict[str, CounterProfile] = {
    "hp_elec_heating_kwh": _VAILLANT_ELEC, "hp_elec_dhw_kwh": _VAILLANT_ELEC,
    "aux_elec_heating_kwh": _VAILLANT_ELEC, "aux_elec_dhw_kwh": _VAILLANT_ELEC,
    "hp_heat_heating_kwh": _VAILLANT_HEAT, "hp_heat_dhw_kwh": _VAILLANT_HEAT,
    "hp_env_heating_kwh": _VAILLANT_HEAT, "hp_env_dhw_kwh": _VAILLANT_HEAT,
    "grid_import_kwh": _METER, "grid_export_kwh": _METER,
}


def profile_for(readings: list[Measurement]) -> CounterProfile:
    if not readings:
        return CounterProfile()
    sig = readings[0].signal
    return COUNTER_PROFILES.get(getattr(sig, "value", str(sig)), CounterProfile())


def _local_midnight(t: datetime) -> datetime:
    from ..timeutils import LONDON

    lt = t.astimezone(LONDON)
    return datetime(lt.year, lt.month, lt.day, tzinfo=LONDON).astimezone(t.tzinfo)


def counter_to_intervals(
    readings: list[Measurement],
    category: str,
    slot: timedelta = SLOT,
    max_gap: timedelta = timedelta(hours=3),
    resolution_downgrade: bool = True,
    max_rate_kwh_per_h: float | None = None,
    profile: CounterProfile | None = None,
    discards: list[dict] | None = None,
) -> list[EnergyInterval]:
    """Convert cumulative counter readings into half-hour energy intervals.

    Rules, in order:
    - Each source (live sensor, Home Assistant statistics) is differenced on its own,
      then each slot is taken from the preferred source (live first).
    - A running maximum is the baseline: a reading below it is a restatement and
      contributes nothing, and the next reading is measured from the maximum, not from
      the low value (which would count the same energy twice).
    - A drop is a reset when a daily counter crosses local midnight, or when the value
      falls to near zero; the new value then counts from zero.
    - A rise larger than the counter could physically produce in the gap plus its
      reporting lag is a re-baseline, not energy: discarded and recorded in ``discards``.
    - For meters, a step across a gap longer than ``max_gap_h`` is not attributed.
    - Late lumps are spread over at least the hour before they arrived (not past midnight
      for daily counters); a reset is spread from local midnight.
    """
    prof = profile or profile_for(readings)
    if max_rate_kwh_per_h is not None:
        prof = CounterProfile(max_rate_kwh_per_h, prof.lag_h, prof.daily_reset, prof.max_gap_h)
    by_source: dict[str | None, list[Measurement]] = {}
    for m in readings:
        by_source.setdefault(m.source_ref, []).append(m)
    if len(by_source) > 1:
        def rank(src: str | None) -> tuple[int, int]:
            return (0 if src and "#statistics" in src else 1, len(by_source[src]))

        ordered = sorted(by_source, key=rank, reverse=True)
        merged: dict[datetime, EnergyInterval] = {}
        for src in ordered:
            for iv in counter_to_intervals(by_source[src], category, slot, max_gap, resolution_downgrade, None, prof, discards):
                if iv.start not in merged:
                    if src and "#statistics" in src:
                        iv.notes.append("from_statistics")
                    merged[iv.start] = iv
        return sorted(merged.values(), key=lambda b: b.start)

    pts = sorted({(m.ts, m.value) for m in readings if m.value is not None})
    if len(pts) < 2:
        return []
    quality_in = readings[0].quality
    source = readings[0].source_ref
    buckets: dict[datetime, EnergyInterval] = {}

    def bucket(s: datetime) -> EnergyInterval:
        b = buckets.get(s)
        if b is None:
            b = EnergyInterval(s, s + slot, 0.0, quality_in, category)
            buckets[s] = b
        return b

    def spread(w0: datetime, w1: datetime, delta: float, notes: list[str], coarse: bool) -> None:
        span = (w1 - w0).total_seconds()
        if span <= 0:
            return  # zero energy still creates intervals: "known to be zero" is data
        s0 = floor_to_slot(w0)
        while s0 < w1:
            e0 = s0 + slot
            frac = overlap_seconds(s0, e0, w0, w1) / span
            if frac > 0:
                b = bucket(s0)
                b.kwh += delta * frac
                if resolution_downgrade and coarse:
                    b.quality = Quality.MODEL_DERIVED if quality_in != Quality.SIMULATED else quality_in
                    if "resampled_from_coarser_readings" not in b.notes:
                        b.notes.append("resampled_from_coarser_readings")
                for n in notes:
                    if n not in b.notes:
                        b.notes.append(n)
            s0 = e0

    def discard(t0: datetime, t1: datetime, delta: float, reason: str) -> None:
        if discards is not None:
            discards.append({"signal": getattr(readings[0].signal, "value", str(readings[0].signal)), "source": source,
                             "from": t0.isoformat(), "to": t1.isoformat(), "kwh": round(delta, 3), "reason": reason})

    t_prev, base = pts[0]
    day_total_before_reset: float | None = None
    for t1, v1 in pts[1:]:
        if t1 <= t_prev:
            continue  # a second value with the same timestamp: the baseline already covers it
        gap = t1 - t_prev
        gap_h = gap.total_seconds() / 3600.0
        notes: list[str] = []
        crosses_midnight = _local_midnight(t1) > t_prev
        w0, w1 = t_prev, t1
        if v1 < base:
            near_zero = v1 < (0.5 if prof.daily_reset else 0.05) * base
            if (prof.daily_reset and crosses_midnight) or near_zero:
                delta = v1
                notes.append("counter_reset")
                day_total_before_reset = base
                base = v1
                if prof.daily_reset:
                    w0 = max(_local_midnight(t1), t_prev)
            else:
                notes.append("non_monotonic_ignored")
                spread(t_prev, t1, 0.0, notes, gap > slot + timedelta(minutes=1))
                t_prev = t1
                continue
        else:
            delta = v1 - base
            # yesterday's late total reappearing just after a midnight reset is not new energy
            if (prof.daily_reset and day_total_before_reset is not None and v1 >= 0.98 * day_total_before_reset
                    and (t1 - _local_midnight(t1)).total_seconds() / 3600.0 < prof.lag_h and delta > 0):
                discard(t_prev, t1, delta, "stale previous-day total after reset")
                t_prev = t1
                continue
            if prof.max_gap_h is not None and gap_h > prof.max_gap_h and delta > 0:
                discard(t_prev, t1, delta, f"gap of {gap_h:.1f} h with no readings; not attributed")
                base = v1
                t_prev = t1
                continue
            if delta > prof.max_rate_kwh_per_h * (gap_h + prof.lag_h):
                discard(t_prev, t1, delta, f"rise of {delta:.1f} kWh in {gap_h * 60:.0f} min is more than this counter can produce")
                b = bucket(floor_to_slot(t_prev))
                if "jump_ignored" not in b.notes:
                    b.notes.append("jump_ignored")
                base = v1
                t_prev = t1
                continue
            base = v1
            if prof.lag_h > 0 and delta > prof.max_rate_kwh_per_h * gap_h:
                # a late lump (more than could accrue in the gap): spread it over the time it
                # needed before it arrived, at least an hour, never back past midnight for a daily total
                need = timedelta(hours=max(1.0, delta / prof.max_rate_kwh_per_h))
                w0 = t1 - need
                if prof.daily_reset:
                    w0 = max(w0, _local_midnight(t1))
                notes.append("late_lump_spread")
        if gap > max_gap:
            notes.append(f"gap_{int(gap.total_seconds() // 60)}min")
        spread(w0, w1, delta, notes, gap > slot + timedelta(minutes=1))
        t_prev = t1
    out = sorted(buckets.values(), key=lambda b: b.start)
    for b in out:
        b.kwh = round(b.kwh, 6)
    return out


def energy_balance(elec_kwh: float, heat_kwh: float, env_kwh: float | None, tolerance: float = 0.10) -> dict:
    """Check a heat pump's own figures add up: heat delivered = electricity + heat from outside air.

    Returns ``status``: ok, inconsistent (the three do not add up within ``tolerance``),
    implausible (no environment figure and a COP below 1 or above 7), idle (too little running).
    ``cop`` is set only when the status is ok. A COP below 1 is never reported.
    """
    if elec_kwh < 0.2:
        return {"status": "idle", "cop": None, "elec_kwh": elec_kwh, "heat_kwh": heat_kwh, "env_kwh": env_kwh}
    cop = heat_kwh / elec_kwh if elec_kwh else None
    if env_kwh is not None and heat_kwh > 0:
        gap = abs(heat_kwh - (elec_kwh + env_kwh)) / max(heat_kwh, elec_kwh + env_kwh)
        if gap > tolerance:
            return {"status": "inconsistent", "cop": None, "elec_kwh": elec_kwh, "heat_kwh": heat_kwh, "env_kwh": env_kwh,
                    "mismatch_pct": round(gap * 100.0, 1)}
    elif env_kwh is not None:
        return {"status": "inconsistent", "cop": None, "elec_kwh": elec_kwh, "heat_kwh": heat_kwh, "env_kwh": env_kwh, "mismatch_pct": 100.0}
    if cop is None or cop < 1.0 or cop > 7.0:
        return {"status": "implausible", "cop": None, "elec_kwh": elec_kwh, "heat_kwh": heat_kwh, "env_kwh": env_kwh}
    return {"status": "ok", "cop": round(cop, 2), "elec_kwh": elec_kwh, "heat_kwh": heat_kwh, "env_kwh": env_kwh}


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
