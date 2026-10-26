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

    @app.get("/api/costs")
    async def costs(start: date | None = None, end: date | None = None, days: int = Query(7, ge=1, le=366)):
        p = plat()
        today = p.clock.now().astimezone(LONDON).date()
        end = end or today + timedelta(days=1)
        start = start or end - timedelta(days=days)
        return {"summary": p.energy_costs(start, end), "daily": p.daily_series(start, end)}

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
        """Update runtime preferences. Applies immediately; no restart."""
        from ..service.settings_store import SettingsError

        p = plat()
        before = p.settings.operating_mode
        try:
            p.settings_store.update(changes, p.settings)
        except SettingsError as exc:
            raise HTTPException(400, str(exc)) from exc
        p._memo.clear()
        if before != p.settings.operating_mode and before.value in ("balanced", "max_savings"):
            await p.release_control(f"operating mode changed to {p.settings.operating_mode.value}")
        return p.settings_store.describe(p.settings)

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

    @app.get("/api/tariffs")
    async def tariffs(days: int = Query(30, ge=7, le=365)):
        """Replay stored consumption on tariff templates. Read-only, indicative."""
        p = plat()
        from ..db import session_scope

        with session_scope(p.settings.database_url) as s:
            return p.compare_tariffs(s, days=days)

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
