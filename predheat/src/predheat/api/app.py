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

from fastapi import FastAPI, HTTPException, Query
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
        p = state["platform"] or build_platform(settings)
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

    @app.post("/api/poll")
    async def poll():
        """Trigger a collection cycle (read-only against equipment)."""
        return await plat().poll_once()

    @app.api_route("/api/control/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def control_blocked(path: str):
        return JSONResponse(status_code=403, content={"detail": "Active control is disabled. See SAFETY.md. Phase 5 requires explicit authorisation."})

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
