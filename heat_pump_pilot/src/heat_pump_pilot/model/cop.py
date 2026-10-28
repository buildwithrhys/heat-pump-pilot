"""Heat pump efficiency model: COP as a linear function of outdoor temperature.

Fitted from aligned electricity and heat intervals (hourly buckets, weighted by
electricity used so idle hours do not dominate). A weak prior keeps the first
estimates sensible: a typical air source unit on a radiator system gives about
3.0 at 7 degC and loses about 0.07 per degree as it gets colder.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

PRIOR_A = 3.0 - 0.07 * 7.0  # COP at 0 degC
PRIOR_B = 0.07  # per degC
COP_MIN, COP_MAX = 1.5, 6.0


@dataclass
class COPModel:
    a: float  # COP at 0 degC outdoor
    b: float  # change per degC
    n_samples: int = 0
    rmse: float | None = None
    confidence: float = 0.0
    t_min: float | None = None
    t_max: float | None = None
    observed_mean: float | None = None
    notes: list[str] = field(default_factory=list)

    def cop_at(self, t_out: float) -> float:
        return float(np.clip(self.a + self.b * t_out, COP_MIN, COP_MAX))

    def as_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "n_samples": self.n_samples, "rmse": self.rmse, "confidence": self.confidence,
                "t_min": self.t_min, "t_max": self.t_max, "observed_mean": self.observed_mean, "notes": self.notes}


def prior_cop_model() -> COPModel:
    return COPModel(PRIOR_A, PRIOR_B, notes=["prior only: no measured data yet"])


def fit_cop_model(t_out, elec_kwh, heat_kwh, min_elec_kwh: float = 0.3, prior_weight: float = 3.0) -> COPModel:
    """Weighted ridge regression of COP on outdoor temperature.

    Rows with less than ``min_elec_kwh`` electricity are dropped (COP is noise
    when the unit barely ran) and implausible ratios outside 1 to 7 are dropped.
    The prior enters as pseudo-observations worth ``prior_weight`` kWh at 0 and
    10 degC, so it fades as real data accumulates.
    """
    t = np.asarray(t_out, float)
    e = np.asarray(elec_kwh, float)
    h = np.asarray(heat_kwh, float)
    ok = np.isfinite(t) & np.isfinite(e) & np.isfinite(h) & (e >= min_elec_kwh) & (h > 0)
    t, e, h = t[ok], e[ok], h[ok]
    cop = h / np.where(e > 0, e, np.nan)
    plaus = (cop >= 1.0) & (cop <= 7.0)
    t, e, cop = t[plaus], e[plaus], cop[plaus]
    notes: list[str] = []
    if len(t) == 0:
        return prior_cop_model()
    if len(t) < 24:
        notes.append("fewer than 24 hours with meaningful heat pump running")
    if np.ptp(t) < 5.0:
        notes.append("outdoor temperature varied less than 5 degC; slope poorly identified")
    # weighted least squares with prior pseudo-rows
    X = np.column_stack([np.ones(len(t)), t])
    w = e.copy()
    Xp = np.array([[1.0, 0.0], [1.0, 10.0]])
    yp = np.array([PRIOR_A, PRIOR_A + PRIOR_B * 10.0])
    wp = np.array([prior_weight, prior_weight])
    XA = np.vstack([X, Xp])
    yA = np.concatenate([cop, yp])
    wA = np.concatenate([w, wp])
    W = np.sqrt(wA)[:, None]
    theta, *_ = np.linalg.lstsq(XA * W, yA * W[:, 0], rcond=None)
    a, b = float(theta[0]), float(theta[1])
    if b < 0:
        notes.append("negative slope (COP falling as it gets warmer); kept but suspicious")
    resid = cop - (a + b * t)
    rmse = float(np.sqrt(np.average(resid**2, weights=w))) if len(t) else None
    total_w = float(w.sum())
    data_share = total_w / (total_w + wp.sum())
    spread = min(1.0, float(np.ptp(t)) / 10.0) if len(t) > 1 else 0.0
    confidence = round(float(np.clip(data_share * (0.5 + 0.5 * spread) * (1.0 if rmse is None or rmse < 0.8 else 0.6), 0, 1)), 2)
    return COPModel(a, b, n_samples=int(len(t)), rmse=rmse, confidence=confidence, t_min=float(t.min()), t_max=float(t.max()),
                    observed_mean=float(np.average(cop, weights=w)), notes=notes)
