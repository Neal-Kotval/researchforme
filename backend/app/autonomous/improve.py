"""Strengthen a project against its own critique (Phase 5 W-8).

The red team tells you what's wrong. This closes the loop: it reads the current
plan AND the adversarial critique of it, and rewrites the plan to actually
CONFRONT the findings — narrow to the strongest defensible wedge, cut what the
critique said doesn't belong, fold in the validation it demanded, and either
strengthen the moat argument or state its limit honestly.

The point is a genuinely better bet, not a better-sounding one. A revision that
launders the criticism — makes the prose confident while the substance is
untouched — is worse than none, because the next red team (same lenses, same web
search) will find the same holes and the founder will have wasted a cycle
believing they addressed them. So the prompt is built to confront, not paper
over: it must name what it changed and why, and it may NOT invent evidence,
demand, or a moat the critique didn't support.

This is the generative half of the loop: critique → strengthen → re-validate,
and the score moves only if the substance did. Degrades honestly: no usable
backend → raise (503), never a fabricated revision.
"""

from __future__ import annotations

import logging

from ..llm.client import ClaudeClient

logger = logging.getLogger("gapfinder.improve")

IMPROVE_SYSTEM = """\
You are strengthening a startup PROJECT plan against an adversarial critique of
it. You have two inputs: the current plan, and a red team's critique naming where
the assembled bet is weak, unproven, or a holding pen of unrelated ideas.

Your job is to rewrite the plan so it genuinely CONFRONTS the critique — not to
argue with the red team, and not to make the plan sound more confident while
changing nothing.

# WHAT A STRONGER PLAN DOES
- NARROWS to the most defensible wedge the critique itself points to. If the red
  team said this is "one company only by abandoning the portfolio", abandon the
  portfolio in the rewrite: lead with the one bet, and demote the rest to
  explicit later-or-spin-out, not co-equal pillars.
- CUTS what the critique said doesn't belong or is already occupied by funded
  incumbents. Do not re-defend a bet the red team showed is a fast-follow.
- ANSWERS the falsification plan. Fold the cheapest kill-switch test into the
  first moves as the gating step, stated as "we do not build until this clears".
- CONFRONTS the moat honestly. If the critique showed the moat is copyable or
  contingent on an unproven claim, either sharpen a real defensibility argument
  or state the limit plainly and make the plan about winning the race, not
  pretending there isn't one.
- KEEPS the honest doubts. A stronger plan still carries "what could kill this";
  it does not delete the unmeasured-demand warning — it schedules the test.

# INVIOLABLE RULES
- Do NOT invent demand, evidence, competitors, or a moat the critique did not
  support. Strength comes from focus and honesty, not from new claims.
- Do NOT launder the criticism. If demand was UNMEASURED, it is still unmeasured
  in the rewrite — now with a dated plan to measure it, not a pretense that it's
  solved.
- The reader is a founder deciding where to spend a year. Plain, precise, no
  hype.

# OUTPUT — the improved plan as markdown, these sections in order:
1. `## What this project is` — the sharpened thesis (narrowed if the critique
   demanded it).
2. `## What changed and why` — a short, blunt list: what you cut, what you
   promoted to the wedge, what concern you're now confronting. This is the
   accountability section — it must map to the critique's actual findings.
3. `## The wedge` — the single strongest first move.
4. `## First 90 days` — sequenced, with the kill-switch validation as the gating
   first step ("we don't build until this clears").
5. `## Open questions` — what's still unresolved, honestly.
6. `## What could kill this` — the load-bearing risks, carried forward, not
   softened.

Markdown only. No preamble, no code fences. Start with `## What this project is`.
"""


class ImproveUnavailable(RuntimeError):
    """No backend could produce a real revision — caller returns 503."""


async def improve_project(
    project_title: str,
    plan_md: str,
    critique_md: str,
    client: ClaudeClient,
    model: str,
) -> str:
    """One strong-model pass: current plan + critique → a stronger plan.

    Raises :class:`ImproveUnavailable` (→503) rather than writing a fabricated
    revision when no real backend is reachable.
    """
    if not plan_md.strip():
        raise ValueError("Need a plan to strengthen.")
    if not critique_md.strip():
        raise ValueError("Need a critique to strengthen against — run the red team first.")

    prompt = (
        f"PROJECT: {project_title}\n\n"
        "Rewrite the plan below to confront the critique below it. Address the "
        "specific findings; narrow, cut, and schedule the tests the critique "
        "demands.\n\n"
        f"===== CURRENT PLAN =====\n{plan_md.strip()}\n\n"
        f"===== ADVERSARIAL CRITIQUE (what to confront) =====\n{critique_md.strip()}"
    )

    try:
        result = await client.complete(
            prompt,
            system=IMPROVE_SYSTEM,
            max_turns=1,
            timeout=240,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001 - degrade to 503, never crash.
        raise ImproveUnavailable(
            f"Could not strengthen the project: {type(exc).__name__}."
        ) from exc

    text = (getattr(result, "text", "") or "").strip()
    if getattr(result, "backend", "") == "fixture":
        raise ImproveUnavailable(
            "The LLM backend is unavailable (fixture mode) — no real revision was "
            "produced, so none was written."
        )
    if len(text) < 400:
        raise ImproveUnavailable("The strengthening pass returned too little to trust.")
    if "## what changed and why" not in text.lower():
        # The accountability section is the whole point — without it we can't
        # tell a real revision from a reworded one, so refuse rather than write a
        # plausible launder.
        raise ImproveUnavailable(
            "The revision omitted its 'What changed and why' accountability "
            "section — refusing to write an unaccountable rewrite."
        )
    return text
