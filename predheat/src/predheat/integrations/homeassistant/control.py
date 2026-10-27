"""The one place PredHeat writes to Home Assistant.

Kept separate from the read-only client on purpose: nothing else imports this
module, and it offers three capabilities: setting a target temperature on the
climate entity or the water_heater entity the user named (through ``Controller``,
under its guards), and setting one of the three Vaillant heating-curve number
entities when the user confirms a change on the heating curve check (through
``Platform.apply_curve_setting``, bounded and logged, with undo), and publishing
PredHeat's own ``sensor.predheat_*`` entities (its heating plan, for Predbat).
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


    async def current_state(self, entity_id: str) -> float | None:
        r = await self.client._http.get(f"/api/states/{entity_id}")
        r.raise_for_status()
        try:
            return float(r.json().get("state"))
        except (TypeError, ValueError):
            return None

    async def set_number(self, entity_id: str, value: float) -> None:
        """Set a number entity (heating curve, minimum flow, outdoor cut-off). Only ever called
        from an explicit, confirmed request on the heating curve check."""
        if not entity_id.startswith("number."):
            raise ValueError("only number entities can be set this way")
        r = await self.client._http.post("/api/services/number/set_value", json={"entity_id": entity_id, "value": value})
        r.raise_for_status()


    async def publish_state(self, entity_id: str, state: str, attributes: dict) -> None:
        """Create or refresh PredHeat's own sensor in Home Assistant (its plan, for Predbat to read).
        Only entities named ``sensor.predheat_*`` can be written this way; nothing else is touched."""
        if not entity_id.startswith("sensor.predheat_"):
            raise ValueError("only PredHeat's own sensors can be published")
        r = await self.client._http.post(f"/api/states/{entity_id}", json={"state": state, "attributes": attributes})
        r.raise_for_status()


class RecordingControlAdapter:
    """Test and simulation stand-in: remembers targets, writes nothing anywhere."""

    name = "recording"

    def __init__(self, initial: float = 21.0):
        self.targets: dict[str, float] = {}
        self.initial = initial
        self.calls: list[tuple[str, float]] = []
        self.published: dict[str, dict] = {}
        self.fail = False

    async def current_target(self, entity_id: str) -> tuple[float | None, dict]:
        return self.targets.get(entity_id, self.initial), {"state": "heat"}

    async def set_target(self, entity_id: str, temperature: float) -> None:
        if self.fail:
            raise RuntimeError("simulated write failure")
        self.targets[entity_id] = round(temperature, 1)
        self.calls.append((entity_id, round(temperature, 1)))

    async def current_state(self, entity_id: str) -> float | None:
        return self.targets.get(entity_id)

    async def set_number(self, entity_id: str, value: float) -> None:
        if not entity_id.startswith("number."):
            raise ValueError("only number entities can be set this way")
        if self.fail:
            raise RuntimeError("simulated write failure")
        self.targets[entity_id] = value
        self.calls.append((entity_id, value))

    async def publish_state(self, entity_id: str, state: str, attributes: dict) -> None:
        if not entity_id.startswith("sensor.predheat_"):
            raise ValueError("only PredHeat's own sensors can be published")
        self.published[entity_id] = {"state": state, "attributes": attributes}
