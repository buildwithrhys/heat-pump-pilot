"""FastAPI application.

Read-only endpoints only. There is no endpoint that writes to equipment; the
``/api/control`` namespace is reserved and returns 403 until Phase 5 and
explicit authorisation.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .. import __version__
from ..config import Settings, get_settings
from ..service import Platform, build_platform
from ..service.repository import samples as repo_samples
from ..timeutils import LONDON

log = logging.getLogger("predheat.api")


def create_app(settings: Settings | None = None, platform: Platform | None = None, poll: bool = True) -> FastAPI:
    settings = settings or get_settings()
    state: dict = {"platform": platform}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from pathlib import Path as _P

        p = state["platform"] or build_platform(settings, options_file=_P("/data/options.json"))
        state["platform"] = p
        if p.property_id is None:
            await p.bootstrap()
        stop = asyncio.Event()

        async def poller():
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.ha_poll_seconds)
                except asyncio.TimeoutError:
                    try:
                        if hasattr(p.clock, "advance"):
                            p.clock.advance(timedelta(seconds=settings.ha_poll_seconds))
                        await p.poll_once()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("poll failed: %s", exc)

        task = asyncio.create_task(poller()) if poll else None
        try:
            yield
        finally:
            stop.set()
            if task:
                task.cancel()
            try:
                await p.release_control("add-on stopping")
            except Exception as exc:  # noqa: BLE001
                log.warning("release on shutdown failed: %s", exc)

    app = FastAPI(title="PredHeat", version=__version__, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET"], allow_headers=["*"])

    def plat() -> Platform:
        p = state["platform"]
        if p is None or p.property_id is None:
            raise HTTPException(503, "platform not ready")
        return p

    @app.get("/api/health")
    async def health():
        p = plat()
        return {"status": "ok", "version": __version__, "data_mode": p.settings.data_mode.value,
                "operating_mode": p.settings.operating_mode.value, "control_enabled": p.settings.control_enabled}

    @app.get("/api/overview")
    async def overview():
        return plat().overview()

    @app.get("/api/system")
    async def system():
        p = plat()
        return p.system_health(await p.health())

    @app.get("/api/discovery")
    async def discovery():
        p = plat()
        return p.capability_report.model_dump(mode="json") if p.capability_report else {}

    def _range(p: Platform, start: date | None, end: date | None, days: int) -> tuple[date, date]:
        today = p.clock.now().astimezone(LONDON).date()
        end_ = (end + timedelta(days=1)) if end else today + timedelta(days=1)  # inclusive end date from the caller
        start_ = start or end_ - timedelta(days=days)
        if end_ <= start_:
            raise HTTPException(400, "end must be after start")
        if (end_ - start_).days > 800:
            raise HTTPException(400, "range too long")
        return start_, end_

    @app.get("/api/costs")
    async def costs(start: date | None = None, end: date | None = None, days: int = Query(7, ge=1, le=800),
                    group: str = Query("day", pattern="^(day|week|month)$")):
        """Costs for a range. ``end`` is inclusive; ``group`` rolls the rows up by ISO week or month."""
        p = plat()
        start_, end_ = _range(p, start, end, days)
        rows = p.grouped_series(start_, end_, group)
        return {"summary": p.energy_costs(start_, end_), "daily": rows, "group": group,
                "period": {"start": start_.isoformat(), "end": (end_ - timedelta(days=1)).isoformat()}}

    @app.get("/api/costs.csv")
    async def costs_csv(start: date | None = None, end: date | None = None, days: int = Query(30, ge=1, le=800),
                        group: str = Query("day", pattern="^(day|week|month)$")):
        from fastapi.responses import PlainTextResponse

        p = plat()
        start_, end_ = _range(p, start, end, days)
        text = p.costs_csv(p.grouped_series(start_, end_, group))
        return PlainTextResponse(text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="predheat-costs-{start_}-to-{end_ - timedelta(days=1)}.csv"'})

    @app.get("/api/timeline")
    async def timeline(hours: int = Query(48, ge=1, le=168)):
        return plat().timeline(hours)

    @app.get("/api/heat-loss")
    async def heat_loss(days: int = Query(14, ge=1, le=90)):
        """Fitted building heat loss (W/K) from recent history. Read-only."""
        p = plat()
        from ..db import session_scope

        with session_scope(p.settings.database_url) as s:
            return p.thermal_estimate(s, days=days)

    @app.get("/api/heat-pump")
    async def heat_pump(days: int = Query(30, ge=1, le=365)):
        """Fitted COP model and observed capacity. Read-only."""
        p = plat()
        from ..db import session_scope

        with session_scope(p.settings.database_url) as s:
            return p.heat_pump_estimate(s, days=days)

    @app.get("/api/accuracy")
    async def accuracy(days: int = Query(7, ge=1, le=90)):
        """Indoor temperature forecast accuracy of recorded plans."""
        p = plat()
        from ..db import session_scope

        with session_scope(p.settings.database_url) as s:
            return p.forecast_accuracy(s, days=days)

    @app.post("/api/poll")
    async def poll():
        """Trigger a collection cycle (read-only against equipment)."""
        return await plat().poll_once()

    @app.get("/api/settings")
    async def get_settings_():
        p = plat()
        return p.settings_store.describe(p.settings)

    @app.put("/api/settings")
    async def put_settings(changes: dict = Body(...)):
        """Update runtime preferences. Applies immediately; no restart.

        Switching Allow control on with unmet preconditions needs ``acknowledged: true``.
        """
        from ..db import session_scope
        from ..service.settings_store import SettingsError

        p = plat()
        changes = dict(changes)
        acknowledged = bool(changes.pop("acknowledged", False))
        before_mode, before_gate = p.settings.operating_mode, p.settings.control_enabled
        turning_on = "control_enabled" in changes and str(changes["control_enabled"]).lower() in ("true", "1", "yes", "on") and not before_gate
        unmet: list[str] = []
        if turning_on:
            with session_scope(p.settings.database_url) as s:
                pre = p.control_preconditions(s)
            unmet = [c["label"] for c in pre["checks"] if not c["met"]]
            if unmet and not acknowledged:
                raise HTTPException(400, "Preconditions not met: " + "; ".join(unmet) + ". Tick the acknowledgement to continue.")
        try:
            p.settings_store.update(changes, p.settings)
        except SettingsError as exc:
            raise HTTPException(400, str(exc)) from exc
        p._memo.clear()
        if "control_enabled" in changes and p.settings.control_enabled != before_gate:
            with session_scope(p.settings.database_url) as s:
                p.log_gate_change(s, p.settings.control_enabled, acknowledged, unmet)
        if (before_mode != p.settings.operating_mode and before_mode.value in ("balanced", "max_savings")) or (before_gate and not p.settings.control_enabled):
            await p.release_control("control switched off" if not p.settings.control_enabled else f"operating mode changed to {p.settings.operating_mode.value}")
        return p.settings_store.describe(p.settings)

    @app.get("/api/trial")
    async def trial(days: int = Query(28, ge=7, le=120)):
        p = plat()
        from ..db import session_scope

        with session_scope(p.settings.database_url) as s:
            return p.trial_report(s, days=days)

    @app.get("/api/control-preconditions")
    async def control_preconditions():
        p = plat()
        from ..db import session_scope

        with session_scope(p.settings.database_url) as s:
            return p.control_preconditions(s)

    @app.get("/api/climate-entities")
    async def climate_entities():
        return plat().climate_entities()

    @app.post("/api/settings/reset")
    async def reset_settings(key: str | None = Query(None)):
        from ..service.settings_store import SettingsError

        p = plat()
        before = p.settings.operating_mode
        try:
            p.settings_store.reset(key, p.settings)
        except SettingsError as exc:
            raise HTTPException(400, str(exc)) from exc
        p._memo.clear()
        if before != p.settings.operating_mode and before.value in ("balanced", "max_savings"):
            await p.release_control("operating mode reset")
        return p.settings_store.describe(p.settings)

    @app.get("/api/samples")
    async def samples(signal: str, hours: int = Query(48, ge=1, le=24 * 60)):
        """Stored readings for one signal, newest last. For checking what the accounting sees."""
        p = plat()
        from ..db import session_scope

        now = p.clock.now()
        with session_scope(p.settings.database_url) as s:
            rows = repo_samples(s, p.property_id, signal, now - timedelta(hours=hours), now)
        return {"signal": signal, "hours": hours, "count": len(rows), "samples": rows}

    @app.get("/api/mapping")
    async def mapping():
        p = plat()
        from ..db import session_scope

        with session_scope(p.settings.database_url) as s:
            return p.mapping_view(s)

    @app.put("/api/mapping/{signal}")
    async def put_mapping(signal: str, body: dict = Body(...)):
        """Choose the entity that feeds a signal, or clear the choice with an empty entity_id."""
        p = plat()
        from ..db import session_scope

        try:
            with session_scope(p.settings.database_url) as s:
                return await p.set_mapping(s, signal, (body.get("entity_id") or "").strip() or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/tariffs")
    async def tariffs(days: int = Query(30, ge=7, le=365), ev_kwh_per_day: float = Query(0.0, ge=0, le=100), battery_kwh_per_day: float = Query(0.0, ge=0, le=100),
                      live: bool = Query(False)):
        """Replay stored consumption on tariff templates, with EV and battery what-ifs. ``live=true`` first
        fetches published rates for your region from the Octopus API (no key needed)."""
        p = plat()
        from ..db import session_scope

        with session_scope(p.settings.database_url) as s:
            if live and p.octopus_api is not None and not p.last_poll.get("live_rates"):
                try:
                    await p.refresh_live_rates(s, days)
                except Exception as exc:  # noqa: BLE001
                    log.warning("live rates unavailable: %s", exc)
            return p.compare_tariffs(s, days=days, ev_kwh_per_day=ev_kwh_per_day, battery_kwh_per_day=battery_kwh_per_day)

    @app.get("/api/control-status")
    async def control_status():
        """Whether PredHeat is allowed to act, and what it last did. Read-only."""
        return plat().control_status()

    @app.api_route("/api/control/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def control_blocked(path: str):
        return JSONResponse(status_code=403, content={"detail": "Control is configured only through the add-on options, never through the API. See SAFETY.md."})

    @app.get("/api/ground-truth")
    async def ground_truth():
        p = plat()
        if p.simulated_home is None:
            raise HTTPException(404, "only available in simulated mode")
        return p.simulated_home.ground_truth()

    _mount_dashboard(app, settings)
    return app


def _mount_dashboard(app: FastAPI, settings: Settings) -> None:
    """Serve the built Vite dashboard from ``settings.frontend_dir`` when it exists.

    Registered after the API routes so ``/api/*`` always wins. Unknown paths fall
    back to ``index.html`` so the single-page app works under Home Assistant
    ingress, where the app is mounted below an opaque prefix.
    """
    root = settings.frontend_dir
    if root is None or not (root / "index.html").is_file():
        return
    root = root.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def dashboard(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (root / path).resolve() if path else root / "index.html"
        if candidate.is_file() and (candidate == root / "index.html" or root in candidate.parents):
            return FileResponse(candidate)
        return FileResponse(root / "index.html")


app = create_app()
