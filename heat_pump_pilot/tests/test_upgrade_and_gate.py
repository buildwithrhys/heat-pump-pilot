"""The upgrade must keep the operating mode; the gate lives in the Settings tab."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from heat_pump_pilot.api import create_app
from heat_pump_pilot.config import DataMode, Settings
from heat_pump_pilot.service import build_platform
from heat_pump_pilot.service.settings_store import SettingsStore


def _options(tmp_path: Path, **kw) -> Path:
    f = tmp_path / "options.json"
    f.write_text(json.dumps({"data_mode": "home_assistant", "poll_seconds": 300, "log_level": "info", **kw}))
    return f


def test_upgrade_from_shadow_in_supervisor_options_stays_shadow(tmp_path: Path):
    opts = _options(tmp_path, operating_mode="shadow", gas_price_p_per_kwh=6.24, boiler_efficiency=0.85)
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'u.sqlite'}", sim_days=2, data_dir=tmp_path)
    p = build_platform(settings, options_file=opts)
    assert p.settings.operating_mode.value == "shadow"
    app = create_app(settings, platform=p, poll=False)
    with TestClient(app) as c:
        assert c.get("/api/health").json()["operating_mode"] == "shadow"
    # a restart (no options file this time) keeps it
    p2 = build_platform(Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'u.sqlite'}", sim_days=2, data_dir=tmp_path))
    assert p2.settings.operating_mode.value == "shadow"


def test_every_moved_option_is_carried_across(tmp_path: Path):
    opts = _options(tmp_path, operating_mode="max_savings", gas_price_p_per_kwh=5.5, boiler_efficiency=0.8,
                    control_max_offset_c=1.5, control_enabled=True, control_climate_entity="climate.zone")
    st = SettingsStore.load(tmp_path, opts)
    assert st.values["operating_mode"] == "max_savings" and st.values["control_enabled"] is True
    assert st.values["control_climate_entity"] == "climate.zone" and st.values["control_max_offset_c"] == 1.5
    assert st.values["gas_price_p_per_kwh"] == 5.5 and st.values["boiler_efficiency"] == 0.8


def test_gate_toggle_needs_acknowledgement_when_preconditions_unmet(tmp_path: Path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'g.sqlite'}", sim_days=3, data_dir=tmp_path)
    p = build_platform(settings)
    asyncio.run(p.bootstrap())
    app = create_app(settings, platform=p, poll=False)
    with TestClient(app) as c:
        pre = c.get("/api/control-preconditions").json()
        assert len(pre["checks"]) == 4 and not pre["all_met"]
        assert c.put("/api/settings", json={"operating_mode": "balanced"}).status_code == 400
        r = c.put("/api/settings", json={"control_enabled": True})
        assert r.status_code == 400 and "Preconditions" in r.json()["detail"]
        r = c.put("/api/settings", json={"control_enabled": True, "control_climate_entity": "climate.sim", "acknowledged": True})
        assert r.status_code == 200 and r.json()["gate"]["control_possible"] is True
        assert c.put("/api/settings", json={"operating_mode": "balanced"}).status_code == 200
        assert c.get("/api/health").json()["operating_mode"] == "balanced"
        assert c.get("/api/control-status").json()["configured"] is True
        # off needs nothing and releases
        r = c.put("/api/settings", json={"control_enabled": False})
        assert r.status_code == 200 and r.json()["gate"]["control_possible"] is False
        assert c.get("/api/control-status").json()["configured"] is False
        assert c.get("/api/settings").json()["values"]["operating_mode"] == "balanced"  # mode kept, but it cannot act
        log = c.get("/api/system").json()["record_counts"]["control_actions"]
        assert log >= 2  # two gate changes recorded
    assert not p.controller.holding
