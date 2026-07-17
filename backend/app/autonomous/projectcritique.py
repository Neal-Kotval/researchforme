"""Adversarial validation for a *project* — the same rigor a gap gets, aimed at
the assembled thesis (Phase 5 W-7).

An individual gap is hammered by a seven-lens red team with live web search, a
viability score, a confidence rating, and a self-critique. The project it becomes
part of — the thing a founder actually commits a year to — used to get only a
synthesis pass that critiques itself *as an advocate* ("here, honestly, is what
could kill this"). That is not a red team. A platform bet deserves at least the
scrutiny its pieces got, arguably more.

This module runs the seven lenses against the PLATFORM thesis, not the individual
ideas. The lenses are re-written for portfolio scope — the sharpest one asks
whether this is one company or a holding pen of features wearing a shared tag —
but the machinery is the exploration engine's, verbatim: the same verdict set
(survives / weakens / kills / unmeasured), the same scoring formula via
``score_viability``, the same confidence logic. So a project's number is directly
comparable to a gap's.

It also produces the project-level falsification plan the gaps get as a research
pack: the single cheapest experiment that kills the whole bet, front-loaded.

Degrades honestly like every LLM surface here: no usable backend → raise, never a
fabricated critique. A false green light on a project is the most expensive lie
this system could tell.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..analysis.rank import composite_score
from ..llm.client import ClaudeClient
from ..schemas import CompanyConcept, Gap, Scores, Weights
from .pressure import _NOVELTY_DELTA, _assemble, _confidence_for, _parse_verdicts, _tally
from .schemas import Confidence, PressureTest

logger = logging.getLogger("gapfinder.projectcritique")

# The seven lenses, re-aimed at a portfolio thesis. Keys match the exploration
# engine's so the reused parser/assembler accept them unchanged; the prompts are
# rewritten for a company assembled from several ideas.
PROJECT_LENSES: tuple[tuple[str, str], ...] = (
    (
        "just_a_feature",
        "IS THIS ONE COMPANY, OR A HOLDING PEN? This is the sharpest lens for a "
        "project. The ideas share a tag ('all silicon IP', 'all used-GPU') — but "
        "a shared theme is not a shared company. Try to prove these are several "
        "separate businesses forced under one banner: do they share a BUYER, a "
        "channel, and a moat, or just a market? Does building idea A actually make "
        "idea B cheaper to sell (a real flywheel), or are they independent bets a "
        "founder is bundling to feel bigger? If the 'platform' is a portfolio with "
        "no compounding link, verdict=kills. If there is a real thread but it is "
        "thinner than the pitch claims, verdict=weakens.",
    ),
    (
        "incumbent_countermove",
        "INCUMBENT COUNTER-MOVE ON THE WHOLE COMPANY. Use web_search. Try to prove "
        "a well-resourced incumbent already occupies the assembled position, or "
        "could fold it into an existing product. Check the platform, not just one "
        "idea: who sells this bundle today, who could bundle it tomorrow, and has a "
        "funded startup already been embedded in the buyer's stack (the Emerald-AI "
        "pattern — a competitor with a marketing site and a design win leaves no "
        "GitHub repo or arXiv paper). If a named, funded player already ships "
        "substantially this to a buyer who can already buy it, verdict=kills.",
    ),
    (
        "demand_mirage",
        "DEMAND MIRAGE FOR THE ASSEMBLED BET. Try to prove the demand is inferred, "
        "not measured. The individual ideas may each have plausible pain, but does "
        "anyone pay for the COMBINED offering? Separate 'this would be useful' from "
        "'we have a budget line for this'. If the plan already admits demand is "
        "UNMEASURED, do not let its analytical confidence launder that — carry it "
        "forward. Killing here needs evidence of real, converting demand that is "
        "ABSENT; if you cannot settle it, verdict=unmeasured and name the check.",
    ),
    (
        "moat",
        "THE PLATFORM MOAT. The whole reason to assemble these ideas is that the "
        "company is more defensible than any piece alone (a shared dataset, a "
        "cross-idea network effect, a standard others adopt). Try to prove that "
        "compounding advantage is imaginary or copyable. Does the moat actually "
        "accrue from combining the ideas, or is each piece independently "
        "commoditizable? If the claimed flywheel does not turn, verdict=kills; if "
        "it turns slowly or is contestable, verdict=weakens.",
    ),
    (
        "venture_scale",
        "VENTURE SCALE OF THE COMPANY. Assume the platform works. Try to prove the "
        "ceiling is a good-specialist-business, not a venture outcome: too few "
        "buyers worldwide, license fees that top out in the low tens of millions, "
        "no expansion surface beyond the ideas already listed. Being big, capital-"
        "intensive, or slow is NOT what this lens attacks — a small ceiling is. If "
        "there is a credible path to a large company, verdict=survives and say why.",
    ),
    (
        "empty_for_a_reason",
        "EMPTY FOR A REASON. Try to prove the assembled white space is empty "
        "because it is a TRAP: a regulatory wall, a channel nobody can reach, a "
        "buyer who structurally will not pay a third party. CRITICAL: if you find "
        "the space is not empty but CROWDED — funded companies already here — that "
        "is BAD news for the premise, not good. Do NOT return survives for a "
        "crowded field; return weakens, or kills if the crowd already serves the "
        "buyer. Reserve survives for a space that is genuinely open AND not a trap.",
    ),
    (
        "why_now_fragility",
        "WHY-NOW FRAGILITY. Try to prove the platform's 'why now' is fragile or "
        "retrofitted: a shift that already happened and closed, or one that could "
        "reverse (a hardware generation that closes the gap, a regulation that "
        "lapses). A durable, dated, still-open shift survives; a soft or fading one "
        "weakens.",
    ),
)

_LENS_KEYS = frozenset(k for k, _ in PROJECT_LENSES)

_SYSTEM = """\
You are the RED TEAM for a startup PROJECT — a company a founder has assembled
from several individual ideas. Your job is NOT to like it. It is to try, in good
faith and with evidence, to KILL the assembled thesis. You are the adversary the
project's own plan is not: the plan argues for itself; you argue against it.

