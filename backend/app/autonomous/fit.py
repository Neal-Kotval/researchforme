"""Founder-fit scoring — "is this space for YOU" (orthogonal to viability).

Viability (``pressure.score_viability``) measures the *market*: demand, moat,
why-now. It says nothing about whether THIS founder — with their skills,
advantages, constraints, and time horizon — can actually attack the space. This
module makes ONE cheap-model call per scored gap that grades exactly that,
grounded in the project's :func:`steering_context_block`.

Contract (degrade-don't-crash like everything else):

* Empty steering → ``(None, "")`` without spending a call. ``None`` means "no
  steering provided or scoring unavailable" — a fit is NEVER fabricated.
* LLM failure or unparseable output → ``(None, "")``. The fixture backend
  returns a gaps array, not a fit object, so it degrades deterministically.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ..analysis.synthesize import _strip_fences
from ..llm.client import ClaudeClient
from ..schemas import Gap
from .intake import steering_context_block
from .pressure import _gap_payload

_FIT_SYSTEM = """\
You are the founder-fit judge of "Market Gap Finder". A market gap has already
been scored for MARKET quality (viability). Your job is entirely different: grade
how well it fits THIS SPECIFIC FOUNDER — their skills, unfair advantages, hard
constraints, and time horizon, as given in their steering context.

# RULES
- Fit is about the FOUNDER, not the market. A great market the founder cannot
  attack — wrong skills, or it violates a HARD constraint they actually stated —
  scores LOW.
- AMBITION IS NEVER A FIT PENALTY. Fit measures founder↔idea match, NOT
  founder↔idea-TODAY match. Capital, headcount, years, fabs, and complexity are
  the founder's problem to resource, and they decide the timeline — not you. An
  idea that needs $50M and twelve people is not a bad fit for a founder who has
  neither today; it is a fundable idea. Never dock fit for scale, cost, or
  duration. This rule exists because this lens used to score LOW for "needs
  capital/time they don't have", and fit multiplies into the tree's primary sort
  ("fit × viability") — so it quietly buried every large idea before the founder
  ever read it.
- Do NOT reward an idea for being modest. A safe, small idea squarely on their
  advantages is not automatically high fit; it is often just small.
- Do not restate or re-score market quality — the viability number already did.
- Ground the reason in the steering context: name the specific advantage or
  constraint that drove the score.

# OUTPUT CONTRACT — STRICT
Return ONLY a JSON object (no prose, no markdown fences), EXACTLY:
{"fit": <integer 0-100>, "fit_reason": "1-2 sentences naming which advantage/constraint drove it"}\
"""


def _parse_fit(text: str) -> tuple[Optional[int], str]:
    """Extract ``(fit, fit_reason)`` from model output, or ``(None, "")``."""
    out = _strip_fences((text or "").strip())
    obj: Any = None
    try:
        obj = json.loads(out)
    except Exception:  # noqa: BLE001 - salvage the first {...} span, then give up.
        start, end = out.find("{"), out.rfind("}")
        if 0 <= start < end:
            try:
                obj = json.loads(out[start : end + 1])
            except Exception:  # noqa: BLE001
                obj = None
    if not isinstance(obj, dict):
        return None, ""
    raw = obj.get("fit")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, ""
    fit = int(max(0, min(100, round(raw))))
    reason = str(obj.get("fit_reason", "") or "").strip()[:400]
    return fit, reason


def _prompt(steering: str, gap: Gap, viability: Optional[int]) -> str:
    return (
        "FOUNDER STEERING CONTEXT (grade fit against THIS, and only this):\n"
        f"{steering}\n\n"
        "THE GAP (already market-scored — do NOT re-grade market quality):\n"
        f"{json.dumps(_gap_payload(gap), indent=2)}\n\n"
        f"MARKET VIABILITY (for reference only): {viability if viability is not None else 'unscored'}/100.\n\n"
        "How well does this gap fit this specific founder? Return ONLY the strict "
        'JSON object: {"fit": 0-100, "fit_reason": "..."}'
    )


async def score_founder_fit(
    gap: Gap,
    viability: Optional[int],
    project: Any,
    client: ClaudeClient,
    model: str,
) -> tuple[Optional[int], str]:
    """Grade founder fit for ``gap`` → ``(fit 0..100, reason)``. NEVER raises.

    Skips the call entirely (→ ``(None, "")``) when the project has no steering
    context — the same emptiness check every other steered step uses. On any LLM
    or parse failure it also returns ``(None, "")``; null means "no steering
    provided or scoring unavailable", never a fabricated score.
    """
    steering = steering_context_block(project)
    if not steering:
        return None, ""
    try:
        result = await client.complete(
            _prompt(steering, gap, viability),
            system=_FIT_SYSTEM,
            max_turns=1,
            timeout=90,
            model=model,
        )
        return _parse_fit(result.text or "")
    except Exception:  # noqa: BLE001 - fit is a bonus lens; never break scoring.
        return None, ""
