"""Auto-fit — cluster loose gap ideas into fundable, company-shaped projects.

Ideas arrive faster than they get organized. A run spits out gaps, imports drop
in strays, mutations and crossovers spawn hybrids — and they pile up as a loose
pool with no home. Left alone that pool is just a list: the founder can't see
that three of the strays are really one company from three angles, or that a
fourth is a dead-on match for a project they already started.

This operator is the light-pass librarian for that pile. Given the loose gaps
and the projects that already exist, it proposes — for each gap — where it
belongs: an existing project whose thesis genuinely overlaps, or a tight NEW
cluster it should seed, naming the other loose gaps that belong with it. It is
PURE RECOMMENDATION: it reads plain tuples and returns plain proposals, moving
nothing itself and touching neither the library nor the schemas layer. The
caller decides what, if anything, to act on.

Deliberately plain-data in and out so it has zero coupling: a gap is
``(gap_id, title, thesis)``, a project is ``(slug, title, one_line_summary)``,
and the answer is a list of :class:`FitProposal`.

Like every operator here it degrades safely: a fixture backend, an empty pool,
any exception, or unparseable output yields ``[]``. A failed fit must never
break a run — the pile just stays a pile.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ValidationError

from ..analysis.synthesize import _extract_json_array
from ..llm.client import ClaudeClient

logger = logging.getLogger("gapfinder.autofit")


class FitProposal(BaseModel):
    """One gap's recommended home in the portfolio. Pure advice — nothing moves."""

    gap_id: str
    target: str = ""  # an existing project slug, or "" to seed a NEW cluster
    cluster_label: str = ""  # the theme name (existing project title, or a new one)
    reason: str = ""  # one sentence: why this gap fits there
    combinable_with: list[str] = []  # other loose gap_ids in the same cluster


_SYSTEM = """\
You are a portfolio curator for a startup-idea engine. You are handed a POOL of
loose, unorganized market-gap ideas and the set of PROJECTS that already exist.
Your job is to group the loose ideas into coherent, fundable, company-shaped
buckets — the way an investor sorts a messy deal pile into a few real theses.

RULES:
- For EACH loose gap, decide ONE home:
  * If its thesis TRULY overlaps an existing project (same buyer, same core
    problem, same company shape), fit it there — set "target" to that project's
    slug and "cluster_label" to that project's title. PREFER this when the
    overlap is real; don't spawn a duplicate of something that already exists.
  * Otherwise seed a NEW cluster — set "target" to "" and invent a tight,
    specific "cluster_label" naming the shared theme (a company, not a category).
- NEVER force unrelated ideas together to tidy the list. A gap that fits nowhere
  is its own new cluster of one; that is a correct, honest answer.
- "combinable_with" lists the OTHER loose gap_ids (from the pool given) that
  belong in the SAME cluster as this gap. Gaps you group together must share
  those lists consistently. Use [] when a gap stands alone.
- Give "reason" as ONE sentence: the concrete overlap that justifies the home.

Reason ONLY from the titles and theses given. Do not invent facts.

Return ONLY a top-level JSON array (no prose, no fences) with EXACTLY ONE object
per input gap, each shaped:
{"gap_id": "<the id you were given>", "target": "<existing slug or \\"\\">",
 "cluster_label": "<theme name>", "reason": "<one sentence>",
 "combinable_with": ["<other gap_id>", ...]}
Return [] only if you were given no gaps at all."""


def _render_gaps(loose_gaps: list[tuple[str, str, str]]) -> str:
    lines = ["LOOSE GAPS (id — title — thesis):"]
    for gap_id, title, thesis in loose_gaps:
        lines.append(f"- [{gap_id}] {title} — {thesis}")
    return "\n".join(lines)


def _render_projects(existing_projects: list[tuple[str, str, str]]) -> str:
    if not existing_projects:
        return "EXISTING PROJECTS: (none — every gap must seed a new cluster)"
    lines = ["EXISTING PROJECTS (slug — title — summary):"]
    for slug, title, summary in existing_projects:
        lines.append(f"- [{slug}] {title} — {summary}")
    return "\n".join(lines)


async def propose_fits(
    loose_gaps: list[tuple[str, str, str]],
    existing_projects: list[tuple[str, str, str]],
    client: ClaudeClient,
    model: str,
) -> list[FitProposal]:
    """Propose how loose gaps cluster into projects — pure recommendation.

    Returns one :class:`FitProposal` per gap the model successfully placed;
    invalid entries are dropped. Degrades to ``[]`` on empty input, a fixture
    backend, any exception, or unparseable output — it never raises.
    """
    if not loose_gaps:
        return []

    valid_ids = {gap_id for gap_id, _, _ in loose_gaps}
    prompt = (
        "Group these loose ideas into coherent, fundable buckets. Fit each into "
        "an existing project when the thesis truly overlaps, else seed a tight "
        "new cluster.\n\n"
        + _render_projects(existing_projects)
        + "\n\n"
        + _render_gaps(loose_gaps)
    )

    try:
        result = await client.complete(
            prompt, system=_SYSTEM, model=model, max_turns=1, timeout=120,
        )
    except Exception:  # noqa: BLE001 - a failed fit must never break a run
        logger.debug("autofit: LLM call failed; degrading to no proposals")
        return []

    if getattr(result, "backend", "") == "fixture":
        return []

    raw = _extract_json_array(result.text or "")
    if not raw:
        return []

    out: list[FitProposal] = []
    seen: set[str] = set()
    for obj in raw:
        if not isinstance(obj, dict):
            continue
        try:
            proposal = FitProposal(**obj)
        except ValidationError:
            continue
        # Drop hallucinated ids and duplicates — only real, unplaced gaps count.
        if proposal.gap_id not in valid_ids or proposal.gap_id in seen:
            continue
        # Scrub combinable_with down to real, other loose ids.
        proposal.combinable_with = [
            other
            for other in proposal.combinable_with
            if other in valid_ids and other != proposal.gap_id
        ]
        seen.add(proposal.gap_id)
        out.append(proposal)
    return out
