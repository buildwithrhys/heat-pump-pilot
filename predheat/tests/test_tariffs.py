"""Tariff replay."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from predheat.config import DataMode, Settings
from predheat.db import session_scope
from predheat.pricing import DEFAULT_TEMPLATES, load_templates, replay
from predheat.service import build_platform

UTC = timezone.utc


def test_window_crossing_midnight_and_replay():
    go = next(t for t in DEFAULT_TEMPLATES if t.code == "INTELLI-VAR")
    assert go.rate_at(datetime(2026, 1, 10, 0, 0, tzinfo=UTC)) == 7.0  # 00:00 UK winter time
    assert go.rate_at(datetime(2026, 1, 10, 5, 29, tzinfo=UTC)) == 7.0
    assert go.rate_at(datetime(2026, 1, 10, 5, 30, tzinfo=UTC)) == 27.7
    assert go.rate_at(datetime(2026, 7, 10, 22, 45, tzinfo=UTC)) == 7.0  # 23:45 BST
    ints = [(datetime(2026, 1, 10, h, 0, tzinfo=UTC), datetime(2026, 1, 10, h, 30, tzinfo=UTC), 1.0) for h in range(24)]
    r = replay(go, ints)
    assert r["kwh"] == 24.0 and r["cheap_share"] == 0.25  # 00:00 to 05:00 slots are cheap, 23:00 is not
    assert r["unit_pence"] == round(6 * 7.0 + 18 * 27.7, 1)


def test_yaml_override(tmp_path):
    (tmp_path / "tariffs.yaml").write_text("tariffs:\n  - code: MINE\n    name: My tariff\n    default_rate_p: 20\n    standing_p_per_day: 40\n")
    codes = {t.code for t in load_templates(tmp_path)}
    assert "MINE" in codes and "COSY" in codes


def test_platform_comparison_ranks_tariffs(tmp_path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 't.sqlite'}", sim_days=8, sim_seed=2, data_dir=tmp_path)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    with session_scope(settings.database_url) as s:
        cmp_ = p.compare_tariffs(s, days=7)
    assert cmp_["status"] == "ok", cmp_
    assert cmp_["actual"]["total_pence"] > 0
    replayable = [r for r in cmp_["results"] if r.get("replayable")]
    assert len(replayable) >= 4
    assert replayable == sorted(replayable, key=lambda r: r["total_pence"])
    assert any(not r.get("replayable") for r in cmp_["results"])  # Agile listed as not replayable
