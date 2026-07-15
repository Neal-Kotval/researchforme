"""Synthesize a real project.md when a project is created (Phase 6, task #7).

Exporting ideas into a project used to leave a stub project.md — an empty
template. This writes a real plan instead: what the project IS, which idea is the
wedge, the first ninety days, and the open questions — derived from the ideas the
founder just imported.

It works for one idea (a plan built around it) or many (which is the wedge, what's
sequenced, what to shelve). Same honesty rules as everywhere: it carries the ideas'
red-team criticism forward, and if no LLM backend can produce a real plan it raises
so the caller keeps the honest stub rather than writing canned filler.
"""

from __future__ import annotations

import logging

from ..llm.client import ClaudeClient

logger = logging.getLogger("gapfinder.projectplan")

PLAN_SYSTEM = """\
You are writing the project.md that anchors a founder's project — the spine other
documents hang off. The founder just collected one or more startup ideas into this
project. Turn them into a plan they can act on.

# SECTIONS (in order)
1. `## What this project is` — 2-3 sentences a stranger could understand: the
   space, the bet, who it's for. Plain language, no jargon dumps.
2. `## The wedge` — of the ideas here, which ONE is the sharpest place to start,
   and why. Name it. If there's only one idea, this is its concrete first move.
3. `## First 90 days` — a short, sequenced list of what to actually do first:
   what to build or validate, in what order. Concrete, not "do research".
4. `## Open questions` — the things that aren't answered yet, especially the
   demand questions. Pull the riskiest assumptions from the ideas.
5. `## What could kill this` — the load-bearing red-team points from the ideas
   that apply to the whole project. Do NOT soften them. If demand was UNMEASURED
   in the ideas, it is UNMEASURED here — say so.

# RULES
- Reason ONLY from the ideas given. Do not invent facts, competitors, numbers, or
  a market size.
- Do NOT manufacture a grand unifying thesis if the ideas don't share one — if
  they're only loosely related, say the project is a holding pen for related bets
  and name the strongest as the wedge.
- Plain, precise, analytical. No hype. Short sentences. The reader is deciding
  where to spend a year.

# OUTPUT
Markdown only. No preamble, no code fences, no frontmatter. Start with
`## What this project is`.
"""


class ProjectPlanUnavailable(RuntimeError):
    """No backend could produce a real plan — caller keeps the stub."""


def _ideas_brief(ideas: list[tuple[str, str]]) -> str:
    parts = []
    for i, (title, body) in enumerate(ideas, 1):
        parts.append(f"===== IDEA {i}: {title} =====\n{body.strip()}")
    return "\n\n".join(parts)


async def synthesize_project_plan(
    project_title: str,
    ideas: list[tuple[str, str]],
    client: ClaudeClient,
    model: str,
) -> str:
    """One model pass over the imported ideas → project.md body. Raises
    ProjectPlanUnavailable if no real plan can be produced."""
    if not ideas:
        raise ProjectPlanUnavailable("No ideas to build a plan from.")

    prompt = (
        f"PROJECT: {project_title}\n"
        f"{len(ideas)} idea(s) were collected here. Write the project.md plan.\n\n"
        f"{_ideas_brief(ideas)}"
    )
    try:
        result = await client.complete(
            prompt, system=PLAN_SYSTEM, max_turns=1, timeout=180, model=model
        )
    except Exception as exc:  # noqa: BLE001 - degrade to the stub, never crash.
        raise ProjectPlanUnavailable(
            f"Could not write the project plan: {type(exc).__name__}."
        ) from exc

    text = (getattr(result, "text", "") or "").strip()
    if getattr(result, "backend", "") == "fixture":
        raise ProjectPlanUnavailable(
            "The LLM backend is unavailable (fixture mode) — kept the stub plan."
        )
    if len(text) < 300:
        raise ProjectPlanUnavailable("The plan pass returned too little to trust.")
    return text
