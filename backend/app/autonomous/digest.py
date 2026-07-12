"""End-of-run digest (Phase 4 H4, docs/strategy/phase234-build.md).

When a project hits a terminal transition (exhausted / budget_spent /
time_limit) the service makes ONE cheap-model call over the run's scored gaps
and produces a compact hand-off digest::

    {top_spaces: [{title, why} x <=3], kill_pattern: str,
     next_questions: [2-3 strings], degraded: bool}

Degrade-don't-crash: if the LLM is unreachable or its output won't parse (the
fixture backend returns a gaps array, not a digest object), the digest is built
deterministically from the tree itself — top gaps by viability, the most-common
kill lens, and each top gap's own recorded riskiest assumption — and flagged
``degraded: true``. Nothing in the deterministic path is invented: every string
traces to data already on the nodes. :func:`build_digest` NEVER raises.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Optional

from ..analysis.synthesize import _strip_fences
from ..llm.client import ClaudeClient
from .intake import steering_context_block
from .schemas import Node, NodeKind, Project

_DIGEST_SYSTEM = """\
You write the end-of-run digest of an autonomous market-gap exploration. You are
given the run's scored gaps (title, viability 0..100, thesis, kill lenses) and
the founder's steering context. Produce a short, honest hand-off: the 2-3 spaces
most worth the founder's next hour (honouring their steering), the pattern in
what kept killing candidates, and 2-3 sharp next questions the run could NOT
answer. Only reference gaps from the supplied list — never invent one. Output
ONLY a JSON object, no prose, no fences."""


def _scored_gaps(nodes: list[Node]) -> list[Node]:
    """The run's scored GAP nodes, best viability first (deterministic order)."""
    gaps = [
        n for n in nodes
        if n.kind is NodeKind.GAP and n.viability is not None and n.gap is not None
    ]
    gaps.sort(key=lambda n: (-(n.viability or 0), n.title.lower()))
    return gaps


def _kill_counts(gaps: list[Node]) -> Counter:
    """How often each pressure lens recorded a kill across the run."""
    counts: Counter = Counter()
    for n in gaps:
        for lens in (n.pressure_test.lenses if n.pressure_test else []):
            if lens.verdict == "kills":
                counts[lens.lens] += 1
    return counts


def deterministic_digest(project: Project, nodes: list[Node]) -> dict:
    """The no-LLM fallback digest, flagged ``degraded: true``.

    Every field traces to recorded data — top gaps by viability (why = the
    gap's own thesis), the most-common kill lens, and next questions lifted
    from the top gaps' recorded riskiest assumptions. Never fabricates.
    """
    gaps = _scored_gaps(nodes)
    top_spaces = [
        {"title": n.title, "why": (n.gap.thesis if n.gap else "") or n.rationale}
        for n in gaps[:3]
    ]

    kills = _kill_counts(gaps)
    if kills:
        lens, count = kills.most_common(1)[0]
        kill_pattern = f"Most common kill: {lens} ({count} candidate{'s' if count != 1 else ''})."
    else:
        kill_pattern = "No pressure lens recorded a kill this run."

    next_questions = [
        f"Validate: {n.gap.riskiest_assumption}"
        for n in gaps[:3]
        if n.gap is not None and n.gap.riskiest_assumption.strip()
    ][:3]

    return {
        "top_spaces": top_spaces,
        "kill_pattern": kill_pattern,
        "next_questions": next_questions,
        "degraded": True,
    }


def _digest_prompt(project: Project, gaps: list[Node]) -> str:
    payload = [
        {
            "title": n.title,
            "viability": n.viability,
            "thesis": n.gap.thesis if n.gap else "",
            "riskiest_assumption": n.gap.riskiest_assumption if n.gap else "",
            "kill_lenses": [
                lens.lens
                for lens in (n.pressure_test.lenses if n.pressure_test else [])
                if lens.verdict == "kills"
            ],
        }
        for n in gaps[:12]
    ]
    steering = steering_context_block(project)
    steer = f"\n\n{steering}" if steering else ""
    return (
        f"DOMAIN EXPLORED: {project.domain}\n"
        f"STOP REASON: {project.stats.stop_reason or ''}\n\n"
        "SCORED GAPS (the only gaps you may reference):\n"
        f"{json.dumps(payload, ensure_ascii=False)}{steer}\n\n"
        "Return ONLY a JSON object shaped like:\n"
        '{"top_spaces": [{"title": "...", "why": "..."}], '
        '"kill_pattern": "the pattern in what kept dying", '
        '"next_questions": ["...", "..."]}'
    )


def _parse_digest(raw: str, gaps: list[Node]) -> Optional[dict]:
    """Validate the LLM's digest object; None on any shape problem.

    ``top_spaces`` titles must come from the run's own gap set (grounding:
    the digest never introduces a space the run didn't score).
    """
    try:
        obj: Any = json.loads(_strip_fences(raw or ""))
    except Exception:  # noqa: BLE001 - unparseable → caller falls back.
        return None
    if not isinstance(obj, dict):
        return None

    known = {n.title.strip().lower() for n in gaps}
    spaces: list[dict] = []
    raw_spaces = obj.get("top_spaces")
    if not isinstance(raw_spaces, list):
        return None
    for item in raw_spaces:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "") or "").strip()
        why = str(item.get("why", "") or "").strip()
        if title and title.lower() in known:
            spaces.append({"title": title, "why": why})
    if not spaces:
        return None

    questions_raw = obj.get("next_questions")
    questions = (
        [str(q).strip() for q in questions_raw if str(q).strip()][:3]
        if isinstance(questions_raw, list)
        else []
    )

    return {
        "top_spaces": spaces[:3],
        "kill_pattern": str(obj.get("kill_pattern", "") or "").strip(),
        "next_questions": questions,
        "degraded": False,
    }


async def build_digest(
    project: Project, nodes: list[Node], client: ClaudeClient
) -> dict:
    """ONE cheap-model call → the run digest; deterministic fallback. NEVER raises.

    Steering-aware: the founder's steering block rides in the prompt so the
    "top spaces" honour who they are. Any LLM/parse failure (including the
    fixture backend, whose canned output is a gaps array, not a digest object)
    degrades to :func:`deterministic_digest`, flagged ``degraded: true``.
    """
    gaps = _scored_gaps(nodes)
    if not gaps:
        return deterministic_digest(project, nodes)
    try:
        result = await client.complete(
            _digest_prompt(project, gaps),
            system=_DIGEST_SYSTEM,
            max_turns=1,
            timeout=90,
            model=project.decompose_model,
        )
        parsed = _parse_digest(result.text or "", gaps)
        return parsed if parsed is not None else deterministic_digest(project, nodes)
    except Exception:  # noqa: BLE001 - the digest is best-effort; never break _finish.
        return deterministic_digest(project, nodes)
