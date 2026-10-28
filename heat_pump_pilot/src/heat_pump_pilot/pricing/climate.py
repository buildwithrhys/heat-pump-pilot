"""Typical-year monthly mean temperatures for Great Britain by latitude band (Met Office 1991 to 2020 averages, rounded)."""

from __future__ import annotations

from typing import Any

# label, latitude threshold, Jan..Dec means in degC
BANDS = [
    ("south west and south Wales", 52.0, [5.5, 5.6, 7.3, 9.4, 12.3, 15.0, 17.0, 16.9, 14.7, 11.5, 8.2, 5.9]),
    ("southern and eastern England", 52.0, [5.0, 5.3, 7.3, 9.6, 12.8, 15.8, 18.0, 17.8, 15.2, 11.7, 7.9, 5.4]),
    ("midlands and north Wales", 53.5, [4.3, 4.5, 6.4, 8.7, 11.7, 14.5, 16.5, 16.2, 13.8, 10.5, 7.0, 4.6]),
    ("northern England", 55.0, [4.0, 4.1, 5.8, 8.0, 10.9, 13.6, 15.6, 15.4, 13.2, 10.0, 6.7, 4.3]),
    ("Scotland", 99.0, [3.3, 3.5, 5.0, 7.2, 9.9, 12.4, 14.3, 14.1, 12.1, 9.0, 5.8, 3.5]),
]
DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


# Octopus tariff region letter (grid supply area) -> climate band label
REGION_BAND = {"A": "southern and eastern England", "B": "midlands and north Wales", "C": "southern and eastern England",
               "D": "midlands and north Wales", "E": "midlands and north Wales", "F": "northern England", "G": "northern England",
               "H": "southern and eastern England", "J": "southern and eastern England", "K": "south west and south Wales",
               "L": "south west and south Wales", "M": "northern England", "N": "Scotland", "P": "Scotland"}


def band_for(region: str | None = None, latitude: float | None = None) -> dict[str, Any]:
    """The climate band for the home: from the tariff region when known, else latitude."""
    if region and region in REGION_BAND:
        label = REGION_BAND[region]
        means = next(b[2] for b in BANDS if b[0] == label)
        return {"label": label, "monthly_mean_c": means, "source": f"tariff region {region}"}
    out = typical_year(latitude)
    out["source"] = "home latitude" if latitude is not None else "UK default"
    return out


def typical_year(latitude: float | None) -> dict[str, Any]:
    lat = latitude if latitude is not None else 52.5
    for label, limit, means in [b for b in BANDS if b[0] != "southern and eastern England"]:
        if lat < limit:
            return {"label": label, "monthly_mean_c": means}
    return {"label": BANDS[-1][0], "monthly_mean_c": BANDS[-1][2]}


def annual_degree_days(year: dict[str, Any], base_c: float = 15.5) -> float:
    """Heating degree-days from monthly means. Monthly means understate degree-days a little
    because cold nights within a month count more than the mean suggests; a 1.08 factor corrects
    this against daily-data totals."""
    return 1.08 * sum(max(0.0, base_c - t) * d for t, d in zip(year["monthly_mean_c"], DAYS))
