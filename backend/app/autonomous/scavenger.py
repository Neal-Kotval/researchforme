"""Idle-headroom scavenger seam (Phase 3 C4) — opt-in only, never automatic.

Contract (docs/strategy/phase234-build.md C4): when a project opted in
(``Budget.allow_idle_deepening``), the governor reports **ample** headroom, and
the run is terminal-exhausted with unexpanded starred branches, the manual
``continue_deepening`` control becomes valid. This build ships the *seam* —
the eligibility check, the :func:`scavenger_candidates` helper, and the control
action — with NO automatic trigger of any kind (NO unattended LLM spend).

Everything here is pure store reads — no LLM, no network, never raises.
"""

from __future__ import annotations

from .schemas import Node, NodeKind, Project, ProjectStatus
from .store import TreeStore


def scavenger_candidates(store: TreeStore, project_id: str) -> list[Node]:
    """Unexpanded starred branches worth deepening into (the C4 candidates).

    A candidate is a starred, scored gap node with no children yet — a branch
    the run flagged as promising but never explored beneath. Highest viability
    first, so a bounded deepening pass spends on the best branches.
    """
    try:
        nodes = store.get_nodes(project_id)
    except Exception:  # noqa: BLE001 - a broken store yields no candidates.
        return []
    candidates = [
        n
        for n in nodes
        if n.kind is NodeKind.GAP and n.star and not n.child_ids
    ]
    candidates.sort(key=lambda n: n.viability or 0, reverse=True)
    return candidates


def deepening_ineligible_reason(
    project: Project, headroom: str, candidates: list[Node]
) -> str | None:
    """Why ``continue_deepening`` is invalid right now, or None when it's valid.

    The contract conditions, checked in order: the project must have opted in,
    be terminal-exhausted, the governor must report ample headroom, and there
    must be unexpanded starred branches to deepen into.
    """
    if not project.budget.allow_idle_deepening:
        return "This project has not opted in to idle deepening (allow_idle_deepening is off)."
    if project.status is not ProjectStatus.EXHAUSTED:
        return (
            "Idle deepening is only available once a run is exhausted "
            f"(status is '{project.status.value}')."
        )
    if headroom != "ample":
        return f"The usage governor reports '{headroom}' headroom — deepening needs ample."
    if not candidates:
        return "No unexpanded starred branches to deepen into."
    return None
