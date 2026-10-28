"""Home Assistant add-on entry point.

The Supervisor starts the container with ``/data/options.json`` (the options the
user set in the add-on UI) and a ``SUPERVISOR_TOKEN`` that grants access to the
Home Assistant API through the Supervisor proxy. This module translates both
into the ``HEAT_PUMP_PILOT_*`` environment variables the application already reads,
then starts the API server.

Outside the Supervisor (plain Docker) there is no options file and no token, so
the environment is used as-is and only the defaults below are filled in.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

OPTIONS_FILE = Path("/data/options.json")
DEFAULT_DATA_DIR = Path("/data")
FRONTEND_DIR = Path("/app/static")

# add-on option -> environment variable
# Install-time and safety options only. Runtime preferences (operating mode, gas
# comparison, setpoint offset) live in settings.json and the dashboard's Settings
# tab; values left in options.json by older releases are migrated once at start.
OPTION_ENV = {
    "poll_seconds": "HEAT_PUMP_PILOT_HA_POLL_SECONDS",
    "log_level": "HEAT_PUMP_PILOT_LOG_LEVEL",
}


def env_from_options(
    options: dict[str, Any],
    supervisor_token: str | None,
    data_dir: Path = DEFAULT_DATA_DIR,
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the environment for the server.

    Precedence: add-on options > environment already present > built-in defaults.
    The control gate (allow control, climate entity, operating mode) is held in
    settings.json and changed only from the dashboard; the controller re-checks
    its guards on every cycle (see SAFETY.md).
    """
    env: dict[str, str] = {
        "HEAT_PUMP_PILOT_DATA_MODE": "home_assistant",  # the only supported source; HEAT_PUMP_PILOT_DEV_SIMULATED=1 overrides for development
        "HEAT_PUMP_PILOT_DATA_DIR": str(data_dir),
        "HEAT_PUMP_PILOT_DATABASE_URL": f"sqlite:///{data_dir / 'heat_pump_pilot.sqlite'}",
        "HEAT_PUMP_PILOT_API_HOST": "0.0.0.0",
        "HEAT_PUMP_PILOT_API_PORT": "8750",
        "HEAT_PUMP_PILOT_FRONTEND_DIR": str(FRONTEND_DIR),
        "HEAT_PUMP_PILOT_LOG_LEVEL": "INFO",
    }
    if supervisor_token:
        env["HEAT_PUMP_PILOT_HA_URL"] = "http://supervisor/core"
        env["HEAT_PUMP_PILOT_HA_WS_URL"] = "ws://supervisor/core/websocket"
        env["HEAT_PUMP_PILOT_HA_TOKEN"] = supervisor_token
    for key, value in (existing or {}).items():
        if key.startswith("HEAT_PUMP_PILOT_") and key != "HEAT_PUMP_PILOT_DATA_MODE":
            env[key] = value
    if (existing or {}).get("HEAT_PUMP_PILOT_DEV_SIMULATED") == "1":
        env["HEAT_PUMP_PILOT_DATA_MODE"] = "simulated"
    for key, var in OPTION_ENV.items():
        value = options.get(key)
        if value is None or value == "":
            continue
        env[var] = str(value).lower() if isinstance(value, bool) else str(value)
    env["HEAT_PUMP_PILOT_LOG_LEVEL"] = env["HEAT_PUMP_PILOT_LOG_LEVEL"].upper()
    env.pop("HEAT_PUMP_PILOT_CONTROL_ENABLED", None)  # the control gate lives in settings.json, never in the environment
    env.pop("HEAT_PUMP_PILOT_CONTROL_CLIMATE_ENTITY", None)
    return env


def load_options(path: Path = OPTIONS_FILE) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def main() -> None:
    options = load_options()
    data_dir = Path(os.environ.get("HEAT_PUMP_PILOT_DATA_DIR") or DEFAULT_DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    env = env_from_options(options, os.environ.get("SUPERVISOR_TOKEN"), data_dir, dict(os.environ))
    os.environ.update(env)

    level = env["HEAT_PUMP_PILOT_LOG_LEVEL"]
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("heat_pump_pilot.addon")
    log.info(
        "Heat Pump Pilot add-on starting: data_mode=%s operating_mode=%s supervisor=%s data_dir=%s",
        env.get("HEAT_PUMP_PILOT_DATA_MODE", "simulated"),
        env.get("HEAT_PUMP_PILOT_OPERATING_MODE", "monitor"),
        "yes" if "SUPERVISOR_TOKEN" in os.environ else "no",
        data_dir,
    )
    if env["HEAT_PUMP_PILOT_DATA_MODE"] == "home_assistant" and not env.get("HEAT_PUMP_PILOT_HA_TOKEN"):
        log.error("data_mode is home_assistant but no Home Assistant token is available; "
                  "run as a Supervisor add-on or set HEAT_PUMP_PILOT_HA_URL and HEAT_PUMP_PILOT_HA_TOKEN")
        raise SystemExit(1)

    import uvicorn

    uvicorn.run(
        "heat_pump_pilot.api.app:app",
        host=env["HEAT_PUMP_PILOT_API_HOST"],
        port=int(env["HEAT_PUMP_PILOT_API_PORT"]),
        log_level=level.lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
