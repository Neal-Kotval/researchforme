"""The cross-project portfolio (Phase 4 H1) — every scored gap, one list.

:func:`portfolio_items` backs ``GET /api/portfolio``: a store-level rollup of
every ``kind == "gap"`` node across every project, each carrying the fields the
2×2 fit × viability scatter needs (viability, fit, confidence, star, triage,
stage). No LLM, no network. ``fit`` stays None when no steering scored it —
the frontend renders those in a separate "no steering" strip, never faked onto
the plot. Degrade-don't-crash: a broken store yields an empty portfolio.
"""

from __future__ import annotations

from .schemas import PortfolioItem
from .store import TreeStore


def portfolio_items(store: TreeStore) -> list[PortfolioItem]:
    """Every scored gap across projects, newest-updated first."""
    try:
        pairs = store.scored_gaps()
    except Exception:  # noqa: BLE001 - a broken store yields an empty portfolio.
        return []
    items = [
        PortfolioItem(
            project_id=node.project_id,
            domain=domain,
            node_id=node.id,
            title=node.title,
            viability=node.viability,
            fit=node.fit,
            confidence=node.confidence,
            star=node.star,
            triage=node.triage,
            stage=node.stage,
            updated_at=node.updated_at,
        )
        for node, domain in pairs
    ]
    items.sort(key=lambda i: (i.updated_at is not None, i.updated_at), reverse=True)
    return items
