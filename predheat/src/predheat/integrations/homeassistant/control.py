"""The one place PredHeat writes to Home Assistant.

Kept separate from the read-only client on purpose: nothing else imports this
module, and it offers exactly one capability, setting a target temperature on
the climate entity or the water_heater entity the user named. Every call goes
through ``Controller`` which logs it.
"""

from __future__ import annotations

from typing import Any

from .client import HAClient


class HAControlAdapter:
    name = "home_assistant_climate"

    def __init__(self, client: HAClient):
        self.client = client

    async def current_target(self, entity_id: str) -> tuple[float | None, dict[str, Any]]:
        r = await self.client._http.get(f"/api/states/{entity_id}")
        r.raise_for_status()
        data = r.json()
        attrs = data.get("attributes", {}) or {}
        t = attrs.get("temperature")
        return (float(t) if t is not None else None), {"state": data.get("state"), "hvac_action": attrs.get("hvac_action"),
                                                       "current_temperature": attrs.get("current_temperature"), "preset_mode": attrs.get("preset_mode")}

    async def set_target(self, entity_id: str, temperature: float) -> None:
        domain = entity_id.split(".", 1)[0]
        service = "water_heater/set_temperature" if domain == "water_heater" else "climate/set_temperature"
        r = await self.client._http.post(f"/api/services/{service}", json={"entity_id": entity_id, "temperature": round(temperature, 1)})
        r.raise_for_status()


class RecordingControlAdapter:
    """Test and simulation stand-in: remembers targets, writes nothing anywhere."""

    name = "recording"

    def __init__(self, initial: float = 21.0):
        self.targets: dict[str, float] = {}
        self.initial = initial
        self.calls: list[tuple[str, float]] = []
        self.fail = False

    async def current_target(self, entity_id: str) -> tuple[float | None, dict]:
        return self.targets.get(entity_id, self.initial), {"state": "heat"}

    async def set_target(self, entity_id: str, temperature: float) -> None:
        if self.fail:
            raise RuntimeError("simulated write failure")
        self.targets[entity_id] = round(temperature, 1)
        self.calls.append((entity_id, round(temperature, 1)))
