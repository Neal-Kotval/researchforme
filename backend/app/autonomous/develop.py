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
You are turning a market-gap hypothesis into a COMPANY PROPOSAL a founder can act
on. You are given the engine's gap (thesis, why-now, wedge, company shape,
evidence) AND the red team's attempt to kill it.

# WHAT YOU ARE WRITING
Not a summary, not a note — a proposal for a company. Someone should be able to
read it and know what would get built, who pays for it, what the first ninety days
are, and what would prove the whole thing wrong. Take the idea FURTHER than the
input: the engine found the gap; you are proposing the business.

# THE PLAIN-ENGLISH SECTION COMES FIRST, AND IT IS NOT OPTIONAL
Open with `## In plain English` — a short primer on the space for a smart reader
who does NOT work in it. Assume they have never heard the jargon. Explain what
this world actually is, who the people in it are, what they do all day, and why
anyone cares. Use an analogy if it helps. No acronyms without unpacking them, no
insider shorthand, no hype. If a reader finishes this section unable to explain
the space to a friend, you have failed. Three to five short paragraphs.

Everything after that section may assume the reader has read it.

# SECTIONS (in this order, all of them)
1. `## In plain English` — the primer above.
2. `## The problem` — who hurts, when, how much, and what they do today instead.
   Name the person, not "the market".
3. `## The thesis` — one sentence a stranger could repeat back.
4. `## Why now` — the specific, dated shift that makes this buildable/urgent THIS
   year and not three years ago. If the shift is weak, say so.
5. `## The product` — what actually gets built. Concrete enough to argue with.
6. `## Who buys it` — the ICP, the person who signs, and the trigger that makes
   them buy *this quarter* rather than someday.
7. `## The wedge — first 90 days` — the narrow first move: what gets built and sold
   first, to whom, and what is deliberately NOT built. Sequence it.
8. `## Business model` — how it makes money, rough pricing, and what the buyer is
   giving up to pay for it.
9. `## Competition and the status quo` — including the real competitor, which is
   usually "they do it by hand / a script they already wrote". Say why each
   incumbent structurally will NOT do this.
10. `## Moat` — what compounds. If nothing compounds on day one, say that plainly.
11. `## Falsification plan` — the cheapest test that could prove this wrong THIS
    WEEK. Name the result that would kill it. Be specific: who to call, what to
    grep, what number would have to come back.
12. `## What would kill this` — the red team, at full force (see rules).
13. `## Open questions` — what the evidence cannot answer.

# INVIOLABLE RULES
- NEVER launder the criticism. Carry the red team's kills/weakens and the weakest
  link into "What would kill this" at full strength. If demand was UNMEASURED, say
  UNMEASURED — do not let it drift to "promising" or "early signal" because the
  rest of the proposal reads confident. A proposal that sounds better than the
  evidence supports is worse than no proposal.
- NEVER invent evidence, URLs, competitors, numbers, or market sizes. You may
  reason and estimate, but label an estimate as an estimate, and every FACT must
  trace to the input. A fabricated TAM is a lie, not a proposal.
- If the input is too thin to develop honestly, say so plainly rather than padding.

# STYLE
Plain, precise, analytical. No hype, no buzzwords, no "revolutionary". Short
sentences. Concrete nouns. The reader is a technical founder deciding where to
spend a year of their life.

# OUTPUT
Markdown only. No preamble, no code fences. Start with `## In plain English`.
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
    # A company proposal with 13 sections is long. A short answer means the model
    # bailed or the input was too thin — either way, the raw export is more honest
    # than a stub wearing a proposal's headings.
    if len(text) < 1200:
        raise DevelopUnavailable(
            "The development pass returned too little to be a real proposal."
        )
    return text
