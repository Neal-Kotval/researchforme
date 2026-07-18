"""FastAPI application entry point for the Market Gap Finder.

Wires the analysis router under ``/api`` and enables CORS for the Vite dev server
(http://localhost:5173). Run locally with:

    uvicorn app.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers.analyze import router as analyze_router
from .routers.assistant import router as assistant_router
from .routers.library import router as library_router
from .routers.projects import router as projects_router

app = FastAPI(
    title="Market Gap Finder",
    description=(
        "Surfaces real, winnable market gaps by cross-referencing demand "
        "(Reddit + Hacker News), capability tailwinds (arXiv + GitHub), and "
        "attention trends (tech newsletters)."
    ),
    version="0.1.0",
)

# The frontend runs on the Vite dev server; allow it to call the API in dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# All analysis endpoints live under /api (e.g. POST /api/analyze).
app.include_router(analyze_router, prefix="/api")
# Autonomous exploration endpoints share the same /api prefix (e.g. /api/projects).
app.include_router(projects_router, prefix="/api")
# The Assistant control surface (POST /api/assistant/chat).
app.include_router(assistant_router, prefix="/api")
# The library: projects as folders, documents as markdown (Phase 5).
app.include_router(library_router, prefix="/api")


@app.on_event("startup")
async def _reconcile_orphaned_runs() -> None:
    """Park projects left RUNNING by a previous process (workers don't survive
    restarts) so the UI shows an honest paused-with-Resume state, not a
    perpetually 'sprinting' ghost.

    Opt-in ``AP_AUTO_RESUME`` (env) instead re-starts those runs immediately —
    for unattended operation where a restart should transparently continue the
    work rather than wait for a human to click Resume. Off by default: a boot
    must never begin spending tokens on its own unless explicitly told to."""
    import os

    from .autonomous.service import get_service

    service = get_service()
    parked = service.reconcile_on_boot()
    if parked and os.getenv("AP_AUTO_RESUME", "").strip().lower() in ("1", "true", "yes", "on"):
        for project in parked:
            try:
                await service.resume(project.id)
            except Exception:  # noqa: BLE001 - one bad resume can't block boot.
                pass


@app.on_event("shutdown")
async def _graceful_shutdown() -> None:
    """Stop workers and close SSE streams so a restart doesn't hang on open
    connections (previously required a kill -9)."""
    from .autonomous.service import get_service

    await get_service().shutdown()


@app.on_event("startup")
async def _maybe_start_watch_sweeper() -> None:
    """Start the periodic Space Watch sweep ONLY if the user opted in.

    Gated on ``Settings.watch_sweep_hours`` (env ``WATCH_SWEEP_HOURS``); the
    default ``None`` makes this a no-op — a server boot never begins fetching
    on its own. Manual sweeps stay available via ``POST /api/watch/sweep``.
    """
    from .autonomous.watch import start_background_sweeper

    start_background_sweeper()


@app.get("/")
def root() -> dict:
    """Tiny liveness probe for the root path."""
    return {"status": "ok", "service": "market-gap-finder", "docs": "/docs"}
