"""Controlled live optimisation.

Applies the optimiser's plan to the home's climate entity as small setpoint
offsets, and only when every guard passes on every cycle:

1. ``control_enabled`` is true, the operating mode is balanced or max_savings,
   and a climate entity is configured.
2. The plan is model-based (the building model is identified with at least
   50 percent confidence) and the indoor reading is under 30 minutes old.
3. The occupant has not changed the setpoint themselves in the last
   ``control_override_hours``.
4. The house is not already below the comfort band.

If any guard fails while Heat Pump Pilot holds the setpoint, it hands the original
setpoint back and logs why. Every proposal, write, failure and release is
recorded in ``control_execution_log``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..config import OperatingMode, Settings
from ..db import models as m
from ..db.session import as_utc

log = logging.getLogger("heat_pump_pilot.control")

MODE_OFFSET = {"boost": +1.0, "coast": -1.0, "hold": 0.0, "off": 0.0}


@dataclass
class ControlDecision:
    acted: bool
    status: str  # idle | holding | released | blocked | failed
    reason: str
    target_c: float | None = None
    previous_c: float | None = None
    mode: str | None = None


@dataclass
class Controller:
    settings: Settings
    adapter: Any
    property_id: int
    baseline_setpoint_c: float | None = None  # the occupant's own setpoint, captured when we take over
    last_commanded_c: float | None = None
    holding: bool = False
    override_until: datetime | None = None
    last_change_at: datetime | None = None
    history: list[ControlDecision] = field(default_factory=list)
    # hot water
    dhw_baseline_c: float | None = None
    dhw_holding: bool = False
    dhw_last_change_at: datetime | None = None

    @staticmethod
    def trial_day_is_baseline(now: datetime) -> bool:
        """In trial mode, even days (by ordinal, local date) run the ordinary thermostat."""
        from ..timeutils import LONDON

        return now.astimezone(LONDON).date().toordinal() % 2 == 0

    # ------------------------------------------------------------------ guards
    def configured(self) -> tuple[bool, str]:
        if not self.settings.control_enabled:
            return False, "control is not enabled"
        if self.settings.operating_mode not in (OperatingMode.BALANCED, OperatingMode.MAX_SAVINGS):
            return False, f"operating mode is {self.settings.operating_mode.value}; control needs balanced or max_savings"
        if not self.settings.control_climate_entity:
            return False, "no climate entity configured"
        return True, "configured"

    def offset_for(self, mode: str) -> float:
        scale = 1.0 if self.settings.operating_mode == OperatingMode.BALANCED else 1.5
        off = MODE_OFFSET.get(mode, 0.0) * min(self.settings.control_max_offset_c * scale, 2.0)
        return round(off * 2) / 2  # half-degree steps, what controllers accept

    # ------------------------------------------------------------------ main cycle
    async def step(self, s, now: datetime, plan: dict[str, Any], indoor_c: float | None, indoor_age: timedelta | None,
                   model_confidence: float | None) -> ControlDecision:
        ok, why = self.configured()
        if not ok:
            return await self._release_if_holding(s, now, f"blocked: {why}")
        entity = self.settings.control_climate_entity
        try:
            current, _info = await self.adapter.current_target(entity)
        except Exception as exc:  # noqa: BLE001
            return self._log(s, now, ControlDecision(False, "failed", f"could not read {entity}: {exc}"), entity)
        # manual override: the target is not what we last set
        if self.holding and self.last_commanded_c is not None and current is not None and abs(current - self.last_commanded_c) > 0.05:
            self.override_until = now + timedelta(hours=self.settings.control_override_hours)
            self.holding = False
            self.baseline_setpoint_c = current
            self.last_commanded_c = None
            return self._log(s, now, ControlDecision(False, "released", f"manual change detected (target {current} degC); backing off for {self.settings.control_override_hours:g} h", previous_c=current), entity)
        if self.override_until and now < self.override_until:
            return self._log(s, now, ControlDecision(False, "blocked", f"manual override in force until {self.override_until.isoformat(timespec='minutes')}"), entity, quiet=True)
        if self.settings.trial_mode and self.trial_day_is_baseline(now):
            d = await self._release_if_holding(s, now, "trial: baseline day, ordinary thermostat in charge")
            await self._dhw_release(s, now, "trial: baseline day")
            return d
        if plan.get("status") != "model_based":
            return await self._release_if_holding(s, now, "blocked: plan is not model-based yet")
        if model_confidence is None or model_confidence < 0.5:
            return await self._release_if_holding(s, now, f"blocked: building model confidence {0 if model_confidence is None else model_confidence:.0%} is below 50%")
        if indoor_c is None or indoor_age is None or indoor_age > timedelta(minutes=30):
            return await self._release_if_holding(s, now, "blocked: indoor temperature reading is missing or stale")
        try:
            await self._dhw_step(s, now, plan)
        except Exception as exc:  # noqa: BLE001
            self._log(s, now, ControlDecision(False, "failed", f"hot water: {exc}"), self.settings.control_dhw_entity)
        if not self.holding:
            self.baseline_setpoint_c = current
        base = self.baseline_setpoint_c if self.baseline_setpoint_c is not None else (plan.get("model") or {}).get("setpoint_c", 21.0)
        tol = float((plan.get("model") or {}).get("tolerance_c", 1.0))
        if indoor_c < base - tol - 0.5:
            return await self._release_if_holding(s, now, f"blocked: indoor {indoor_c:.1f} degC is below the comfort band; leaving the thermostat alone")
        mode = self._current_mode(plan, now)
        target = round((base + self.offset_for(mode)) * 2) / 2
        if current is not None and abs(current - target) < 0.05:
            self.holding = self.holding or target != base
            return self._log(s, now, ControlDecision(False, "holding" if self.holding else "idle", f"target already {target} degC ({mode})", target_c=target, mode=mode), entity, quiet=True)
        if self.last_change_at and now - self.last_change_at < timedelta(minutes=25):
            return self._log(s, now, ControlDecision(False, "holding" if self.holding else "idle", "changed less than 25 minutes ago; waiting", target_c=target, mode=mode), entity, quiet=True)
        try:
            await self.adapter.set_target(entity, target)
        except Exception as exc:  # noqa: BLE001
            return self._log(s, now, ControlDecision(False, "failed", f"write to {entity} failed: {exc}", target_c=target, previous_c=current, mode=mode), entity)
        self.last_commanded_c = target
        self.last_change_at = now
        self.holding = target != base
        return self._log(s, now, ControlDecision(True, "holding" if self.holding else "idle", f"set {entity} to {target} degC for '{mode}' (occupant setpoint {base} degC)", target_c=target, previous_c=current, mode=mode), entity)

    async def release(self, s, now: datetime, reason: str = "released") -> ControlDecision:
        await self._dhw_release(s, now, reason)
        return await self._release_if_holding(s, now, reason)

    # ------------------------------------------------------------------ hot water
    def _dhw_action_now(self, plan: dict[str, Any], now: datetime) -> bool:
        for a in plan.get("actions", []):
            if a.get("kind") == "heat_hot_water" and datetime.fromisoformat(a["start"]) <= now < datetime.fromisoformat(a["end"]):
                return True
        return False

    async def _dhw_step(self, s, now: datetime, plan: dict[str, Any]) -> None:
        entity = self.settings.control_dhw_entity
        if not entity:
            return
        wanted = self._dhw_action_now(plan, now)
        current, _ = await self.adapter.current_target(entity)
        if self.dhw_holding and self.dhw_baseline_c is not None and current is not None and self.dhw_last_change_at and \
                abs(current - (self.dhw_baseline_c + self.settings.control_dhw_boost_c)) > 0.05:
            self.dhw_holding = False  # someone changed it; leave it alone
            self.dhw_baseline_c = None
            self._log(s, now, ControlDecision(False, "released", f"hot water target changed by hand to {current} °C; leaving it"), entity)
            return
        if wanted and not self.dhw_holding:
            if current is None:
                return
            if self.dhw_last_change_at and now - self.dhw_last_change_at < timedelta(minutes=25):
                return
            target = round(current + self.settings.control_dhw_boost_c, 1)
            await self.adapter.set_target(entity, target)
            self.dhw_baseline_c, self.dhw_holding, self.dhw_last_change_at = current, True, now
            self._log(s, now, ControlDecision(True, "holding", f"raised hot water target on {entity} to {target} °C for the planned reheat (was {current} °C)", target_c=target, previous_c=current, mode="heat_hot_water"), entity)
        elif not wanted and self.dhw_holding:
            await self._dhw_release(s, now, "planned reheat finished")

    async def _dhw_release(self, s, now: datetime, reason: str) -> None:
        entity = self.settings.control_dhw_entity
        if not (self.dhw_holding and entity and self.dhw_baseline_c is not None):
            return
        try:
            await self.adapter.set_target(entity, self.dhw_baseline_c)
            self._log(s, now, ControlDecision(True, "released", f"{reason}; hot water target restored to {self.dhw_baseline_c} °C", target_c=self.dhw_baseline_c), entity)
        except Exception as exc:  # noqa: BLE001
            self._log(s, now, ControlDecision(False, "failed", f"{reason}; restoring hot water target failed: {exc}"), entity)
        self.dhw_holding, self.dhw_baseline_c, self.dhw_last_change_at = False, None, now

    # ------------------------------------------------------------------ helpers
    async def _release_if_holding(self, s, now: datetime, reason: str) -> ControlDecision:
        entity = self.settings.control_climate_entity
        if not self.holding or self.baseline_setpoint_c is None or not entity:
            return self._log(s, now, ControlDecision(False, "blocked" if reason.startswith("blocked") else "idle", reason), entity, quiet=True)
        try:
            await self.adapter.set_target(entity, self.baseline_setpoint_c)
            d = ControlDecision(True, "released", f"{reason}; restored occupant setpoint {self.baseline_setpoint_c} degC", target_c=self.baseline_setpoint_c, previous_c=self.last_commanded_c)
        except Exception as exc:  # noqa: BLE001
            d = ControlDecision(False, "failed", f"{reason}; restoring {self.baseline_setpoint_c} degC failed: {exc}", target_c=self.baseline_setpoint_c)
        self.holding = False
        self.last_commanded_c = None
        self.last_change_at = now
        return self._log(s, now, d, entity)

    @staticmethod
    def _current_mode(plan: dict[str, Any], now: datetime) -> str:
        for p in plan.get("predicted", []):
            start = datetime.fromisoformat(p["start"])
            if start <= now < start + timedelta(minutes=30):
                return p.get("mode", "hold")
        return "hold"

    def _log(self, s, now: datetime, d: ControlDecision, entity: str | None, quiet: bool = False) -> ControlDecision:
        self.history.append(d)
        self.history = self.history[-50:]
        if not quiet or d.acted:
            s.add(m.ControlExecutionLog(property_id=self.property_id, ts=now, action="climate.set_temperature" if d.target_c is not None else "none",
                                        target=entity, requested_value=None if d.target_c is None else str(d.target_c),
                                        previous_value=None if d.previous_c is None else str(d.previous_c),
                                        status="executed" if d.acted else d.status, reason=d.reason))
            log.info("control: %s (%s)", d.reason, d.status)
        return d

    def status(self) -> dict[str, Any]:
        ok, why = self.configured()
        last = self.history[-1] if self.history else None
        return {"configured": ok, "reason": why, "entity": self.settings.control_climate_entity, "holding": self.holding,
                "occupant_setpoint_c": self.baseline_setpoint_c, "commanded_c": self.last_commanded_c,
                "override_until": self.override_until.isoformat() if self.override_until else None,
                "last": {"status": last.status, "reason": last.reason, "target_c": last.target_c, "mode": last.mode} if last else None,
                "max_offset_c": self.settings.control_max_offset_c, "mode": self.settings.operating_mode.value,
                "hot_water": {"entity": self.settings.control_dhw_entity, "holding": self.dhw_holding, "baseline_c": self.dhw_baseline_c},
                "trial_mode": self.settings.trial_mode}
