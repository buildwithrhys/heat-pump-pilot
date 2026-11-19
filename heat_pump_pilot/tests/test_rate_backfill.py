"""Past prices come from Octopus's public API, so history fetched on first start can be costed."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from heat_pump_pilot.config import DataMode, Settings
from heat_pump_pilot.db import session_scope
from heat_pump_pilot.domain import Provenance, RateInterval
from heat_pump_pilot.service import build_platform
from heat_pump_pilot.service import repository as repo
from heat_pump_pilot.service.platform import _product_from_tariff
from heat_pump_pilot.db.session import as_utc

CODE = "E-1R-COSY-22-12-08-A"


class FakeOctopus:
    def __init__(self):
        self.asked = None

    async def unit_rates(self, product, region, start, end, tariff_code=None):
        self.asked = (product, region, tariff_code)
        # one long row, as the API returns for a fixed stretch; the platform splits it into half hours
        return [RateInterval(start=start, end=end, rate_p_per_kwh=25.2, tariff_code=tariff_code, provenance=Provenance.OCTOPUS_API)]


def test_product_from_tariff():
    assert _product_from_tariff(CODE) == "COSY-22-12-08"
    assert _product_from_tariff("E-1R-INTELLI-VAR-24-10-29-K") == "INTELLI-VAR-24-10-29"
    assert _product_from_tariff("E-2R-VAR-22-11-01-A") is None  # two-register tariffs are not filled
    assert _product_from_tariff(None) is None


def test_backfill_fills_only_missing_half_hours(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'r.sqlite'}", sim_days=2, sim_seed=3)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    p.octopus_api = FakeOctopus()
    p.last_poll["tariff_code"] = CODE
    now = p.clock.now().replace(minute=0, second=0, microsecond=0)
    seen = now - timedelta(hours=5)
    with session_scope(settings.database_url) as s:  # a rate Home Assistant already gave us
        repo.upsert_rates(s, [RateInterval(start=seen, end=seen + timedelta(minutes=30), rate_p_per_kwh=12.6, tariff_code=CODE,
                                           provenance=Provenance.OCTOPUS_HA)], now)
    n = asyncio.run(p.backfill_rates(2))
    assert p.octopus_api.asked == ("COSY-22-12-08", "A", CODE)
    assert n == 2 * 48 - 1
    with session_scope(settings.database_url) as s:
        rows = {as_utc(r.start): r.rate_p_per_kwh for r in repo.rates_for(s, CODE, now - timedelta(days=2), now)}
    assert len(rows) == 2 * 48 and rows[seen] == 12.6  # Home Assistant's rate is kept
    assert asyncio.run(p.backfill_rates(2)) == 0  # nothing left to fill
