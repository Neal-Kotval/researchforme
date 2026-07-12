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
from .routers.projects import router as projects_router

app = FastAPI(
    title="Market Gap Finder",
    description=(
        "Surfaces real, enterable market gaps by cross-referencing demand "
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


@app.on_event("startup")
def _reconcile_orphaned_runs() -> None:
    """Park projects left RUNNING by a previous process (workers don't survive
    restarts) so the UI shows an honest paused-with-Resume state, not a
    perpetually 'sprinting' ghost."""
    from .autonomous.service import get_service

    get_service().reconcile_on_boot()


@app.get("/")
def root() -> dict:
    """Tiny liveness probe for the root path."""
    return {"status": "ok", "service": "market-gap-finder", "docs": "/docs"}
