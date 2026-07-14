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
You are consolidating several startup ideas that a founder collected into one
project. Each idea below is a market-gap proposal with its own thesis, wedge, and
red-team criticism. Your job is to tell the founder whether these are ONE company
or several, and what to do next.

# WHAT YOU PRODUCE
A markdown document with these sections, in order:

1. `## Verdict` — one of exactly these, stated in the first line:
   - "One company." — the ideas share a thesis and belong together.
   - "A few companies." — they cluster into 2+ distinct businesses.
   - "Unrelated." — they do not belong in one project.
   Then one paragraph defending the verdict from the actual ideas.

2. `## The common thread` — the thesis, customer, or capability the ideas share,
   IF one genuinely exists. If they share only a surface theme ("all AI", "all
   for developers") and not a real thesis, SAY THAT — a shared tag is not a shared
   company.

3. `## Where they conflict` — the real tensions: ideas aimed at different buyers,
   incompatible wedges, assumptions that cannot both hold, or a cheap idea and a
   capital-intensive one that need different companies. Be specific and name the
   ideas. If there are no real conflicts, say so — do not invent tension.

4. `## What doesn't belong` — any idea that should be spun OUT into its own
   project, and why. Empty is a valid answer; forcing a bad fit is not.

5. `## Recommended plan` — IF this is one company: the sequenced plan — which idea
   is the wedge, which are later expansions, what to build first. IF it's several:
   which cluster to pursue first and why, and what to shelve.

6. `## What would kill the combined bet` — carry through the sharpest red-team
   points from the individual ideas that apply to the whole. Do not soften them.

# INVIOLABLE RULES
- Do NOT invent a unifying thesis. If the honest answer is "these are three
  different companies", the verdict is "Unrelated" or "A few companies" and you
  say why. A false synthesis is the single worst thing you can produce here.
- Do NOT launder the criticism. The combined bet inherits the individual ideas'
  weaknesses; carry the load-bearing ones forward. If demand was UNMEASURED for
  the ideas, it is UNMEASURED for the combination.
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
