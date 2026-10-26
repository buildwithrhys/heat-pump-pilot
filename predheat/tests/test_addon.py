"""Add-on entry point: options -> environment mapping, and dashboard serving."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from predheat.addon import env_from_options, load_options
from predheat.api import create_app
from predheat.config import DataMode, Settings


def test_supervisor_env_is_derived_from_token():
    env = env_from_options({}, "tok", Path("/data"))
    assert env["PREDHEAT_HA_URL"] == "http://supervisor/core"
    assert env["PREDHEAT_HA_WS_URL"] == "ws://supervisor/core/websocket"
    assert env["PREDHEAT_HA_TOKEN"] == "tok"
    assert env["PREDHEAT_DATABASE_URL"] == "sqlite:////data/predheat.sqlite"
    assert env["PREDHEAT_API_HOST"] == "0.0.0.0"


def test_options_map_to_env_and_override_existing():
    options = {"data_mode": "home_assistant", "poll_seconds": 120, "log_level": "debug",
               "latitude": 51.5, "control_climate_entity": ""}
    env = env_from_options(options, "tok", Path("/data"), existing={"PREDHEAT_HA_POLL_SECONDS": "900", "HOME": "/root"})
    assert env["PREDHEAT_DATA_MODE"] == "home_assistant"
    assert env["PREDHEAT_HA_POLL_SECONDS"] == "120"  # option beats existing env
    assert env["PREDHEAT_LOG_LEVEL"] == "DEBUG"
    assert "PREDHEAT_LATITUDE" not in env  # location comes from Home Assistant now
    assert "PREDHEAT_CONTROL_CLIMATE_ENTITY" not in env  # empty options are not exported
    assert "HOME" not in env


def test_control_is_off_unless_explicitly_configured():
    env = env_from_options({}, None, Path("/data"), existing={"PREDHEAT_CONTROL_ENABLED": "true"})
    assert env["PREDHEAT_CONTROL_ENABLED"] == "false"  # environment alone cannot switch it on
    assert "PREDHEAT_HA_TOKEN" not in env  # no supervisor, nothing invented


def test_load_options_missing_file(tmp_path: Path):
    assert load_options(tmp_path / "nope.json") == {}
    (tmp_path / "o.json").write_text('{"data_mode": "simulated"}')
    assert load_options(tmp_path / "o.json") == {"data_mode": "simulated"}


def test_dashboard_served_with_spa_fallback(tmp_path: Path):
    fe = tmp_path / "static"
    (fe / "assets").mkdir(parents=True)
    (fe / "index.html").write_text("<html>dash</html>")
    (fe / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
                        sim_days=2, frontend_dir=fe)
    app = create_app(settings, poll=False)
    with TestClient(app) as c:
        assert c.get("/").text == "<html>dash</html>"
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/anything/deep").text == "<html>dash</html>"  # SPA fallback
        assert c.get("/../secret.txt").status_code in (200, 404) and "nope" not in c.get("/../secret.txt").text
        assert c.get("/api/health").json()["status"] == "ok"  # API still wins
        assert c.get("/api/does-not-exist").status_code == 404


def test_no_dashboard_without_frontend_dir(tmp_path: Path):
    settings = Settings(data_mode=DataMode.SIMULATED, database_url=f"sqlite:///{tmp_path / 'db.sqlite'}", sim_days=2)
    app = create_app(settings, poll=False)
    with TestClient(app) as c:
        assert c.get("/").status_code == 404
        assert c.get("/api/health").status_code == 200
