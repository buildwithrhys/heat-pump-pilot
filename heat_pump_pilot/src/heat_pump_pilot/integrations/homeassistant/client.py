"""Thin Home Assistant client (REST + WebSocket).

Read-only by design for Phases 0 to 4: there is deliberately no
``call_service`` method that writes to equipment. The only service call
offered is ``weather.get_forecasts`` which returns data and changes nothing.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from ...config import get_settings


@dataclass
class HAState:
    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_changed: datetime | None = None
    last_updated: datetime | None = None
    last_reported: datetime | None = None  # moves on every write, even an unchanged one (Home Assistant 2024.3 and later)
    platform: str | None = None  # from entity registry when available
    disabled: bool = False

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def unit(self) -> str | None:
        return self.attributes.get("unit_of_measurement")

    @property
    def device_class(self) -> str | None:
        return self.attributes.get("device_class")

    @property
    def is_live(self) -> bool:
        return self.state not in ("unavailable", "unknown", "", None)

    def as_float(self) -> float | None:
        try:
            return float(self.state)
        except (TypeError, ValueError):
            return None


class HAClient:
    def __init__(self, url: str | None = None, token: str | None = None, timeout: float = 20.0, ws_url: str | None = None):
        settings = get_settings()
        self.url = (url or settings.ha_url).rstrip("/")
        self.ws_url = ws_url or settings.ha_ws_url or (
            self.url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
        )
        self.token = token or (settings.ha_token.get_secret_value() if settings.ha_token else None)
        if not self.token:
            raise ValueError("Home Assistant token not configured (HEAT_PUMP_PILOT_HA_TOKEN)")
        self._http = httpx.AsyncClient(
            base_url=self.url,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------------- REST
    async def ping(self) -> dict[str, Any]:
        r = await self._http.get("/api/")
        r.raise_for_status()
        return r.json()

    async def config(self) -> dict[str, Any]:
        r = await self._http.get("/api/config")
        r.raise_for_status()
        return r.json()

    async def states(self) -> list[HAState]:
        r = await self._http.get("/api/states")
        r.raise_for_status()
        return [_parse_state(s) for s in r.json()]

    async def history(self, entity_ids: list[str], start: datetime, end: datetime) -> dict[str, list[HAState]]:
        params = {
            "filter_entity_id": ",".join(entity_ids),
            "end_time": end.isoformat(),
            "minimal_response": "false",
            "no_attributes": "true",
        }
        r = await self._http.get(f"/api/history/period/{start.isoformat()}", params=params)
        r.raise_for_status()
        out: dict[str, list[HAState]] = {}
        for series in r.json():
            for item in series:
                out.setdefault(item["entity_id"], []).append(_parse_state(item))
        return out

    # ---------------- WebSocket
    async def ws_call(self, message: dict[str, Any]) -> Any:
        """Send one authenticated websocket command and return its result."""
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("websockets package required") from exc

        async with websockets.connect(self.ws_url, max_size=50 * 1024 * 1024) as ws:
            hello = json.loads(await ws.recv())
            if hello.get("type") != "auth_required":
                raise RuntimeError(f"unexpected HA hello: {hello}")
            await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
            auth = json.loads(await ws.recv())
            if auth.get("type") != "auth_ok":
                raise RuntimeError("Home Assistant websocket authentication failed")
            await ws.send(json.dumps({"id": 1, **message}))
            while True:
                reply = json.loads(await ws.recv())
                if reply.get("id") == 1:
                    if not reply.get("success", False):
                        raise RuntimeError(f"HA ws error: {reply.get('error')}")
                    return reply.get("result")

    async def entity_registry(self) -> list[dict[str, Any]]:
        return await self.ws_call({"type": "config/entity_registry/list"})

    async def device_registry(self) -> list[dict[str, Any]]:
        return await self.ws_call({"type": "config/device_registry/list"})

    async def config_entries(self) -> list[dict[str, Any]]:
        return await self.ws_call({"type": "config_entries/get"})

    async def statistics(
        self,
        statistic_ids: list[str],
        start: datetime,
        end: datetime | None = None,
        period: str = "hour",
        types: tuple[str, ...] = ("sum", "mean", "change", "state"),
    ) -> dict[str, list[dict[str, Any]]]:
        msg: dict[str, Any] = {
            "type": "recorder/statistics_during_period",
            "start_time": start.isoformat(),
            "statistic_ids": statistic_ids,
            "period": period,
            "types": list(types),
        }
        if end:
            msg["end_time"] = end.isoformat()
        return await self.ws_call(msg)

    async def weather_forecast(self, entity_id: str, kind: str = "hourly") -> list[dict[str, Any]]:
        """weather.get_forecasts is a read-only service that returns a response."""
        result = await self.ws_call(
            {
                "type": "call_service",
                "domain": "weather",
                "service": "get_forecasts",
                "service_data": {"type": kind},
                "target": {"entity_id": entity_id},
                "return_response": True,
            }
        )
        return result["response"][entity_id]["forecast"]

    async def states_with_registry(self) -> list[HAState]:
        """States enriched with the integration platform from the entity registry."""
        states, registry = await asyncio.gather(self.states(), self.entity_registry())
        platforms = {e["entity_id"]: (e.get("platform"), e.get("disabled_by")) for e in registry}
        for s in states:
            plat, disabled = platforms.get(s.entity_id, (None, None))
            s.platform = plat
            s.disabled = disabled is not None
        return states


def _parse_state(raw: dict[str, Any]) -> HAState:
    def _dt(v: str | None) -> datetime | None:
        return datetime.fromisoformat(v) if v else None

    return HAState(
        entity_id=raw["entity_id"],
        state=raw.get("state", ""),
        attributes=raw.get("attributes", {}) or {},
        last_changed=_dt(raw.get("last_changed")),
        last_updated=_dt(raw.get("last_updated")),
        last_reported=_dt(raw.get("last_reported")),
    )
