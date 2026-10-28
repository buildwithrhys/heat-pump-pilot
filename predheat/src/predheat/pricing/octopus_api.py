"""Direct Octopus Energy API: product unit rates by region.

The products and rates endpoints are public, so no key is needed for the
tariff comparison or Agile history. Consumption needs a key and the meter
identifiers; Heat Pump Pilot reads consumption through Home Assistant instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..domain import Provenance, RateInterval

BASE = "https://api.octopus.energy/v1"

# Current product codes per tariff family; checked against the products list at run time.
PRODUCT_CODES = {"AGILE": "AGILE-24-10-01", "INTELLI-VAR": "INTELLI-VAR-22-10-14", "GO-VAR": "GO-VAR-22-10-14", "COSY": "COSY-22-12-08", "FLEX": "VAR-22-11-01"}


class OctopusAPI:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20.0):
        self._http = httpx.AsyncClient(base_url=BASE, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def products(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        url: str | None = "/products/?is_variable=true&is_business=false"
        while url:
            r = await self._http.get(url)
            r.raise_for_status()
            doc = r.json()
            out.extend(doc.get("results", []))
            url = doc.get("next")
            if url and url.startswith(BASE):
                url = url[len(BASE):]
        return out

    async def latest_product_code(self, family: str) -> str | None:
        """The most recent product whose code starts with the family prefix (e.g. AGILE-)."""
        try:
            prods = await self.products()
        except Exception:  # noqa: BLE001
            return PRODUCT_CODES.get(family)
        cands = sorted([p["code"] for p in prods if p.get("code", "").startswith(family + "-") and p.get("direction") == "IMPORT"], reverse=True)
        return cands[0] if cands else PRODUCT_CODES.get(family)

    async def unit_rates(self, product_code: str, region: str, start: datetime, end: datetime, tariff_code: str | None = None) -> list[RateInterval]:
        tariff = tariff_code or f"E-1R-{product_code}-{region}"
        url: str | None = f"/products/{product_code}/electricity-tariffs/{tariff}/standard-unit-rates/?period_from={start.astimezone(timezone.utc).isoformat()}&period_to={end.astimezone(timezone.utc).isoformat()}&page_size=1500"
        out: list[RateInterval] = []
        while url:
            r = await self._http.get(url)
            r.raise_for_status()
            doc = r.json()
            for row in doc.get("results", []):
                if row.get("valid_from") is None:
                    continue
                vf = datetime.fromisoformat(row["valid_from"].replace("Z", "+00:00"))
                vt = datetime.fromisoformat(row["valid_to"].replace("Z", "+00:00")) if row.get("valid_to") else end
                out.append(RateInterval(start=vf, end=vt, rate_p_per_kwh=float(row["value_inc_vat"]), tariff_code=tariff, provenance=Provenance.OCTOPUS_API))
            url = doc.get("next")
            if url and url.startswith(BASE):
                url = url[len(BASE):]
        return out

    async def standing_charge(self, product_code: str, region: str) -> float | None:
        tariff = f"E-1R-{product_code}-{region}"
        r = await self._http.get(f"/products/{product_code}/electricity-tariffs/{tariff}/standing-charges/?page_size=1")
        if r.status_code != 200:
            return None
        rows = r.json().get("results", [])
        return float(rows[0]["value_inc_vat"]) if rows else None


def expand_to_half_hours(rates: list[RateInterval], start: datetime, end: datetime) -> list[RateInterval]:
    """Turn arbitrary-length rate intervals into half-hour rows between start and end."""
    out: list[RateInterval] = []
    for r in rates:
        t = max(r.start, start)
        while t < min(r.end, end):
            t2 = t + timedelta(minutes=30)
            out.append(RateInterval(start=t, end=t2, rate_p_per_kwh=r.rate_p_per_kwh, tariff_code=r.tariff_code, provenance=r.provenance))
            t = t2
    return out