You wear several independent lenses, one at a time. For each, attempt to prove its
kill-question TRUE.

# RULES OF ENGAGEMENT
- Attack the ASSEMBLED company, not just its pieces. The ideas were already
  red-teamed individually; your job is the thing they add up to.
- Be specific and structural. "It might be hard" is worthless. Name the exact
  incumbent, the exact reason the flywheel doesn't turn, the exact buyer who
  won't pay. Generic doubt does not kill.
- Use web_search to check whether a funded competitor already occupies the
  assembled position — a rival with a marketing site and a design win leaves no
  repo or paper. Check before concluding the field is open.
- NO FABRICATION. Every evidence URL must come from the project text or a tool
  result you actually fetched. Never invent a competitor, a quote, or a number.
- ABSENCE OF EVIDENCE IS NOT EVIDENCE OF ABSENCE. If you cannot settle a lens from
  what you have, the verdict is "unmeasured" — not "survives". Say what check
  would settle it. An unchecked axis is an open question, never a free pass.
- "survives" is a POSITIVE claim: you attempted the kill and the thesis withstood
  it. If you did not actually check, it is "unmeasured".

# VERDICTS (per lens)
- "kills"      — a credible, specific case that this lens's kill-question is TRUE.
- "weakens"    — a real hit, not fatal.
- "survives"   — you attempted the kill and the thesis held; say what you tried.
- "unmeasured" — you could not settle it; name the check that would.

# ALSO ASSESS THE PROJECT ON FIVE AXES (1-5, your honest read of the ASSEMBLED
# bet, not the average of its ideas):
- demand_strength, competitive_openness (5=open, 1=crowded), trend_tailwind,
  feasibility (can THIS founder build it, given their stated resources — capital
  and long timelines are NOT penalties if they said those are acceptable),
  willingness_to_pay. Plus novelty (1-5): how non-obvious is the assembled thesis?

# AND WRITE THE FALSIFICATION PLAN
The single cheapest experiment that could kill the WHOLE bet, and the sequence of
tests ordered by cost — the kill-switch call a founder should run THIS WEEK before
building anything. Front-load the one question that, answered wrong, ends it.

