"""The idea development pass (Phase 5 W-3).

Exporting a gap into a project can optionally run ONE strong-model pass that
takes it further than the engine's own output: sharpens the thesis, works the
wedge into concrete first steps, turns the riskiest assumption into a
falsification plan, and names the open questions.

Two rules govern this, and both are load-bearing:

1. **It must not launder the criticism.** The red team's verdicts, the weakest
   link, and any UNMEASURED caveats survive into the developed document under
   their own heading. An idea that reads better after export but hides what
   would kill it is a downgrade disguised as an upgrade — and it is exactly the
   failure this whole engine exists to prevent.
2. **It cannot fail.** If no backend can produce a real development pass, the
   caller falls back to the raw deterministic export with ``developed: false``
   in the frontmatter. Never canned prose, never a lost idea.
"""

from __future__ import annotations

import logging

from ..llm.client import ClaudeClient
from .schemas import Node

logger = logging.getLogger("gapfinder.develop")

DEVELOP_SYSTEM = """\
You are developing a market-gap hypothesis into a working document a founder will
actually use. You are given the engine's gap (thesis, why-now, wedge, company
shape, evidence) AND the red team's attempt to kill it.

# YOUR JOB
Take the idea FURTHER than the input. Not a summary — a development. Specifically:
- Sharpen the thesis into one sentence a stranger could repeat.
- Turn the wedge into a concrete first move: what exactly gets built/sold first,
  to whom, and what is deliberately NOT built.
- Convert the riskiest assumption into a falsification plan: the cheapest test
  that could prove it wrong THIS WEEK, and what result would kill the idea.
- Name the open questions the evidence cannot answer.

# INVIOLABLE RULES
- NEVER launder the criticism. Carry the red team's kills/weakens and the weakest
  link into a "What would kill this" section, in full force. If demand was
  UNMEASURED, say UNMEASURED — do not upgrade it to "promising" because the rest
  of the doc sounds confident.
- NEVER invent evidence, URLs, competitors, or numbers. You may reason, but every
  fact must come from the input.
- If the input is too thin to develop honestly, say so plainly rather than padding.

# OUTPUT
Markdown only. No preamble, no code fences. Start with `## Thesis`. Use these
sections in order: Thesis, Why now, The wedge (first move), Falsification plan,
What would kill this, Open questions.
"""


class DevelopUnavailable(RuntimeError):
    """No backend could produce a real development pass — caller must fall back."""


def _gap_brief(node: Node) -> str:
    """Everything the model may reason from — including the criticism."""
    g = node.gap
    if g is None:
        raise DevelopUnavailable("This node has no gap payload to develop.")

    L: list[str] = [f"# GAP: {g.title}", ""]
    L.append(f"THESIS: {g.thesis}")
    if g.why_now:
        L.append(f"WHY NOW: {g.why_now}")
    if g.wedge:
        L.append(f"WEDGE: {g.wedge}")
    if g.riskiest_assumption:
        L.append(f"RISKIEST ASSUMPTION: {g.riskiest_assumption}")
    if g.weakest_link:
        L.append(f"WEAKEST LINK: {g.weakest_link}")
    if node.viability is not None:
        L.append(f"VIABILITY: {node.viability}/100 (confidence: {node.confidence})")
    if node.fit is not None:
        L.append(f"FOUNDER FIT: {node.fit}/100 — {node.fit_reason}")

    if g.company:
        c = g.company
        L += ["", "COMPANY SHAPE:",
              f"- product: {c.product}", f"- ICP: {c.icp}",
              f"- business model: {c.business_model}", f"- moat: {c.moat}"]

    if g.competitors:
        L += ["", "COMPETITORS:"]
        for comp in g.competitors:
            L.append(f"- {comp.name}: {comp.positioning} — weakness: {comp.weakness}")

    if g.evidence:
        L += ["", "EVIDENCE (the only facts you may cite):"]
        for e in g.evidence:
            live = "" if e.live else " [MOCK/FIXTURE — not real evidence]"
            L.append(f"- [{e.source}]{live} {e.quote} ({e.url})")

    pt = node.pressure_test
    if pt and pt.lenses:
        L += ["", f"RED TEAM ({pt.summary}) — CARRY THIS THROUGH:"]
        for lens in pt.lenses:
            L.append(f"- {lens.lens} → {lens.verdict.upper()}: {lens.argument}")
        if pt.self_critique:
            L.append(f"- THE ENGINE'S CRITIQUE OF ITS OWN SCORE: {pt.self_critique}")
    return "\n".join(L)


async def develop_idea(
    node: Node, client: ClaudeClient, model: str, steering: str = ""
) -> str:
    """One strong-model pass → developed markdown. Raises DevelopUnavailable."""
    brief = _gap_brief(node)
    prompt = brief
    if steering.strip():
        prompt += f"\n\n== FOUNDER STEERING (who this is for) ==\n{steering.strip()}"

    try:
        result = await client.complete(
            prompt, system=DEVELOP_SYSTEM, max_turns=1, timeout=180, model=model
        )
    except Exception as exc:  # noqa: BLE001 - degrade to the raw export, never crash.
        raise DevelopUnavailable(
            f"Could not develop the idea: {type(exc).__name__}."
        ) from exc

    text = (getattr(result, "text", "") or "").strip()
    backend = getattr(result, "backend", "")
    # The fixture backend cannot produce a real development — refusing here is what
    # makes `developed: true` in the frontmatter mean something.
    if backend == "fixture":
        raise DevelopUnavailable(
            "The LLM backend is unavailable (fixture mode) — exported the raw idea "
            "instead of inventing a developed one."
        )
    if len(text) < 200:
        raise DevelopUnavailable("The development pass returned too little to trust.")
    return text
