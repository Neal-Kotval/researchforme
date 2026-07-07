"""HTTP surface for the analysis pipeline.

Three endpoints, all mounted under the app's ``/api`` prefix:

* ``POST /analyze`` — run (or, when ``reweight_only`` is set, cheaply re-rank) an
  analysis and return the full ``GapReport``.
* ``POST /rerank``  — re-weight an already-synthesized report without re-fetching;
  transparently falls back to a full analysis if nothing is cached yet.
* ``GET  /health``  — which LLM backend resolved and which sources are live.

Errors never leak a stack trace: any unexpected failure is logged server-side and
returned as a clean HTTP 500 message. The pipeline itself is written to degrade
rather than raise, so a 500 here means something genuinely unexpected happened.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..config import AVAILABLE_MODELS, get_settings
from ..llm.client import get_client
from ..pipeline import rerank_cached, run_analysis
from ..schemas import AnalyzeRequest, GapReport, RankRequest, Weights
from ..sources.registry import get_sources

logger = logging.getLogger("gapfinder.api")

router = APIRouter(tags=["analysis"])


@router.post("/analyze", response_model=GapReport)
async def analyze(req: AnalyzeRequest) -> GapReport:
    """Analyze an ``area`` for enterable market gaps.

    When ``req.reweight_only`` is set we first try to re-rank a cached synthesis
    for this ``(area, sub_segments)`` — instant, no fetch, no LLM. If nothing is
    cached we transparently fall back to a full run so the caller always gets a
    report.
    """
    try:
        if req.reweight_only:
            rank_req = RankRequest(
                area=req.area,
                sub_segments=req.sub_segments,
                weights=req.weights or Weights(),
            )
            cached = rerank_cached(rank_req)
            if cached is not None:
                return cached
            # No cached synthesis yet -> fall through to a full analysis.
        return await run_analysis(req)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - never leak internals to the client.
        logger.exception("analyze failed for area=%r", req.area)
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed unexpectedly: {type(exc).__name__}.",
        ) from exc


@router.post("/rerank", response_model=GapReport)
async def rerank(req: RankRequest) -> GapReport:
    """Re-rank an existing analysis with new weights (no re-fetch, no LLM).

    If the report cache is cold (e.g. the server restarted), we fall back to a
    full analysis using the request's area/sub-segments so the endpoint always
    returns a valid ``GapReport``.
    """
    try:
        cached = rerank_cached(req)
        if cached is not None:
            return cached
        # Cache miss -> run the full pipeline, seeding the cache for next time.
        return await run_analysis(
            AnalyzeRequest(
                area=req.area,
                sub_segments=req.sub_segments,
                weights=req.weights,
            )
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - never leak internals to the client.
        logger.exception("rerank failed for area=%r", req.area)
        raise HTTPException(
            status_code=500,
            detail=f"Re-rank failed unexpectedly: {type(exc).__name__}.",
        ) from exc


@router.get("/health")
def health() -> dict:
    """Report the resolved LLM backend and which sources are live vs mock.

    Cheap and network-free: reads settings-derived flags only.
    """
    try:
        backend = get_client().backend
    except Exception:  # noqa: BLE001 - health must not itself 500 on LLM init.
        backend = "unknown"

    sources = {
        src.name.value: {
            "live": bool(src.live),
            "mode": "live" if src.live else "mock",
        }
        for src in get_sources()
    }
    return {
        "status": "ok",
        "llm_backend": backend,
        "sources": sources,
        "models": AVAILABLE_MODELS,
        "default_model": get_settings().llm_model,
    }
