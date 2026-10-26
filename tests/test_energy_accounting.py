from __future__ import annotations

from datetime import datetime, timedelta, timezone

from predheat.analytics import allocate_heat_pump_categories, counter_to_intervals, reconcile
from predheat.domain import Measurement, Provenance, Quality, Signal

UTC = timezone.utc


def _counter(values, start=datetime(2026, 10, 24, 0, 0, tzinfo=UTC), step=timedelta(hours=1), sig=Signal.HP_ELEC_HEATING_KWH):
    return [Measurement(signal=sig, ts=start + i * step, value=v, quality=Quality.MANUFACTURER_ESTIMATED, provenance=Provenance.VAILLANT_MYPYLLANT) for i, v in enumerate(values)]


def test_hourly_counter_splits_into_half_hours_and_is_labelled():
    ints = counter_to_intervals(_counter([10.0, 11.0, 11.5, 11.5]), "hp_elec_heating")
    assert len(ints) == 6
    assert abs(sum(i.kwh for i in ints) - 1.5) < 1e-9
    assert ints[0].kwh == 0.5 and ints[1].kwh == 0.5
    assert ints[0].quality == Quality.MODEL_DERIVED and "resampled_from_coarser_readings" in ints[0].notes


def test_counter_reset_is_handled_and_flagged():
    ints = counter_to_intervals(_counter([5.0, 6.0, 0.4, 1.0], step=timedelta(minutes=30)), "x")
    assert abs(sum(i.kwh for i in ints) - (1.0 + 0.4 + 0.6)) < 1e-9
    assert any("counter_reset" in i.notes for i in ints)


def test_duplicate_readings_are_ignored():
    ms = _counter([1.0, 2.0], step=timedelta(minutes=30))
    ms.append(ms[-1].model_copy())
    ints = counter_to_intervals(ms, "x")
    assert abs(sum(i.kwh for i in ints) - 1.0) < 1e-9


def test_categories_reconcile_to_total_with_residual_exposed():
    heat = counter_to_intervals(_counter([0, 1.0, 2.0], step=timedelta(minutes=30)), "hp_elec_heating")
    dhw = counter_to_intervals(_counter([0, 0.2, 0.2], step=timedelta(minutes=30), sig=Signal.HP_ELEC_DHW_KWH), "hp_elec_dhw")
    total = counter_to_intervals(_counter([0, 1.3, 2.35], step=timedelta(minutes=30), sig=Signal.GRID_IMPORT_KWH), "hp_total")
    by_slot = allocate_heat_pump_categories(heat, dhw, hp_total=total)
    r = reconcile(by_slot)
    assert r["reconciled"]
    first = by_slot[datetime(2026, 10, 24, 0, 0, tzinfo=UTC)]
    assert abs(first["hp_standby"].kwh + first["hp_unclassified"].kwh - 0.1) < 1e-9
    assert first["hp_unclassified"].quality == Quality.MODEL_DERIVED


def test_without_meter_standby_is_assumed_and_labelled():
    heat = counter_to_intervals(_counter([0, 0, 0.5], step=timedelta(minutes=30)), "hp_elec_heating")
    by_slot = allocate_heat_pump_categories(heat, [])
    first = by_slot[datetime(2026, 10, 24, 0, 0, tzinfo=UTC)]
    assert first["hp_standby"].kwh > 0 and "assumed_standby_no_meter" in first["hp_standby"].notes
    assert reconcile(by_slot)["reconciled"]
