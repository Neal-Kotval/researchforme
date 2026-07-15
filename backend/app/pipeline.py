"""The end-to-end analysis pipeline.

One function orchestrates the whole loop:

    scope_area  ->  fetch ALL sources in parallel  ->  extract_signals
                ->  synthesize (LLM, degrades to fixtures)
                ->  rank_gaps  ->  assemble GapReport

Design notes
------------
* **Parallel, non-blocking fetch.** Each ``Source.fetch`` is synchronous (per the
  ``Source`` ABC) and may do network I/O, so we run them off the event loop with
  ``asyncio.to_thread`` and await them together via ``asyncio.gather``. Adapters
  never raise, so ``gather`` needs no per-source error shielding — but we still
  fall back to a minimal report if an adapter somehow returns one that is ``None``.

* **Two caches, two purposes.**
    - Ingest cache (``ns='ingest:<source>'``) is owned by the adapters themselves.
      ``req.refresh`` bypasses it by clearing those namespaces before fetching, so
      the next fetch is forced to hit the live source (or re-derive its mock).
    - Report cache (``ns='report'``) stores the *synthesized, unranked* gaps keyed
      by ``(area, sub_segments)``. This lets ``rerank_cached`` re-weight and
      re-order an existing analysis WITHOUT re-fetching or re-calling the LLM.

* **Deterministic key.** Both ``run_analysis`` and ``rerank_cached`` key the report
  cache on the *raw request* ``(area, sub_segments)`` — the one thing both request
  shapes share — so a rerank always finds the analysis it belongs to.
"""

from __future__ import annotations

import asyncio

from .analysis.extract import extract_signals
from .analysis.rank import rank_gaps
from .analysis.scope import scope_area
from .analysis.synthesize import synthesize
from .cache import get_cache
from .config import resolve_model
from .llm.client import get_client
from .schemas import (
    AnalyzeRequest,
    Gap,
    GapReport,
    RankRequest,
    SourceName,
    SourceReport,
    SourceStatus,
    Weights,
)
from .sources.base import FetchResult
from .sources.registry import get_sources

# Cache namespace for synthesized reports (adapters own the 'ingest:*' namespaces).
_REPORT_NS = "report"
# The ingest namespaces we clear when the caller asks for a hard refresh.
_INGEST_NAMESPACES = tuple(f"ingest:{name.value}" for name in SourceName)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _report_for(source_name: SourceName, result: FetchResult) -> SourceReport:
    """Return the adapter's own report, or synthesize a minimal one if missing.

    Adapters are contractually required to attach a ``SourceReport``; this guard
    just guarantees the telemetry strip in the UI always has a row per source.
    """
    if result.report is not None:
        return result.report
    return SourceReport(
        name=source_name,
        status=SourceStatus.EMPTY,
        item_count=len(result.items),
        note="Adapter returned no report; defaulted to EMPTY.",
    )


async def _fetch_all(
    req: AnalyzeRequest, keywords: list[str], sub_segments: list[str]
) -> tuple[dict[SourceName, FetchResult], list[SourceReport]]:
    """Fetch every registered source in parallel and collect items + reports."""
    if req.refresh:
        # Bypass the ingest cache: clear each adapter's namespace so the next
        # fetch is forced to re-hit the live source (or freshly re-derive mock).
        cache = get_cache()
        for ns in _INGEST_NAMESPACES:
            cache.clear(ns)

    sources = get_sources()
    results: list[FetchResult] = await asyncio.gather(
        *(
            asyncio.to_thread(src.fetch, req.area, keywords, sub_segments)
            for src in sources
        )
    )

    # Fixture-contamination gate — mirror the autonomous engine (engine._fetch_all).
    # A degraded adapter (e.g. Reddit 403) serves an off-domain fixture corpus; on a
    # real run that content must not enter synthesis and become a fintech "gap" on a
    # bio query. Reports still flow (confidence cap intact); only a deliberate
    # fixture run keeps the fixtures.
    try:
        from .llm.client import get_client
        real_run = get_client().backend != "fixture"
    except Exception:  # noqa: BLE001
        real_run = True

    fetched: dict[SourceName, FetchResult] = {}
    reports: list[SourceReport] = []
    for src, result in zip(sources, results):
        report = _report_for(src.name, result)
        reports.append(report)
        if real_run and getattr(result, "report", None) is not None \
                and result.report.status is SourceStatus.MOCK:
            continue
        fetched[src.name] = result
    return fetched, reports


