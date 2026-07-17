"""Consolidate a project's ideas into one thesis + plan (Phase 5 W-4).

A project accumulates several imported ideas. This pass reads them together and
answers the question a founder actually has: *do these belong to one company, and
if so, what is the plan?*

The whole value is in honesty about what does NOT fit:

- It names the **common thesis** only if there genuinely is one.
- It names where the ideas **conflict** — different customers, different wedges,
  assumptions that can't both be true.
- It says plainly when an idea **does not belong** and should be spun out into its
  own project, rather than forcing a false synthesis.

A merge prompt will happily invent a unifying story for ideas that share nothing;
the prompt is written to resist that. And like every LLM surface here it degrades
honestly: no usable backend → 503, never a fabricated consolidation.
"""

from __future__ import annotations

import logging

from ..llm.client import ClaudeClient

logger = logging.getLogger("gapfinder.consolidate")

CONSOLIDATE_SYSTEM = """\
You are the CURATOR for a founder who collected several startup ideas into one
project. Each idea below is a market-gap proposal with its own thesis, wedge, and
red-team criticism. Your job is NOT just to judge whether they're one company —
it is to CHERRY-PICK: select the strongest combinable subset, justify every
inclusion AND every exclusion, and assemble the best possible composite from the
parts that genuinely fit. Discard the parts that don't, with reasons.

Think like a founder building the strongest company they can from these
ingredients — keeping the good, cutting the weak, and being honest that the
result is a SELECTION, not the whole pile forced together.

# WHAT YOU PRODUCE
A markdown document with these sections, in order:

1. `## Verdict` — one blunt line: is there a strong company to build from a
   SUBSET of these ideas, and roughly which subset. (Not a binary "one company
   vs unrelated" — a selection.)

2. `## The composite — what to combine and why` — the cherry-picked set. For
   EACH idea you KEEP, one line justifying why it belongs and what it contributes
   to the combined company (the wedge, a moat input, a channel, an expansion
   surface). Name how the kept pieces reinforce each other — the real
   compounding link, if there is one — or say plainly that they're a portfolio
   held together by a shared buyer rather than a flywheel.

3. `## What's cut, and why` — every idea you DROP, each with a specific reason:
   already occupied by a funded incumbent, wrong buyer/channel from the rest,
   redundant with a stronger kept idea, capital profile that needs its own
   company, or a red-team kill it can't survive. A cut with no justification is
   not allowed. Cutting nothing is suspicious — most collections have a weak link.

4. `## The lead — where to start` — of the kept set, which single idea is the
   wedge and why, what's a later expansion, what to build first.

5. `## What would kill the combined bet` — carry the sharpest red-team points from
   the KEPT ideas forward to the whole. Do not soften them.

# INVIOLABLE RULES
- CHERRY-PICK, don't force-merge and don't over-prune. Keep what strengthens the
  company; cut what doesn't; justify both. A false unifying thesis is the worst
  output — but so is refusing to combine ideas that genuinely reinforce.
- Do NOT launder the criticism. The composite inherits the kept ideas'
  weaknesses; carry the load-bearing ones forward. If demand was UNMEASURED, it
  is UNMEASURED for the composite.
- Reason ONLY from the ideas given. Do not invent facts, competitors, or numbers.
- Plain, precise, analytical. No hype. The reader is deciding where to spend a
  year of their life.

# OUTPUT
Markdown only. No preamble, no code fences. Start with `## Verdict`.
"""


class ConsolidateUnavailable(RuntimeError):
    """No backend could produce a real consolidation — caller returns 503."""


def _ideas_brief(ideas: list[tuple[str, str]]) -> str:
    """Format the ideas (title, markdown-body) for the model, numbered."""
    parts: list[str] = []
    for i, (title, body) in enumerate(ideas, 1):
        parts.append(f"===== IDEA {i}: {title} =====\n{body.strip()}")
    return "\n\n".join(parts)


async def consolidate_ideas(
    project_title: str,
    ideas: list[tuple[str, str]],
    client: ClaudeClient,
    model: str,
) -> str:
    """One strong-model pass over N ideas → a consolidation doc. Raises on
    unavailability so the caller can 503 instead of writing canned content."""
    if len(ideas) < 2:
        raise ValueError("Need at least two ideas to consolidate.")

    prompt = (
        f"PROJECT: {project_title}\n"
        f"{len(ideas)} ideas were collected into this project. Consolidate them.\n\n"
        f"{_ideas_brief(ideas)}"
    )

    try:
        result = await client.complete(
            prompt,
            system=CONSOLIDATE_SYSTEM,
            max_turns=1,
            timeout=180,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001 - degrade to 503, never crash.
        raise ConsolidateUnavailable(
            f"Could not consolidate the ideas: {type(exc).__name__}."
        ) from exc

    text = (getattr(result, "text", "") or "").strip()
    backend = getattr(result, "backend", "")
    if backend == "fixture":
        raise ConsolidateUnavailable(
            "The LLM backend is unavailable (fixture mode) — no real consolidation "
            "was produced, so none was written."
        )
    if len(text) < 400:
        raise ConsolidateUnavailable(
            "The consolidation pass returned too little to trust."
        )
    return text
