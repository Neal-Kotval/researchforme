"""Preflight intake — a short, sharp set of clarifying questions asked *before*
an exploration starts, so the tree is steered by who the builder is and what a
win looks like (SPEC feature A).

Degrade-don't-crash like everything else: if the LLM is unavailable or its output
won't parse, we fall back to a solid static question set derived from the domain,
so the intake step always returns something useful.
"""

from __future__ import annotations

from typing import Any

from ..analysis.synthesize import _extract_json_array
from ..llm.client import ClaudeClient
from .schemas import IntakeQuestion

_INTAKE_SYSTEM = """\
You are the intake step of an autonomous market-gap explorer. Before it spends
hours exploring a domain, you ask the founder a SHORT, SHARP set of clarifying
questions that will actually change what gets explored — who they are, their
constraints, what "a win" looks like, sub-segments to favor or avoid, and their
time/skill horizon. Ask 3-5 questions. Each must be decision-relevant (it should
steer scope, not be trivia) and come with 3-4 concrete suggested answers plus the
implicit option to free-type. Output ONLY a JSON array, no prose, no fences."""


def _prompt(domain: str) -> str:
    return (
        f"DOMAIN THE FOUNDER WANTS TO EXPLORE: {domain}\n\n"
        "Ask 3-5 clarifying questions that will most change which sub-areas and "
        "gaps are worth exploring here. For each, give 3-4 concrete, mutually-"
        "distinct suggested answers. Return ONLY a JSON array shaped like:\n"
        '[{"question": "...", "suggestions": ["...", "...", "..."]}]'
    )


def _static_questions(domain: str) -> list[IntakeQuestion]:
    """A solid domain-agnostic fallback set (used when the LLM can't help)."""
    return [
        IntakeQuestion(
            question=f"Who are you building in {domain} for first?",
            suggestions=["Individual consumers (B2C)", "Small businesses / SMB",
                         "Mid-market / enterprise (B2B)", "Developers / technical users"],
        ),
        IntakeQuestion(
            question="What does a win look like in the next 12 months?",
            suggestions=["A profitable lifestyle business", "Fast VC-scale growth",
                         "An acquisition target", "Validated traction, then decide"],
        ),
        IntakeQuestion(
            question="Any hard constraints to respect?",
            suggestions=["Regulated / compliance-heavy is OK", "Avoid regulated spaces",
                         "Solo-founder feasible only", "No heavy capital / hardware"],
        ),
        IntakeQuestion(
            question="Your unfair advantage / time horizon?",
            suggestions=["Deep domain expertise", "Strong distribution/audience",
                         "Technical depth (AI/infra)", "Nights-and-weekends for now"],
        ),
    ]


def _parse(raw: Any) -> list[IntakeQuestion]:
    out: list[IntakeQuestion] = []
    if not isinstance(raw, list):
        return out
    for obj in raw:
        if not isinstance(obj, dict):
            continue
        q = str(obj.get("question", "") or "").strip()
        if not q:
            continue
        sugg = obj.get("suggestions") or obj.get("options") or []
        suggestions = [str(s).strip() for s in sugg if str(s).strip()][:4] if isinstance(sugg, list) else []
        out.append(IntakeQuestion(question=q, suggestions=suggestions))
    return out[:5]


async def generate_intake_questions(
    domain: str, client: ClaudeClient, model: str
) -> list[IntakeQuestion]:
    """Generate 3-5 steering questions for ``domain``. NEVER raises.

    Uses the cheap model; degrades to a static set on any failure so the intake
    panel always has something to show.
    """
    try:
        result = await client.complete(
            _prompt(domain), system=_INTAKE_SYSTEM, max_turns=1, timeout=60, model=model
        )
        parsed = _parse(_extract_json_array(result.text or ""))
        return parsed or _static_questions(domain)
    except Exception:  # noqa: BLE001 - intake is best-effort; never break the flow.
        return _static_questions(domain)


def intake_context_block(intake: dict[str, str]) -> str:
    """Render intake answers as a compact steering block for prompts (or "")."""
    if not intake:
        return ""
    lines = [f"- {q.strip()} → {a.strip()}" for q, a in intake.items() if a and a.strip()]
    if not lines:
        return ""
    return "FOUNDER INTAKE (steer the exploration to honour these):\n" + "\n".join(lines)
