"""Re-run diff (Phase 3 C3) — node-level comparison of two exploration trees.

:func:`project_diff` backs ``GET /api/projects/{pid}/diff?against={other}``:
scored gap nodes are matched across the two runs by *normalized title* (case,
punctuation, and whitespace folded away), then bucketed:

* ``new``   — gaps in ``pid`` with no title match in ``against``.
* ``gone``  — gaps in ``against`` with no title match in ``pid``.
* ``moved`` — title-matched gaps whose viability or fit changed, carrying the
  from (baseline ``against``) → to (``pid``) values for both scores.

Pure store computation — no LLM, no network. Degrade-don't-crash: unscored
sides stay None (never fabricated), and duplicate normalized titles within one
run collapse to the first (oldest) node so the match stays deterministic.
"""

from __future__ import annotations

import re

from .schemas import DiffEntry, MovedGap, Node, NodeKind, ProjectDiff
from .store import TreeStore

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    """Fold case, punctuation, and whitespace so trivially-restyled titles match."""
    return _NON_ALNUM.sub(" ", (title or "").lower()).strip()


def _gaps_by_title(store: TreeStore, project_id: str) -> dict[str, Node]:
    """Scored gap nodes of one project keyed by normalized title (first wins)."""
    out: dict[str, Node] = {}
    for node in store.get_nodes(project_id):
        if node.kind is not NodeKind.GAP:
            continue
        key = normalize_title(node.title)
        if key and key not in out:
            out[key] = node
    return out


def _entry(node: Node) -> DiffEntry:
    return DiffEntry(
        node_id=node.id, title=node.title, viability=node.viability, fit=node.fit
    )


def project_diff(store: TreeStore, project_id: str, against: str) -> ProjectDiff:
    """Diff ``project_id``'s scored gaps against the baseline run ``against``."""
    current = _gaps_by_title(store, project_id)
    baseline = _gaps_by_title(store, against)

    new = [_entry(n) for key, n in current.items() if key not in baseline]
    gone = [_entry(n) for key, n in baseline.items() if key not in current]
    moved = [
        MovedGap(
            title=cur.title,
            viability_from=base.viability,
            viability_to=cur.viability,
            fit_from=base.fit,
            fit_to=cur.fit,
        )
        for key, cur in current.items()
        if (base := baseline.get(key)) is not None
        and (base.viability != cur.viability or base.fit != cur.fit)
    ]
    return ProjectDiff(
        project_id=project_id, against=against, new=new, gone=gone, moved=moved
    )
