"""Runtime configuration.

All secrets come from environment variables (or a local ``.env`` file that is
never committed). Nothing here is ever logged in full.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DataMode(StrEnum):
    """Where telemetry comes from."""

    SIMULATED = "simulated"  # synthetic home, no external access needed
    HOME_ASSISTANT = "home_assistant"  # live telemetry via Home Assistant APIs


class OperatingMode(StrEnum):
    """Optimiser authority. Only MONITOR and SHADOW exist until Phase 5."""

    MONITOR = "monitor"
    SHADOW = "shadow"
    BALANCED = "balanced"
    MAX_SAVINGS = "max_savings"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PREDHEAT_", env_file=".env", extra="ignore")

    data_mode: DataMode = DataMode.SIMULATED
    operating_mode: OperatingMode = OperatingMode.MONITOR
    # Hard guard: active control is impossible unless this is explicitly set to true
    # AND the operating mode is balanced/max_savings AND a climate entity is named
    # AND the controller's runtime checks pass on every cycle. See SAFETY.md.
    control_enabled: bool = False
    control_climate_entity: str | None = None  # e.g. climate.house_zone_1_circuit_0_climate
    control_max_offset_c: float = 1.0  # how far the setpoint may be moved either way
    control_override_hours: float = 4.0  # back off this long after a manual change
    control_dhw_entity: str | None = None  # water_heater.* entity for hot water scheduling
    control_dhw_boost_c: float = 5.0  # how far the cylinder target is raised during a planned reheat
    trial_mode: bool = False  # alternate baseline and optimised days to measure the saving

    database_url: str = "sqlite:///./data/predheat.sqlite"
    timezone: str = "Europe/London"
    latitude: float | None = None
    longitude: float | None = None

    # Home Assistant
    ha_url: str = "http://homeassistant.local:8123"
    # Websocket endpoint. Defaults to <ha_url>/api/websocket; the add-on sets it to the
    # Supervisor proxy (ws://supervisor/core/websocket) which lives on a different path.
    ha_ws_url: str | None = None
    ha_token: SecretStr | None = None
    ha_poll_seconds: int = 300
    backfill_days: int = 60  # how far back to pull long-term statistics on first start

    # Direct provider credentials (optional; the HA integrations are the PoC source)
    octopus_api_key: SecretStr | None = None
    octopus_account: str | None = None
    openweather_api_key: SecretStr | None = None
    solcast_api_key: SecretStr | None = None

    # House and heat pump facts the user may enter (Settings tab)
    design_outdoor_c: float | None = None
    survey_heat_loss_kw: float | None = None
    survey_outdoor_c: float | None = None
    heat_pump_rated_kw: float | None = None

    # Gas comparison (modelled counterfactual, labelled as such)
    gas_price_p_per_kwh: float = 6.24
    boiler_efficiency: float = 0.85

    # Simulation
    sim_seed: int = 42
    sim_days: int = 14
    sim_slot_minutes: int = 30

    api_host: str = "127.0.0.1"
    api_port: int = 8750
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    log_level: str = "INFO"

    # Where the database and discovery reports live. The add-on points this at /data,
    # the Supervisor's persistent storage for the add-on.
    data_dir: Path = Path("./data")
    # Built dashboard (Vite output). When set and present, the API serves it at "/".
    frontend_dir: Path | None = None


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
