"""Single-zone 1R1C building model: Phase 3 foundation.

Discrete form (step h hours):
    T_in[k+1] = T_in[k] + h / C * ( Q_heat[k] + a * G[k] + Q_int - UA * (T_in[k] - T_out[k]) )

with UA in kW/K (reported as W/K), C in kWh/K, Q in kW, G irradiance proxy in kW/m2,
a an effective solar aperture (m2). The regression is linear in
(UA/C, 1/C, a/C, Q_int/C) so ordinary ridge least squares identifies it, with
a survey prior on UA when provided. This is deliberately interpretable; it is
not the final learner, but it is enough to validate against the simulator's
known parameters and to show what data variation is needed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class RCParameters:
    ua_w_per_k: float
    capacity_kwh_per_k: float
    solar_aperture_m2: float
    internal_gain_kw: float
    ua_ci_low: float | None = None
    ua_ci_high: float | None = None
    n_samples: int = 0
    rmse_c: float | None = None
    confidence: float = 0.0
    notes: list[str] | None = None


def survey_to_coefficient(design_kw: float, design_indoor_c: float, design_outdoor_c: float) -> float:
    """Convert a survey design heat loss to W/K."""
    dt = design_indoor_c - design_outdoor_c
    if dt <= 0:
        raise ValueError("design indoor temperature must exceed design outdoor temperature")
    return design_kw * 1000.0 / dt


def simulate_rc(p: RCParameters, t_in0: float, t_out: np.ndarray, q_heat_kw: np.ndarray,
                irradiance_kw_m2: np.ndarray | None = None, step_hours: float = 0.5) -> np.ndarray:
    n = len(t_out)
    g = irradiance_kw_m2 if irradiance_kw_m2 is not None else np.zeros(n)
    t = np.empty(n + 1)
    t[0] = t_in0
    ua_kw = p.ua_w_per_k / 1000.0
    for k in range(n):
        q = q_heat_kw[k] + p.solar_aperture_m2 * g[k] + p.internal_gain_kw - ua_kw * (t[k] - t_out[k])
        t[k + 1] = t[k] + step_hours / p.capacity_kwh_per_k * q
    return t[1:]


def fit_rc_model(
    t_in: np.ndarray,
    t_out: np.ndarray,
    q_heat_kw: np.ndarray,
    irradiance_kw_m2: np.ndarray | None = None,
    step_hours: float = 0.5,
    survey_ua_w_per_k: float | None = None,
    survey_weight: float = 0.05,
    ridge: float = 1e-6,
) -> RCParameters:
    """Fit UA, C, solar aperture and internal gain by regularised least squares.

    Rows where any input is NaN are dropped. ``survey_ua_w_per_k`` acts as a weak
    prior (pseudo-observation) so early estimates stay sensible with little data.
    """
    t_in = np.asarray(t_in, float)
    t_out = np.asarray(t_out, float)
    q = np.asarray(q_heat_kw, float)
    g = np.asarray(irradiance_kw_m2, float) if irradiance_kw_m2 is not None else np.zeros_like(t_in)
    n = len(t_in) - 1
    y = (t_in[1:] - t_in[:-1]) / step_hours  # degC per hour
    X = np.column_stack([-(t_in[:-1] - t_out[:-1]), q[:-1], g[:-1], np.ones(n)])  # theta = [UA/C, 1/C, a/C, Qint/C]
    ok = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    X, y = X[ok], y[ok]
    notes: list[str] = []
    if len(y) < 24:
        notes.append("fewer than 24 usable samples")
    if np.std(X[:, 0]) < 1.0:
        notes.append("little indoor/outdoor temperature variation; UA poorly identified")
    if np.std(X[:, 1]) < 0.1:
        notes.append("little heating variation; capacity poorly identified")

    A = X.T @ X + ridge * np.eye(4)
    b = X.T @ y
    theta = np.linalg.solve(A, b)
    # Weak survey prior on UA = theta0/theta1, applied iteratively as a pseudo-row.
    if survey_ua_w_per_k is not None and theta[1] > 0:
        for _ in range(3):
            inv_c = theta[1]
            w = survey_weight * len(y)
            row = np.array([1.0, 0.0, 0.0, 0.0])
            target = survey_ua_w_per_k / 1000.0 * inv_c
            A2 = A + w * np.outer(row, row)
            b2 = b + w * row * target
            theta = np.linalg.solve(A2, b2)
    ua_over_c, inv_c, a_over_c, qint_over_c = theta
    if inv_c <= 0:
        notes.append("capacity estimate non-physical; returning survey or NaN")
        return RCParameters(float(survey_ua_w_per_k or np.nan), np.nan, np.nan, np.nan, n_samples=len(y), notes=notes)
    C = 1.0 / inv_c
    ua_kw = ua_over_c * C
    resid = y - X @ theta
    dof = max(1, len(y) - 4)
    sigma2 = float(resid @ resid) / dof
    cov = sigma2 * np.linalg.inv(A)
    # Delta method for UA = theta0/theta1
    grad = np.array([C, -ua_over_c * C * C, 0.0, 0.0])
    ua_var = float(grad @ cov @ grad)
    ua_sd = np.sqrt(max(ua_var, 0.0))
    rmse = float(np.sqrt(np.mean((resid * step_hours) ** 2)))
    rel_ci = (1.96 * ua_sd) / max(ua_kw, 1e-6)
    confidence = float(np.clip(1.0 - rel_ci, 0.0, 1.0)) * (1.0 if not notes else 0.6)
    return RCParameters(
        ua_w_per_k=float(ua_kw * 1000.0),
        capacity_kwh_per_k=float(C),
        solar_aperture_m2=float(a_over_c * C),
        internal_gain_kw=float(qint_over_c * C),
        ua_ci_low=float((ua_kw - 1.96 * ua_sd) * 1000.0),
        ua_ci_high=float((ua_kw + 1.96 * ua_sd) * 1000.0),
        n_samples=len(y),
        rmse_c=rmse,
        confidence=confidence,
        notes=notes,
    )
