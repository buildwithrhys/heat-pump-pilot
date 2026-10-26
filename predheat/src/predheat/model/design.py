"""Design outdoor temperature by UK location.

Heat loss surveys quote kilowatts at a regional design outdoor temperature
(CIBSE Guide A, roughly the coldest temperature reached in a typical winter).
This picks a sensible value from the home's latitude and longitude; the user
can override it in Settings.
"""

from __future__ import annotations

DEFAULT_DESIGN_C = -3.0
DESIGN_INDOOR_C = 21.0


def design_outdoor_c(latitude: float | None, longitude: float | None) -> tuple[float, str]:
    """Return (design temperature, region label)."""
    if latitude is None or longitude is None:
        return DEFAULT_DESIGN_C, "UK default"
    lat, lon = latitude, longitude
    if lat >= 56.0:
        return -4.5, "northern Scotland"
    if lat >= 54.8:
        return -4.0, "southern Scotland and the far north of England"
    if lat < 51.5 and lon < -4.0:
        return -2.0, "Cornwall and west Devon"
    if lon < -2.8 and 51.3 <= lat < 53.5:
        return -3.0, "Wales"
    if lat < 51.8 and lon > -2.0:
        return -2.0, "London and the south east"
    if lat < 51.8:
        return -2.5, "southern England"
    if lat >= 53.2:
        return -3.5, "northern England"
    return -3.0, "the Midlands"
