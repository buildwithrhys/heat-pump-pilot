"""Home Assistant add-on entry point.

The Supervisor starts the container with ``/data/options.json`` (the options the
user set in the add-on UI) and a ``SUPERVISOR_TOKEN`` that grants access to the
Home Assistant API through the Supervisor proxy. This module translates both
into the ``PREDHEAT_*`` environment variables the application already reads,
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
    "poll_seconds": "PREDHEAT_HA_POLL_SECONDS",
    "log_level": "PREDHEAT_LOG_LEVEL",
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
        "PREDHEAT_DATA_MODE": "home_assistant",  # the only supported source; PREDHEAT_DEV_SIMULATED=1 overrides for development
        "PREDHEAT_DATA_DIR": str(data_dir),
        "PREDHEAT_DATABASE_URL": f"sqlite:///{data_dir / 'predheat.sqlite'}",
        "PREDHEAT_API_HOST": "0.0.0.0",
        "PREDHEAT_API_PORT": "8750",
        "PREDHEAT_FRONTEND_DIR": str(FRONTEND_DIR),
        "PREDHEAT_LOG_LEVEL": "INFO",
    }
    if supervisor_token:
        env["PREDHEAT_HA_URL"] = "http://supervisor/core"
        env["PREDHEAT_HA_WS_URL"] = "ws://supervisor/core/websocket"
        env["PREDHEAT_HA_TOKEN"] = supervisor_token
    for key, value in (existing or {}).items():
        if key.startswith("PREDHEAT_") and key != "PREDHEAT_DATA_MODE":
            env[key] = value
    if (existing or {}).get("PREDHEAT_DEV_SIMULATED") == "1":
        env["PREDHEAT_DATA_MODE"] = "simulated"
    for key, var in OPTION_ENV.items():
        value = options.get(key)
        if value is None or value == "":
            continue
        env[var] = str(value).lower() if isinstance(value, bool) else str(value)
    env["PREDHEAT_LOG_LEVEL"] = env["PREDHEAT_LOG_LEVEL"].upper()
    env.pop("PREDHEAT_CONTROL_ENABLED", None)  # the control gate lives in settings.json, never in the environment
    env.pop("PREDHEAT_CONTROL_CLIMATE_ENTITY", None)
    return env


def load_options(path: Path = OPTIONS_FILE) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def main() -> None:
    options = load_options()
    data_dir = Path(os.environ.get("PREDHEAT_DATA_DIR") or DEFAULT_DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    env = env_from_options(options, os.environ.get("SUPERVISOR_TOKEN"), data_dir, dict(os.environ))
    os.environ.update(env)

    level = env["PREDHEAT_LOG_LEVEL"]
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("predheat.addon")
    log.info(
        "Heat Pump Pilot add-on starting: data_mode=%s operating_mode=%s supervisor=%s data_dir=%s",
        env.get("PREDHEAT_DATA_MODE", "simulated"),
        env.get("PREDHEAT_OPERATING_MODE", "monitor"),
        "yes" if "SUPERVISOR_TOKEN" in os.environ else "no",
        data_dir,
    )
    if env["PREDHEAT_DATA_MODE"] == "home_assistant" and not env.get("PREDHEAT_HA_TOKEN"):
        log.error("data_mode is home_assistant but no Home Assistant token is available; "
                  "run as a Supervisor add-on or set PREDHEAT_HA_URL and PREDHEAT_HA_TOKEN")
        raise SystemExit(1)

    import uvicorn

    uvicorn.run(
        "predheat.api.app:app",
        host=env["PREDHEAT_API_HOST"],
        port=int(env["PREDHEAT_API_PORT"]),
        log_level=level.lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
