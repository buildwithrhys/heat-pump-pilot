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
    # AND the operating mode is balanced/max_savings AND the control adapter reports
    # that control ownership has been verified. See SAFETY.md.
    control_enabled: bool = False

    database_url: str = "sqlite:///./data/predheat.sqlite"
    timezone: str = "Europe/London"
    latitude: float | None = None
    longitude: float | None = None

    # Home Assistant
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: SecretStr | None = None
    ha_poll_seconds: int = 300

    # Direct provider credentials (optional; the HA integrations are the PoC source)
    octopus_api_key: SecretStr | None = None
    octopus_account: str | None = None
    openweather_api_key: SecretStr | None = None
    solcast_api_key: SecretStr | None = None

    # Simulation
    sim_seed: int = 42
    sim_days: int = 14
    sim_slot_minutes: int = 30

    api_host: str = "127.0.0.1"
    api_port: int = 8750
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @property
    def data_dir(self) -> Path:
        return Path("./data")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
