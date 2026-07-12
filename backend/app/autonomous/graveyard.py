"""The anti-portfolio graveyard (Phase 2 S3, + the S4 external corpus merge).

Two consumers, one dataset:

* :func:`graveyard_items` backs ``GET /api/graveyard`` — the cross-project list
  of rejected gaps (killed by a pressure lens, viability ≤ 40, or user-passed),
  merged with the curated post-mortem corpus flagged ``external: true``. Pure
  store-level SQL + fixture read; no LLM, ever.
* :func:`graveyard_context_block` renders the most-relevant rejections (token
  overlap with the domain being decomposed) as a prompt block injected beside
  ``steering_context_block`` in the decomposition prompt, so the explorer stops
  re-proposing spaces the founder already buried — while staying free to flag a
  space whose recorded kill reason has expired.

Degrade-don't-crash: every path returns fewer (or no) items rather than raise.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from ..sources.postmortems import _FIXTURE, _tokens
from .schemas import GraveyardItem, Node
from .store import TreeStore


def _first_line(text: str, cap: int = 200) -> str:
    """The first non-empty line of ``text``, trimmed to ``cap`` chars."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= cap else line[: cap - 1] + "…"
    return ""


def _kill_lenses(node: Node) -> list[str]:
    """Names of the pressure lenses that killed this gap (may be empty)."""
    test = node.pressure_test
    if test is None:
        return []
    return [l.lens for l in test.lenses if l.verdict == "kills" and l.lens]


def _internal_items(store: TreeStore) -> list[GraveyardItem]:
    """Rejected gap nodes across every project, newest first."""
    try:
        rows = store.rejected_gaps()
    except Exception:  # noqa: BLE001 - a broken store yields an empty graveyard.
        return []
    items = [
        GraveyardItem(
            project_id=node.project_id,
            project_domain=domain,
            node_id=node.id,
            title=node.title,
            thesis_first_line=_first_line(node.gap.thesis if node.gap else ""),
            viability=node.viability,
            kill_lenses=_kill_lenses(node),
            triage_reason=node.triage_reason,
            updated_at=node.updated_at,
            external=False,
        )
        for node, domain in rows
    ]
    items.sort(
        key=lambda i: i.updated_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return items


def _external_items() -> list[GraveyardItem]:
    """The curated post-mortem corpus (S4) as graveyard entries, flagged external."""
    try:
        corpus = json.loads(_FIXTURE.read_text())
    except Exception:  # noqa: BLE001 - a missing corpus just merges nothing.
        return []
    items: list[GraveyardItem] = []
    for entry in corpus if isinstance(corpus, list) else []:
        if not isinstance(entry, dict) or not entry.get("company"):
            continue
        company = str(entry["company"])
        year = entry.get("year")
        kill_reason = str(entry.get("kill_reason") or "")
        created: Optional[datetime] = None
        try:
            if year:
                created = datetime(int(year), 1, 1, tzinfo=timezone.utc)
        except (TypeError, ValueError):
            created = None
        items.append(
            GraveyardItem(
                project_id=None,
                project_domain=None,
                node_id=f"postmortem:{company.lower().replace(' ', '-')}",
                title=f"{company} ({year})" if year else company,
                thesis_first_line=_first_line(str(entry.get("summary") or "")),
                viability=None,
                kill_lenses=[kill_reason] if kill_reason else [],
                triage_reason="",
                updated_at=created,
                external=True,
            )
        )
    return items


def _item_tokens(item: GraveyardItem) -> set[str]:
    """All searchable tokens of one graveyard item (for the ``q=`` filter)."""
    return _tokens(
        " ".join(
            [
                item.title,
                item.thesis_first_line,
                item.project_domain or "",
                item.triage_reason,
                " ".join(item.kill_lenses),
            ]
        )
    )


def graveyard_items(store: TreeStore, q: str = "", limit: int = 50) -> list[GraveyardItem]:
    """The merged graveyard list: internal rejections first, then the external
    post-mortem corpus. ``q`` is an every-token-must-match filter; ``limit``
    caps the merged result. No LLM; never raises."""
    items = _internal_items(store) + _external_items()
    query = _tokens(q or "")
    if query:
        items = [i for i in items if query <= _item_tokens(i)]
    return items[: max(1, limit)]


# --------------------------------------------------------------------------- #
# Decomposition-prompt injection (the S3 memory seam)                          #
# --------------------------------------------------------------------------- #
_HEADER = (
    "SPACES ALREADY REJECTED (the founder's cross-project anti-portfolio) — "
    "do not re-propose these as children; DO flag a child if you believe the "
    "recorded kill reason has expired:"
)


def graveyard_context_block(store: TreeStore, domain: str, cap: int = 12) -> str:
    """Render the most-relevant internal rejections as a prompt block (or "").

    Relevance = token overlap with ``domain`` (deterministic, no LLM), with
    recency as the tiebreaker — cross-project rejections still surface so a
    fresh domain can't resurrect an old buried space. Empty store → "".
    """
    items = _internal_items(store)
    if not items:
        return ""
    domain_tokens = _tokens(domain or "")
    ranked = sorted(
        items,
        key=lambda i: (
            -len(domain_tokens & _item_tokens(i)),
            -(i.updated_at or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
        ),
    )
    lines: list[str] = []
    for item in ranked[: max(1, cap)]:
        killed_by = ", ".join(item.kill_lenses) if item.kill_lenses else (
            "user passed" + (f" ({item.triage_reason})" if item.triage_reason else "")
        )
        line = f'- "{item.title}"'
        if item.project_domain:
            line += f" [{item.project_domain}]"
        line += f" — killed by: {killed_by}"
        if item.viability is not None:
            line += f" (viability {item.viability})"
        if item.thesis_first_line:
            line += f" — {item.thesis_first_line}"
        lines.append(line)
    return _HEADER + "\n" + "\n".join(lines)