def _cache_synthesis(
    req: AnalyzeRequest,
    sub_segments: list[str],
    gaps: list[Gap],
    reports: list[SourceReport],
    llm_mode: str,
    model: str,
    warnings: list[str],
) -> None:
    """Persist the unranked synthesis so ``rerank_cached`` can re-weight it later.

    Keyed on the raw request ``(area, sub_segments)`` so a later ``RankRequest``
    with the same area/sub-segments resolves to this exact analysis.
    """
    payload = {
        "area": req.area,
        "sub_segments": sub_segments,
        "gaps": [g.model_dump(mode="json") for g in gaps],
        "sources": [r.model_dump(mode="json") for r in reports],
        "llm_mode": llm_mode,
        "model": model,
        "warnings": warnings,
    }
    get_cache().set(_REPORT_NS, payload, req.area, req.sub_segments)


# --------------------------------------------------------------------------- #
# Public entry points                                                         #
# --------------------------------------------------------------------------- #
async def run_analysis(req: AnalyzeRequest) -> GapReport:
    """Run the full ingest -> extract -> synthesize -> rank loop for one request.

    Never raises for ordinary degraded-source / degraded-LLM modes: sources fall
    back to mock, synthesis falls back to fixtures, and the resulting statuses /
    warnings are surfaced on the ``GapReport``.
    """
    weights = req.weights or Weights()
    model = resolve_model(req.model)

    # 1) Deterministic scope (keywords + sub-segments) — drives cache keys too.
    scope = scope_area(req.area, req.sub_segments)

    # 2) Parallel ingest across all sources.
    fetched, reports = await _fetch_all(req, scope.keywords, scope.sub_segments)

    # 3) Deterministic reduction into typed, normalized signal streams.
    signals = extract_signals(req.area, scope, fetched)

    # 4) Creative synthesis (LLM; degrades to fixtures). Returns unranked gaps.
    gaps, llm_mode, warnings = await synthesize(signals, reports, get_client(), model=model)

    # 5) Cache the unranked synthesis for cheap future re-weighting.
    _cache_synthesis(req, scope.sub_segments, gaps, reports, llm_mode, model, warnings)

    # 6) Weighted ranking for the served payload.
    ranked = rank_gaps(gaps, weights)

    return GapReport(
        area=req.area,
        sub_segments=scope.sub_segments,
        sources=reports,
        weights=weights,
        gaps=ranked,
        llm_mode=llm_mode,
        model=model,
        cache_hit=False,
        warnings=warnings,
    )


def rerank_cached(req: RankRequest) -> GapReport | None:
    """Re-rank a previously-synthesized report with new weights — no re-fetch.

    Returns a freshly-ranked ``GapReport`` (``cache_hit=True``) if a synthesis is
    cached for this ``(area, sub_segments)``, else ``None`` so the caller can fall
    back to a full ``run_analysis``. Pure/synchronous: only a cache read + a sort.
    """
    payload = get_cache().get(_REPORT_NS, req.area, req.sub_segments)
    if not payload or not isinstance(payload, dict):
        return None

    try:
        gaps = [Gap.model_validate(g) for g in payload.get("gaps", [])]
        reports = [SourceReport.model_validate(r) for r in payload.get("sources", [])]
    except Exception:  # noqa: BLE001 - a corrupt cache entry -> force a fresh run.
        return None

    ranked = rank_gaps(gaps, req.weights)
    warnings = list(payload.get("warnings", []))
    warnings.append("Re-ranked from cached synthesis (no re-fetch).")

    return GapReport(
        area=req.area,
        sub_segments=list(payload.get("sub_segments", req.sub_segments)),
        sources=reports,
        weights=req.weights,
        gaps=ranked,
        llm_mode=payload.get("llm_mode", "unknown"),
        model=payload.get("model", ""),
        cache_hit=True,
        warnings=warnings,
    )
