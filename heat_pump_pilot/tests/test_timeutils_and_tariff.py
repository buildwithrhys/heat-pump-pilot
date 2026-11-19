"""Tariff intervals, UK clock changes and cost arithmetic."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from heat_pump_pilot.domain import Dispatch, DispatchKind, Provenance, RateInterval, StandingCharge
from heat_pump_pilot.pricing import PriceClass, build_rate_book, classify_slots, standing_charge_for_period
from heat_pump_pilot.timeutils import LONDON, slots_in_local_day

UTC = timezone.utc


def _iog_rates(day: date, tariff="E-1R-INTELLI-VAR-24-10-29-K", off=6.6, peak=36.0965) -> list[RateInterval]:
    out = []
    for s, e in slots_in_local_day(day):
        lt = s.astimezone(LONDON)
        mins = lt.hour * 60 + lt.minute
        rate = off if (mins >= 23 * 60 + 30 or mins < 5 * 60 + 30) else peak
        out.append(RateInterval(start=s, end=e, rate_p_per_kwh=rate, tariff_code=tariff, provenance=Provenance.OCTOPUS_HA))
    return out


def test_clock_change_days_have_46_and_50_slots():
    assert len(slots_in_local_day(date(2026, 10, 25))) == 50  # BST ends
    assert len(slots_in_local_day(date(2026, 3, 29))) == 46  # BST starts
    assert len(slots_in_local_day(date(2026, 10, 24))) == 48


def test_iog_offpeak_window_on_clock_change_day():
    rates = _iog_rates(date(2026, 10, 25))
    book = build_rate_book(rates)
    # 01:15 BST (00:15 UTC) and 01:15 GMT (01:15 UTC) are both off-peak and both exist
    assert book.rate_at(datetime(2026, 10, 25, 0, 15, tzinfo=UTC)).rate_p_per_kwh == 6.6
    assert book.rate_at(datetime(2026, 10, 25, 1, 15, tzinfo=UTC)).rate_p_per_kwh == 6.6
    # 05:30 GMT onwards is peak
    assert book.rate_at(datetime(2026, 10, 25, 5, 45, tzinfo=UTC)).rate_p_per_kwh == 36.0965
    # 23:30 GMT back to off-peak
    assert book.rate_at(datetime(2026, 10, 25, 23, 45, tzinfo=UTC)).rate_p_per_kwh == 6.6
    assert sum(1 for r in book.intervals if r.rate_p_per_kwh == 6.6) == 14  # 13 slots 00:00-05:30 inc extra hour, + 1 slot 23:30


def test_cost_straddling_rate_boundary_is_pro_rated():
    book = build_rate_book(_iog_rates(date(2026, 10, 24)))
    # 05:00-06:00 BST = 04:00-05:00 UTC: first half 6.6p, second half 36.0965p
    cost, eff, complete = book.cost_pence(datetime(2026, 10, 24, 4, 0, tzinfo=UTC), datetime(2026, 10, 24, 5, 0, tzinfo=UTC), 2.0)
    assert complete
    assert abs(cost - (1.0 * 6.6 + 1.0 * 36.0965)) < 1e-6
    assert abs(eff - (6.6 + 36.0965) / 2) < 1e-6


def test_missing_rates_are_flagged_not_silently_zeroed():
    book = build_rate_book(_iog_rates(date(2026, 10, 24)))
    cost, eff, complete = book.cost_pence(datetime(2026, 10, 25, 12, 0, tzinfo=UTC), datetime(2026, 10, 25, 12, 30, tzinfo=UTC), 1.0)
    assert not complete and cost == 0.0
    assert book.coverage_gaps(datetime(2026, 10, 25, 0, 0, tzinfo=UTC), datetime(2026, 10, 25, 1, 0, tzinfo=UTC))


def test_dispatch_classification_distinguishes_planned_from_confirmed():
    day = date(2026, 10, 25)
    book = build_rate_book(_iog_rates(day))
    now = datetime(2026, 10, 25, 22, 45, tzinfo=UTC)
    dispatches = [
        Dispatch(kind=DispatchKind.STARTED, start=datetime(2026, 10, 25, 7, 0, tzinfo=UTC), end=datetime(2026, 10, 25, 13, 0, tzinfo=UTC), observed_at=now),
        Dispatch(kind=DispatchKind.PLANNED, start=datetime(2026, 10, 25, 18, 0, tzinfo=UTC), end=datetime(2026, 10, 25, 19, 0, tzinfo=UTC), observed_at=now),
    ]
    slots = classify_slots(datetime(2026, 10, 24, 23, 0, tzinfo=UTC), datetime(2026, 10, 25, 23, 0, tzinfo=UTC), book, dispatches)
    by_start = {s.start: s for s in slots}
    assert by_start[datetime(2026, 10, 25, 0, 0, tzinfo=UTC)].price_class == PriceClass.GUARANTEED_OFF_PEAK
    s9 = by_start[datetime(2026, 10, 25, 9, 0, tzinfo=UTC)]
    assert s9.price_class == PriceClass.CONFIRMED_DISPATCH and s9.rate_p_per_kwh == 6.6 and not s9.assumed
    s18 = by_start[datetime(2026, 10, 25, 18, 0, tzinfo=UTC)]
    assert s18.price_class == PriceClass.PLANNED_DISPATCH and s18.assumed
    assert by_start[datetime(2026, 10, 25, 15, 0, tzinfo=UTC)].price_class == PriceClass.PEAK
    assert len(slots) == 48


def test_standing_charge_is_a_whole_day_across_clock_change():
    charges = [StandingCharge(start=datetime(2026, 10, 1, tzinfo=UTC), end=None, pence_per_day=54.3619, tariff_code="x")]
    # Whole local day of 25 hours still costs one day's standing charge
    s = datetime(2026, 10, 25, tzinfo=LONDON).astimezone(UTC)
    e = datetime(2026, 10, 26, tzinfo=LONDON).astimezone(UTC)
    assert (e - s) == timedelta(hours=25)
    assert abs(standing_charge_for_period(charges, s, e) - 54.3619) < 1e-6
    # Half a normal day still pays the whole day: the standing charge is never pro-rated
    s2 = datetime(2026, 10, 24, tzinfo=LONDON).astimezone(UTC)
    assert abs(standing_charge_for_period(charges, s2, s2 + timedelta(hours=12)) - 54.3619) < 1e-6


def _cosy_rates(day: date, tariff="E-1R-COSY-22-12-08-A") -> list[RateInterval]:
    """Cosy: cheap 04-07, 13-16, 22-24; peak 16-19; standard otherwise (including 00:00-04:00)."""
    out = []
    for s, e in slots_in_local_day(day):
        h = s.astimezone(LONDON).hour
        rate = 12.6 if (4 <= h < 7 or 13 <= h < 16 or h >= 22) else 36.9 if 16 <= h < 19 else 25.2
        out.append(RateInterval(start=s, end=e, rate_p_per_kwh=rate, tariff_code=tariff, provenance=Provenance.OCTOPUS_HA))
    return out


def test_cosy_night_is_priced_from_its_rates_not_the_iog_window():
    from heat_pump_pilot.pricing import tariff_family
    day = date(2026, 11, 17)
    code = "E-1R-COSY-22-12-08-A"
    assert tariff_family(code) == "time_of_use" and tariff_family("E-1R-INTELLI-VAR-24-10-29-K") == "intelligent_go"
    book = build_rate_book(_cosy_rates(day), code)
    t0 = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)  # GMT, so local == UTC
    slots = {s.start.astimezone(LONDON).hour * 60 + s.start.astimezone(LONDON).minute: s
             for s in classify_slots(t0, t0 + timedelta(days=1), book, [], family=tariff_family(code))}
    assert slots[0].rate_p_per_kwh == 25.2 and slots[0].price_class == PriceClass.PEAK  # 00:00 is standard on Cosy
    assert slots[3 * 60 + 30].price_class == PriceClass.PEAK
    assert slots[4 * 60].rate_p_per_kwh == 12.6 and slots[4 * 60].price_class == PriceClass.GUARANTEED_OFF_PEAK
    assert slots[23 * 60 + 30].price_class == PriceClass.GUARANTEED_OFF_PEAK
    assert slots[17 * 60].rate_p_per_kwh == 36.9 and not slots[17 * 60].assumed


def test_cosy_beyond_published_rates_repeats_the_latest_day():
    from heat_pump_pilot.pricing import tariff_family
    code = "E-1R-COSY-22-12-08-A"
    book = build_rate_book(_cosy_rates(date(2026, 11, 17)), code)
    t1 = datetime(2026, 11, 18, 0, 0, tzinfo=UTC)
    slots = {s.start.hour: s for s in classify_slots(t1, t1 + timedelta(days=1), book, [], family=tariff_family(code)) if s.start.minute == 0}
    assert slots[1].rate_p_per_kwh == 25.2 and slots[1].assumed
    assert slots[5].rate_p_per_kwh == 12.6 and slots[5].price_class == PriceClass.GUARANTEED_OFF_PEAK and slots[5].assumed
