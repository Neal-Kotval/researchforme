"""Novelty-distance scoring against the FUNDED startup landscape (the anti-obvious gate).

The engine's core failure mode is mode-collapse: an LLM asked for "market gaps"
converges on the ideas everyone already thought of, which are precisely the ones
that are already funded and occupied. A gap can read beautifully — sharp wedge,
clean thesis, five-out-of-five scores — and still be a company that raised a
Series A eighteen months ago. Nothing in decomposition, synthesis, or the red
team actually checks the live funded landscape, because our fixed adapters
(arXiv/GitHub/HN/jobs) structurally cannot see who took money.

This module is that check. It hands ONE gap to a web-enabled model with a single
job: find the CLOSEST real, funded companies to this idea, then rate honestly how
much open space is left on top of them. A gap sitting on a YC/funded incumbent
scores LOW novelty no matter how good it sounds; a gap the search cannot place
near anything real scores high. The score is distance-to-nearest-competitor, not
polish.

Two rules keep it honest:

* Do NOT fabricate. If the model is unsure a company is real, it leaves the url
  empty rather than inventing a plausible-sounding startup — a hallucinated
  incumbent would wrongly kill a real gap.
* Rate the REMAINING space, not the idea's charm. "occupied" means someone funded
  is already doing this; "novel" means the search genuinely came up empty.

Degrades safely like every operator: a fixture backend, any exception, or an
unparseable answer yields None. A failed novelty scan must never break a run — a
gap simply goes unscored on this axis rather than taking the whole run down.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from ..analysis.synthesize import _strip_fences
from ..llm.client import ClaudeClient
from ..schemas import Gap

logger = logging.getLogger("gapfinder.novelty")


class NearestCompany(BaseModel):
    """One real, ideally-funded company that sits close to the proposed gap."""

    name: str = ""
    url: str = ""                    # empty when the model is unsure it's real
    why_similar: str = ""            # the overlap — what they already do here
    funded: str = ""                 # funding status if known (e.g. "YC S23", "Series A $12M")


class NoveltyScan(BaseModel):
    """The distance between a gap and the nearest funded incumbent."""

    nearest_known: list[NearestCompany] = Field(default_factory=list)  # up to 3 closest
    novelty_0_100: int = Field(ge=0, le=100)  # 0 = identical to a funded incumbent, 100 = white space
    verdict: str = ""                # "occupied" | "adjacent" | "open" | "novel"
    structural_risk: str = ""        # why the open space may be a trap (adverse selection, correlated
                                     # risk, regulatory/capital barrier, commoditizing input, low demand)
    rationale: str = ""              # why this score — name the overlap or the gap


_SYSTEM = """\
You are a skeptical seed-stage analyst mapping a PROPOSED startup idea against the
real funded landscape. Locate the idea among what ALREADY EXISTS, then rate how
much open room is genuinely left — AND whether the room that's left is empty for a
STRUCTURAL reason (a trap), not an oversight.

You exist because a cheaper first-pass check keeps calling occupied spaces "open":
it finds the topically-similar STARTUPS but misses the DIRECT incumbent shipping
the exact business model, and never asks why a plausible space is still empty.
Fix both.

Use web search for real. Two searches, not one:
1. THE EXACT BUSINESS MODEL — is someone already selling THIS product to THIS buyer
   with THIS revenue shape? Search the wedge and the model, not just the topic.
   Crucially, incumbents often reach the market through a PARTNER, so the product
   ships under a big brand's distribution (e.g. a broker + a managing general
   underwriter; a platform + an OEM; an incumbent + a startup it white-labels).
   "Marsh IP Protect" is Marsh + Ambridge — that is the idea already shipping, even
   though no standalone startup matches. Hunt these pairings. A company that OWNS
   the distribution or capacity this plan must RENT is a countermove-ready
   incumbent → occupied, not adjacent.
2. THE STRUCTURAL "WHY EMPTY" — if little is found, ask why. Is the space empty
   because it is a trap a knowledgeable operator avoids? Look for: adverse
   selection (the keenest buyers are the worst risks), correlated / fat-tail
   exposure, regulatory or capital barriers, commoditizing inputs (the core tech is
   becoming free), or genuinely low-frequency demand. If a domain expert would say
   "that's empty because it doesn't work," novelty is NOT a virtue — say so.

