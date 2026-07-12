"""Preflight intake — a short, sharp set of clarifying questions asked *before*
an exploration starts, so the tree is steered by who the builder is and what a
win looks like (SPEC feature A).

Degrade-don't-crash like everything else: if the LLM is unavailable or its output
won't parse, we fall back to a solid static question set derived from the domain,
so the intake step always returns something useful.
"""

from __future__ import annotations

from typing import Any

import json

from ..analysis.synthesize import _extract_json_array, _strip_fences
from ..llm.client import ClaudeClient
from .schemas import IntakeQuestion, SortedResearch

_INTAKE_SYSTEM = """\
You are the intake step of an autonomous market-gap explorer. Before it spends
hours exploring a domain, you ask the founder a SHORT, SHARP set of clarifying
questions that will actually change what gets explored — who they are, their
constraints, what "a win" looks like, sub-segments to favor or avoid, and their
time/skill horizon. Ask 3-5 questions. Each must be decision-relevant (it should
steer scope, not be trivia) and come with 3-4 concrete suggested answers plus the
implicit option to free-type. Output ONLY a JSON array, no prose, no fences."""


def _prompt(domain: str, brief: str = "") -> str:
    ctx = ""
    if brief and brief.strip():
        ctx = (
            "\n\nWHAT THE FOUNDER ALREADY TOLD YOU (build on this — do NOT ask what "
            f"they've already answered):\n{brief.strip()[:4000]}"
        )
    return (
        f"DOMAIN THE FOUNDER WANTS TO EXPLORE: {domain}{ctx}\n\n"
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
    domain: str, client: ClaudeClient, model: str, brief: str = ""
) -> list[IntakeQuestion]:
    """Generate 3-5 steering questions for ``domain``. NEVER raises.

    Uses the cheap model; degrades to a static set on any failure so the intake
    panel always has something to show. An optional ``brief`` lets the questions
    build on context the founder already supplied instead of re-asking it.
    """
    try:
        result = await client.complete(
            _prompt(domain, brief), system=_INTAKE_SYSTEM, max_turns=1, timeout=60, model=model
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


def _bullets(label: str, items: list[str]) -> list[str]:
    vals = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not vals:
        return []
    return [f"{label}: " + "; ".join(vals)]


def steering_context_block(project: Any) -> str:
    """Render the full founder steer (brief + structured fields + intake) as one
    compact block for any prompt, or "" when nothing was supplied.

    Accepts the ``Project`` so every LLM step (decompose / synthesize / pressure)
    can honour the same context. Degrades to "" when empty — behaviour is then
    identical to before steering existed.
    """
    steering = getattr(project, "steering", None)
    parts: list[str] = []

    if steering is not None:
        brief = (getattr(steering, "brief", "") or "").strip()
        if brief:
            parts.append("FOUNDER BRIEF (their own words — weight this heavily):\n" + brief)
        parts += _bullets("UNFAIR ADVANTAGES", getattr(steering, "advantages", []))
        parts += _bullets("HARD CONSTRAINTS (respect these)", getattr(steering, "constraints", []))
        parts += _bullets("AVOID (do not explore)", getattr(steering, "avoid", []))
        horizon = (getattr(steering, "time_horizon", "") or "").strip()
        if horizon:
            parts.append(f"TIME HORIZON: {horizon}")
        research = (getattr(steering, "research", "") or "").strip()
        if research:
            snippet = research if len(research) <= 4000 else research[:4000] + "…"
            parts.append("FOUNDER'S OWN RESEARCH (mine this for leads, don't ignore it):\n" + snippet)

    intake_block = intake_context_block(getattr(project, "intake", {}) or {})
    if intake_block:
        parts.append(intake_block)

    # H3: distilled preferences the user CONFIRMED (status == "active") steer
    # every prompt; a pending proposal or dismissed row injects nothing.
    # Lazy import + broad guard: prompt building must never break over prefs.
    try:
        from .preferences import learned_preferences_block
        from .store import get_store

        prefs_block = learned_preferences_block(get_store())
    except Exception:  # noqa: BLE001 - degrade to no-prefs, never crash a prompt.
        prefs_block = ""
    if prefs_block:
        parts.append(prefs_block)

    if not parts:
        return ""
    return "== FOUNDER STEERING (honour all of this) ==\n" + "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Bulk-paste research → a ready-to-launch auto job                            #
# --------------------------------------------------------------------------- #
_SORT_SYSTEM = """\
You are the intake sorter of an autonomous market-gap explorer. A founder pastes
a wall of their OWN research — notes, links, half-formed theses, competitor lists,
Reddit quotes, whatever. Your job is to sort it into a clean, ready-to-run
exploration job: infer the single best DOMAIN to explore, the concrete
SUB-SEGMENTS the research already points at, and a tight BRIEF that distills the
founder's angle/thesis/constraints so the explorer is steered by it. Extract; do
not invent. If the paste is thin, still return your best structured guess.
Output ONLY a JSON object, no prose, no fences."""


def _sort_prompt(text: str) -> str:
    return (
        "FOUNDER'S PASTED RESEARCH:\n"
        f"{text.strip()[:16000]}\n\n"
        "Sort it into a launchable job. Return ONLY a JSON object shaped like:\n"
        '{"domain": "the single best domain to explore (short)", '
        '"sub_segments": ["concrete", "segments", "the research points at"], '
        '"brief": "a tight distillation of the founder\'s angle, thesis, '
        'advantages and constraints — 2-5 sentences, their POV preserved"}'
    )


def _parse_sorted(raw: Any, fallback_text: str) -> SortedResearch:
    obj = raw if isinstance(raw, dict) else {}
    domain = str(obj.get("domain", "") or "").strip()
    subs = obj.get("sub_segments") or obj.get("segments") or []
    sub_segments = [str(s).strip() for s in subs if str(s).strip()][:8] if isinstance(subs, list) else []
    brief = str(obj.get("brief", "") or "").strip()
    # Always keep the raw paste as `research` so nothing the founder gave is lost.
    return SortedResearch(
        domain=domain,
        sub_segments=sub_segments,
        brief=brief or fallback_text.strip()[:2000],
        research=fallback_text.strip()[:20000],
    )


async def sort_research(text: str, client: ClaudeClient, model: str) -> SortedResearch:
    """Sort a raw research paste into a launchable ``SortedResearch``. NEVER raises.

    Degrades gracefully: on any LLM/parse failure the whole paste is preserved as
    ``research`` (and echoed into ``brief``) with an empty domain, so the UI can
    still let the founder name the domain and launch.
    """
    try:
        result = await client.complete(
            _sort_prompt(text), system=_SORT_SYSTEM, max_turns=1, timeout=90, model=model
        )
        out = (result.text or "").strip()
        parsed: Any = None
        try:
            parsed = json.loads(_strip_fences(out))
        except Exception:  # noqa: BLE001 - fall back to array-scan-then-give-up.
            arr = _extract_json_array(out)
            parsed = arr[0] if isinstance(arr, list) and arr and isinstance(arr[0], dict) else None
        return _parse_sorted(parsed, text)
    except Exception:  # noqa: BLE001 - sorter is best-effort; never break the flow.
        return _parse_sorted(None, text)
