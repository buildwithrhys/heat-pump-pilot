"""Runtime settings: persistence, validation, control gate, migration, API."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from predheat.api import create_app
from predheat.config import DataMode, OperatingMode, Settings
from predheat.service import build_platform
from predheat.service.settings_store import SettingsError, SettingsStore


def test_defaults_and_persistence(tmp_path: Path):
    st = SettingsStore.load(tmp_path)
    assert st.values["gas_price_p_per_kwh"] == 6.24 and st.values["operating_mode"] == "monitor"
    s = Settings(data_mode=DataMode.SIMULATED)
    st.update({"gas_price_p_per_kwh": 7.5, "boiler_efficiency": 0.9}, s)
    assert s.gas_price_p_per_kwh == 7.5 and s.boiler_efficiency == 0.9
    again = SettingsStore.load(tmp_path)
    assert again.values["gas_price_p_per_kwh"] == 7.5


def test_validation_ranges(tmp_path: Path):
    st = SettingsStore.load(tmp_path)
    s = Settings(data_mode=DataMode.SIMULATED)
    for bad in ({"boiler_efficiency": 1.2}, {"control_max_offset_c": 2.5}, {"operating_mode": "turbo"}, {"nonsense": 1}):
        with pytest.raises(SettingsError):
            st.update(bad, s)


def test_controlling_mode_needs_the_supervisor_gate(tmp_path: Path):
    st = SettingsStore.load(tmp_path)
    s = Settings(data_mode=DataMode.SIMULATED, control_enabled=False)
    with pytest.raises(SettingsError):
        st.update({"operating_mode": "balanced"}, s)
    s2 = Settings(data_mode=DataMode.SIMULATED, control_enabled=True, control_climate_entity="climate.x")
    st.update({"operating_mode": "balanced"}, s2)
    assert s2.operating_mode == OperatingMode.BALANCED
    assert st.reset("operating_mode", s2)["operating_mode"] == "monitor"


def test_migration_from_options_once(tmp_path: Path):
    opts = tmp_path / "options.json"
    opts.write_text(json.dumps({"operating_mode": "shadow", "gas_price_p_per_kwh": 5.5, "boiler_efficiency": 0.8,
                                "control_max_offset_c": 1.5, "data_mode": "home_assistant"}))
    st = SettingsStore.load(tmp_path, opts)
    assert st.values == {"operating_mode": "shadow", "gas_price_p_per_kwh": 5.5, "boiler_efficiency": 0.8, "control_max_offset_c": 1.5}
    assert st.migrated_from_options
    s = Settings(data_mode=DataMode.SIMULATED)
    st.update({"gas_price_p_per_kwh": 9.0}, s)
    again = SettingsStore.load(tmp_path, opts)
    assert again.values["gas_price_p_per_kwh"] == 9.0  # not re-migrated


def test_api_settings_round_trip_and_gate(tmp_path: Path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'a.sqlite'}", sim_days=2, data_dir=tmp_path)
    p = build_platform(settings)
    asyncio.run(p.bootstrap(0))
    app = create_app(settings, platform=p, poll=False)
    with TestClient(app) as c:
        d = c.get("/api/settings").json()
        assert d["values"]["operating_mode"] == "monitor" and d["gate"]["control_possible"] is False
        assert d["fields"]["boiler_efficiency"]["min"] == 0.5
        r = c.put("/api/settings", json={"gas_price_p_per_kwh": 7.0, "operating_mode": "shadow"})
        assert r.status_code == 200 and r.json()["effective"]["operating_mode"] == "shadow"
        assert c.get("/api/health").json()["operating_mode"] == "shadow"
        assert c.put("/api/settings", json={"operating_mode": "balanced"}).status_code == 400
        assert c.put("/api/settings", json={"boiler_efficiency": 3}).status_code == 400
        r = c.post("/api/settings/reset?key=gas_price_p_per_kwh")
        assert r.json()["values"]["gas_price_p_per_kwh"] == 6.24
        assert c.get("/api/overview").json()["today"]["gas_equivalent"]["price_p_per_kwh"] == 6.24


def test_addon_env_no_longer_carries_runtime_options():
    from predheat.addon import env_from_options

    env = env_from_options({"operating_mode": "shadow", "gas_price_p_per_kwh": 5.5, "control_enabled": True, "control_climate_entity": "climate.x"}, "tok")
    assert "PREDHEAT_OPERATING_MODE" not in env and "PREDHEAT_GAS_PRICE_P_PER_KWH" not in env
    assert env["PREDHEAT_CONTROL_ENABLED"] == "true"