HARD RULES:
- DO NOT FABRICATE. If unsure a company is real, leave its "url" empty and say so.
  A made-up incumbent that wrongly kills a real gap is the worst failure here.
- Rate the REMAINING, DEFENSIBLE open space — not how good the pitch sounds, and
  not raw whitespace. Whitespace that is a structural trap scores LOW, not high.
- If the EXACT model is already shipping (even under an incumbent's brand via a
  partner), verdict is "occupied" regardless of what the startup search returned.
- Calibrate verdict to score:
    "occupied"  (~0-25)  : the exact product/model already ships (standalone or via a partner).
    "adjacent"  (~25-55) : funded players are close; the wedge overlaps theirs or they hold the distribution.
    "open"      (~55-80) : neighbors exist but real, DEFENSIBLE room is left on a distinct angle.
    "novel"     (~80-100): no close player AND no structural reason it's empty.
- NAME the specific overlap (which company, what they own — distribution, capacity,
  data) or the specific defensible opening. Vague rationales are useless.

Return ONLY a single top-level JSON object (no prose, no code fences):
{
  "nearest_known": [
    {"name": "", "url": "", "why_similar": "what they already own that overlaps — incl. distribution/capacity", "funded": "funding status if known, else empty"}
  ],
  "novelty_0_100": 0-100,
  "verdict": "occupied|adjacent|open|novel",
  "structural_risk": "if the open space is empty for a structural reason (adverse selection, correlated risk, regulatory/capital barrier, commoditizing input, low-frequency demand), name it — else empty string",
  "rationale": "why this score — name the company/distribution you overlap or the defensible space that's open"
}"""


def _extract_json_object(text: str) -> Optional[dict]:
    """Pull the first top-level JSON object out of arbitrary model output.

    Tries whole-text and fence-stripped parses, then a string-aware brace scan for
    the outermost {...}. Returns a dict on success, else None. Mirrors
    ``synthesize._extract_json_array`` but for a single object result.
    """
    if not text:
        return None

    for cand in (text, _strip_fences(text)):
        cand = cand.strip()
        if not cand:
            continue
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    src = _strip_fences(text)
    start = src.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(src)):
        ch = src[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(src[start : i + 1])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _gap_brief(gap: Gap) -> str:
    """Compact description of the gap to place against the funded landscape."""
    c = gap.company
    lines = [
        f"===== PROPOSED IDEA: {gap.title} =====",
        f"thesis: {gap.thesis}",
        f"wedge: {gap.wedge}",
        f"why_now: {gap.why_now}",
        f"product: {c.product if c else '(none stated)'}",
        f"buyer (ICP): {c.icp if c else '(none stated)'}",
        f"business_model: {c.business_model if c else '(none stated)'}",
        f"moat: {c.moat if c else '(none stated)'}",
        f"sub_segment: {gap.sub_segment}",
        f"tags: {', '.join(gap.tags or []) or '(none)'}",
    ]
    return "\n".join(lines)


async def novelty_scan(
    gap: Gap, client: ClaudeClient, model: str
) -> NoveltyScan | None:
    """Place a gap against the funded startup landscape and score its open space.

    Web-enabled single pass: find the nearest real, funded companies and rate how
    much room is left. Returns a validated ``NoveltyScan``, or None on a fixture
    backend, any error, or an unparseable/invalid answer — a failed scan leaves the
    gap unscored on this axis, it never breaks a run.
    """
    prompt = (
        "Place this proposed idea against the real, funded startup landscape. "
        "Search for the closest companies that already exist, then rate the open "
        "space left on top of them.\n\n"
        + _gap_brief(gap)
    )
    try:
        result = await client.complete(
            prompt, system=_SYSTEM, model=model, max_turns=6, timeout=120, web=True,
        )
    except Exception:  # noqa: BLE001 - a failed novelty scan must never break a run
        return None
    if getattr(result, "backend", "") == "fixture":
        return None
    obj = _extract_json_object(result.text or "")
    if obj is None:
        return None
    # Keep only the three nearest — the prompt asks for up to three, but trust the
    # contract, not the model.
    if isinstance(obj.get("nearest_known"), list):
        obj["nearest_known"] = obj["nearest_known"][:3]
    try:
        return NoveltyScan.model_validate(obj)
    except ValidationError:
        return None
