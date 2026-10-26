"""Typical-year monthly mean temperatures for Great Britain by latitude band (Met Office 1991 to 2020 averages, rounded)."""

from __future__ import annotations

from typing import Any

# label, latitude threshold, Jan..Dec means in degC
BANDS = [
    ("south west and south Wales", 52.0, [5.5, 5.6, 7.3, 9.4, 12.3, 15.0, 17.0, 16.9, 14.7, 11.5, 8.2, 5.9]),
    ("midlands and north Wales", 53.5, [4.3, 4.5, 6.4, 8.7, 11.7, 14.5, 16.5, 16.2, 13.8, 10.5, 7.0, 4.6]),
    ("northern England", 55.0, [4.0, 4.1, 5.8, 8.0, 10.9, 13.6, 15.6, 15.4, 13.2, 10.0, 6.7, 4.3]),
    ("Scotland", 99.0, [3.3, 3.5, 5.0, 7.2, 9.9, 12.4, 14.3, 14.1, 12.1, 9.0, 5.8, 3.5]),
]
DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def typical_year(latitude: float | None) -> dict[str, Any]:
    lat = latitude if latitude is not None else 52.5
    for label, limit, means in BANDS:
        if lat < limit:
            return {"label": label, "monthly_mean_c": means}
    return {"label": BANDS[-1][0], "monthly_mean_c": BANDS[-1][2]}


def annual_degree_days(year: dict[str, Any], base_c: float = 15.5) -> float:
    """Heating degree-days from monthly means. Monthly means understate degree-days a little
    because cold nights within a month count more than the mean suggests; a 1.08 factor corrects
    this against daily-data totals."""
    return 1.08 * sum(max(0.0, base_c - t) * d for t, d in zip(year["monthly_mean_c"], DAYS))
