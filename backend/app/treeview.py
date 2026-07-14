"""Compact, presentation-level views over a TreeSnapshot dict.

Shared by the CLI and the MCP server so both show the same shape of a run:
a trimmed node dict (drop the heavy payloads), a gap-ranking helper, and an
indented text rendering of the tree. Everything here operates on the plain
JSON dicts returned by :class:`app.apiclient.GapFinderClient` — no pydantic,
so these views never break when the schema grows a field.
"""

from __future__ import annotations

from typing import Any, Optional


def compact_node(node: dict) -> dict:
    """The fields a human (or an LLM) needs to reason about a node — not the
    full pressure-test/gap payloads, which dwarf everything else."""
    out = {
        "id": node.get("id"),
        "parent_id": node.get("parent_id"),
        "kind": node.get("kind"),
        "state": node.get("state"),
        "title": node.get("title"),
        "depth": node.get("depth"),
        "viability": node.get("viability"),
        "fit": node.get("fit"),
        "star": node.get("star"),
    }
    # Optional flags only when set, to keep the payload lean.
    for key in ("pinned", "watched", "triage", "stage", "error"):
        if node.get(key):
            out[key] = node[key]
    return out


def compact_tree(snapshot: dict, *, starred_only: bool = False,
                 min_viability: Optional[int] = None) -> dict:
    """A snapshot trimmed to compact nodes, optionally filtered."""
    nodes = snapshot.get("nodes", [])
    if starred_only:
        nodes = [n for n in nodes if n.get("star")]
    if min_viability is not None:
        nodes = [n for n in nodes
                 if (n.get("viability") or 0) >= min_viability]
    return {
        "project": snapshot.get("project"),
        "nodes": [compact_node(n) for n in nodes],
        "node_count": len(snapshot.get("nodes", [])),
    }


def top_gaps(snapshot: dict, limit: int = 10) -> list[dict]:
    """The highest-viability scored gap nodes, best first."""
    gaps = [n for n in snapshot.get("nodes", [])
            if n.get("kind") == "gap" and n.get("viability") is not None]
    gaps.sort(key=lambda n: (n.get("viability") or 0, n.get("fit") or 0),
              reverse=True)
    return [compact_node(n) for n in gaps[:limit]]


def render_tree(snapshot: dict, *, max_depth: Optional[int] = None) -> str:
    """Indented text rendering of the tree for terminal display."""
    nodes = snapshot.get("nodes", [])
    by_parent: dict[Any, list[dict]] = {}
    for n in nodes:
        by_parent.setdefault(n.get("parent_id"), []).append(n)
    for children in by_parent.values():
        children.sort(key=lambda n: -(n.get("priority") or 0))

    lines: list[str] = []

    def walk(parent_id: Any, depth: int) -> None:
        if max_depth is not None and depth > max_depth:
            return
        for n in by_parent.get(parent_id, []):
            badges = []
            if n.get("viability") is not None:
                badges.append(f"v{n['viability']}")
            if n.get("fit") is not None:
                badges.append(f"f{n['fit']}")
            if n.get("star"):
                badges.append("*")
            if n.get("pinned"):
                badges.append("pin")
            if n.get("watched"):
                badges.append("watch")
            if n.get("triage"):
                badges.append(n["triage"])
            suffix = f"  [{' '.join(badges)}]" if badges else ""
            state = n.get("state", "")
            lines.append(
                f"{'  ' * depth}- ({n.get('kind')},{state}) {n.get('title')}{suffix}"
            )
            walk(n.get("id"), depth + 1)

    walk(None, 0)
    return "\n".join(lines) if lines else "(empty tree)"