# OUTPUT CONTRACT — STRICT
Return ONLY a top-level JSON object (no prose, no code fences), EXACTLY:
{
  "verdict_line": "one blunt sentence: is this a fundable company, and the single biggest reason it might not be",
  "scores": {"demand_strength": 1-5, "competitive_openness": 1-5, "trend_tailwind": 1-5, "feasibility": 1-5, "willingness_to_pay": 1-5},
  "novelty": 1-5,
  "lenses": [
    {"lens": "<key verbatim>", "verdict": "survives|weakens|kills|unmeasured",
     "argument": "the specific structural case",
     "evidence": [{"source": "web|arxiv|github|newsletter", "url": "<real>", "quote": "<snippet>", "date": "YYYY-MM-DD or null"}]}
  ],
  "self_critique": "the single strongest reason your OWN verdict/score is wrong",
  "validation_plan": "markdown: the cheapest falsification sequence, kill-switch question first"
}
Include one object per lens listed in the prompt. Output the JSON and nothing else.\
"""


class CritiqueUnavailable(RuntimeError):
    """No backend could produce a real critique — caller returns 503."""


@dataclass
class ProjectCritique:
    viability: int
    confidence: Confidence
    verdict_line: str
    lenses_markdown: str        # the per-lens red team, rendered
    validation_plan: str        # the falsification sequence
    self_critique: str
    scores: dict                # the 5 axes the LLM assessed
    survived: int
    weakened: int
    killed: int
    unmeasured: int


def _lens_block() -> str:
    return "\n\n".join(f"LENS `{key}`:\n{prompt}" for key, prompt in PROJECT_LENSES)


def _prompt(project_title: str, thesis_md: str) -> str:
    return (
        f"PROJECT UNDER ATTACK: {project_title}\n\n"
        "Here is the assembled project thesis and plan. Red-team the COMPANY it "
        "describes.\n\n"
        f"===== PROJECT THESIS =====\n{thesis_md.strip()}\n\n"
        f"===== APPLY EVERY LENS =====\n{_lens_block()}"
    )


def _synthetic_gap(project_title: str, thesis_md: str, obj: dict) -> Gap:
    """A Gap built purely as the scoring vehicle for ``score_viability``.

    Not fed to the lens prompts (those are project-scoped) — this only carries the
    LLM-assessed project scores + novelty into the identical scoring formula every
    gap uses, so the project's viability is on the same scale.
    """
    raw = obj.get("scores") or {}

    def _s(key: str) -> int:
        try:
            return max(1, min(5, int(raw.get(key, 3))))
        except (TypeError, ValueError):
            return 3

    scores = Scores(
        demand_strength=_s("demand_strength"),
        competitive_openness=_s("competitive_openness"),
        trend_tailwind=_s("trend_tailwind"),
        feasibility=_s("feasibility"),
        willingness_to_pay=_s("willingness_to_pay"),
    )
    try:
        novelty = max(1, min(5, int(obj.get("novelty", 3))))
    except (TypeError, ValueError):
        novelty = 3
    # A project is standalone-company-shaped by construction and has an expansion
    # surface (the ideas it hasn't led with) — so the deterministic fallbacks in
    # the scorer read it as a company, not a feature.
    company = CompanyConcept(
        product=project_title,
        standalone=True,
        expansion_path="the ideas beyond the wedge",
    )
    return Gap(
        title=project_title,
        thesis=thesis_md[:400],
        scores=scores,
        wedge="(assembled project)",
        riskiest_assumption="(see critique)",
        weakest_link="(see critique)",
        novelty=novelty,
        company=company,
    )


# Project-calibrated scoring deltas. Deliberately NOT the gap constants.
#
# A rigorous red team of a real COMPANY weakens on nearly every lens — every
# genuine business has seven honest concerns, so "weakens" is the expected
# baseline, not a ding worth -9. At the gap calibration, six weakens (-54) score
# LOWER than a single fatal kill (capped at 40) — so a project with six manageable
# concerns would rank below one with a fatal flaw. That is backwards.
#
# Here a weaken is a mild -5 (present but survivable), a kill is fatal and
# dominates via the cap, a survival is a real, rare positive, and unmeasured is
# zero — an open question to go resolve, surfaced separately, never a free pass
# and never a penalty for our failure to settle it.
_P_SURVIVE = 6.0
_P_WEAKEN = 5.0
_P_KILL = 22.0
_P_KILL_CAP = 42.0


def _score_project(gap: Gap, test: PressureTest) -> tuple[int, Confidence]:
    """Project-scale viability from the same axes + verdicts, softer on weakens.

    Reuses the gap machinery (composite of the five axes, novelty delta,
    confidence) but recalibrated so a company's expected fistful of honest
    concerns doesn't read as 'dead', while a genuine kill still does.
    """
    composite = composite_score(gap.scores, Weights())      # 1..5
    viability = (composite - 1.0) / 4.0 * 100.0

    survived, weakened, killed = _tally(test.lenses)
    viability += _P_SURVIVE * survived
    viability -= _P_WEAKEN * weakened
    viability -= _P_KILL * killed
    viability += _NOVELTY_DELTA.get(int(gap.novelty), 0.0)

    if killed:
        viability = min(viability, _P_KILL_CAP)

    viability_int = int(max(0, min(100, round(viability))))
    corroboration = sum(len(lv.evidence) for lv in test.lenses)
    return viability_int, _confidence_for(gap, test, corroboration)


def _render_lenses(test) -> str:  # noqa: ANN001 - PressureTest
    lines: list[str] = [test.summary, ""]
    for lv in test.lenses:
        lines.append(f"### {lv.lens.replace('_', ' ')} — {lv.verdict.upper()}")
        lines.append(lv.argument or "_(no argument given)_")
        for ev in lv.evidence:
            tag = " _(mock)_" if getattr(ev, "live", True) is False else ""
            lines.append(f"- {ev.source}{tag}: {ev.quote} ({ev.url})")
        lines.append("")
    return "\n".join(lines).strip()


async def critique_project(
    project_title: str,
    thesis_md: str,
    client: ClaudeClient,
    model: str,
) -> ProjectCritique:
    """Run the seven project-lenses against the assembled thesis, with web search.

    Raises :class:`CritiqueUnavailable` (→503) rather than writing a fabricated
    green light when no real backend is reachable.
    """
    if not thesis_md.strip():
        raise ValueError("Need a project thesis to critique.")

    try:
        result = await client.complete(
            _prompt(project_title, thesis_md),
            system=_SYSTEM,
            model=model,
            max_turns=6,
            timeout=300,
            web=True,
        )
    except Exception as exc:  # noqa: BLE001 - degrade to 503, never crash.
        raise CritiqueUnavailable(
            f"Could not critique the project: {type(exc).__name__}."
        ) from exc

    text = (getattr(result, "text", "") or "").strip()
    if getattr(result, "backend", "") == "fixture":
        raise CritiqueUnavailable(
            "The LLM backend is unavailable (fixture mode) — no real critique was "
            "produced, so none was written."
        )

    obj = _extract_object(text)
    if obj is None:
        raise CritiqueUnavailable("The critique pass returned nothing parseable.")

    known_urls = {
        u: bool(getattr(result, "tool_urls", None) and u in result.tool_urls)
        for lens in obj.get("lenses", [])
        if isinstance(lens, dict)
        for ev in (lens.get("evidence") or [])
        if isinstance(ev, dict)
        for u in [str(ev.get("url", ""))]
        if u
    }
    by_key = _parse_verdicts(obj.get("lenses") or [], set(_LENS_KEYS), known_urls)
    if not by_key:
        raise CritiqueUnavailable("The critique produced no usable lens verdicts.")

    # Any lens the model skipped is an open question, never a free pass — mirror
    # the engine's honesty about an unrun axis.
    from .schemas import LensVerdict

    lenses = []
    for key, _ in PROJECT_LENSES:
        lenses.append(
            by_key.get(key)
            or LensVerdict(
                lens=key,
                verdict="unmeasured",
                argument="Not returned by the critique pass — an open question, not a pass.",
            )
        )

    test = _assemble(lenses, "deep")
    gap = _synthetic_gap(project_title, thesis_md, obj)
    # The lenses' own evidence is the corroboration signal for confidence.
    gap.evidence = [ev for lv in lenses for ev in lv.evidence]
    viability, confidence = _score_project(gap, test)

    return ProjectCritique(
        viability=viability,
        confidence=confidence,
        verdict_line=str(obj.get("verdict_line", "") or "").strip(),
        lenses_markdown=_render_lenses(test),
        validation_plan=str(obj.get("validation_plan", "") or "").strip(),
        self_critique=str(obj.get("self_critique", "") or "").strip(),
        scores=gap.scores.model_dump(),
        survived=test.survived,
        weakened=test.weakened,
        killed=test.killed,
        unmeasured=test.unmeasured,
    )


def _extract_object(text: str) -> dict | None:
    """First balanced top-level JSON object in the text, or None."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def render_critique_markdown(c: ProjectCritique) -> str:
    """The critique.md body (frontmatter is added by the caller)."""
    parts = [
        "## Verdict",
        f"**Viability {c.viability}/100 · confidence {c.confidence}** — "
        f"{c.survived} survived · {c.weakened} weakened · {c.killed} killed · "
        f"{c.unmeasured} unmeasured across the red team.",
        "",
        c.verdict_line or "_(no verdict line)_",
        "",
        "## The red team",
        c.lenses_markdown,
        "",
        "## Validation plan — cheapest falsification first",
        c.validation_plan or "_(none produced)_",
        "",
        "## The strongest reason this verdict is wrong",
        c.self_critique or "_(none produced)_",
    ]
    return "\n".join(parts)
