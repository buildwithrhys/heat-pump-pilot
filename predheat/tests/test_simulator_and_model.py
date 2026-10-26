"""The simulator must be physically sensible and the RC fitter must recover its parameters."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from predheat.integrations.simulated import SimConfig, SimulatedHome
from predheat.model import fit_rc_model, simulate_rc, survey_to_coefficient, RCParameters


@pytest.fixture(scope="module")
def home() -> SimulatedHome:
    return SimulatedHome(SimConfig(seed=3, days=10))


def test_simulator_keeps_comfort_and_plausible_cop(home):
    t_in = np.array([r["t_in"] for r in home.records])
    assert 17.5 < t_in.min() and t_in.max() < 24.5
    last = home.records[-1]
    cop_heating = last["heat_heating_kwh"] / last["elec_heating_kwh"]
    assert 2.5 < cop_heating < 5.0
    assert last["heat_dhw_kwh"] > 0 and last["elec_dhw_kwh"] > 0
    assert last["cycles"] > 10


def test_simulator_tariff_window(home):
    off = [r for r in home.records if r["rate_p"] == home.cfg.offpeak_rate]
    peak = [r for r in home.records if r["rate_p"] == home.cfg.peak_rate]
    assert len(off) > 0 and len(peak) > 0
    # the fixed window alone is 6 of 24 hours; dispatches add some
    frac = len(off) / len(home.records)
    assert 0.25 <= frac <= 0.45


def test_survey_conversion():
    assert abs(survey_to_coefficient(5.4, 21, -3) - 225.0) < 1e-9


def test_rc_fit_recovers_ground_truth_from_half_hourly_data(home):
    recs = home.records[::6]  # 30-minute samples
    t_in = np.array([r["t_in"] for r in recs])
    t_out = np.array([r["t_out"] for r in recs])
    # Average heat delivered over each half hour from the cumulative counter
    heat_kwh = np.array([r["heat_heating_kwh"] for r in recs])
    q_kw = np.append(np.diff(heat_kwh) / 0.5, 0.0)
    ghi = np.array([r["ghi"] for r in recs]) / 1000.0
    p = fit_rc_model(t_in, t_out, q_kw, ghi, step_hours=0.5)
    truth = home.ground_truth()
    assert abs(p.ua_w_per_k - truth["ua_w_per_k"]) / truth["ua_w_per_k"] < 0.15
    assert abs(p.capacity_kwh_per_k - truth["capacity_kwh_per_k"]) / truth["capacity_kwh_per_k"] < 0.25
    # The statistical interval is narrow (it only reflects residual noise, not
    # discretisation bias, see VALIDATION.md), so check it brackets the estimate and
    # that the truth lies within a few half-widths of it.
    assert p.ua_ci_low < p.ua_w_per_k < p.ua_ci_high
    half_width = (p.ua_ci_high - p.ua_ci_low) / 2
    assert abs(p.ua_w_per_k - truth["ua_w_per_k"]) < max(4 * half_width, 0.05 * truth["ua_w_per_k"])
    assert p.confidence > 0.5


def test_rc_fit_reports_poor_identifiability_when_flat():
    n = 200
    t_in = np.full(n, 21.0)
    t_out = np.full(n, 10.0)
    q = np.full(n, 2.0)
    p = fit_rc_model(t_in, t_out, q, step_hours=0.5)
    assert p.notes and any("variation" in x for x in p.notes)
    assert p.confidence < 0.7


def test_simulate_rc_roundtrip():
    p = RCParameters(ua_w_per_k=180, capacity_kwh_per_k=6, solar_aperture_m2=0, internal_gain_kw=0)
    t_out = np.full(48, 0.0)
    q = np.full(48, 180 * 21 / 1000)  # exactly balances losses at 21C
    t = simulate_rc(p, 21.0, t_out, q)
    assert np.allclose(t, 21.0)
