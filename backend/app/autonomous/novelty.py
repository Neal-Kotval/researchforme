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
    rationale: str = ""              # why this score — name the overlap or the gap


_SYSTEM = """\
You are a skeptical seed-stage analyst mapping a PROPOSED startup idea against the
real funded-startup landscape. Your job is to locate the idea in the space of
companies that ALREADY EXIST and already took money, then rate how much open room
is genuinely left on top of them.

Use web search for real. Find the CLOSEST real companies to the proposed idea —
YC-backed, VC-funded, or obvious well-known incumbents. Report up to THREE, nearest
first.

HARD RULES:
- DO NOT FABRICATE. If you are not confident a company is real, leave its "url"
  empty and say so in why_similar. A made-up incumbent that wrongly kills a real
  gap is the worst possible failure here. Never invent a plausible-sounding startup
  or a funding round you did not find.
- Rate the REMAINING OPEN SPACE, not how good the pitch sounds. A gap that sits on
  top of a funded company is LOW novelty regardless of how polished it is.
- Score honestly and calibrate the verdict to the score:
    "occupied"  (novelty ~0-25)  : a funded company is already doing essentially this.
    "adjacent"  (novelty ~25-55) : funded companies are close; the wedge overlaps theirs.
    "open"      (novelty ~55-80) : neighbors exist but real room is left on a distinct angle.
    "novel"     (novelty ~80-100): search genuinely finds no close funded company.
- In rationale, NAME the specific overlap (which company, what they already own) or
  name the specific open space. Vague rationales are useless.

Return ONLY a single top-level JSON object (no prose, no code fences):
{
  "nearest_known": [
    {"name": "", "url": "", "why_similar": "what they already do that overlaps", "funded": "funding status if known, else empty"}
  ],
  "novelty_0_100": 0-100,
  "verdict": "occupied|adjacent|open|novel",
  "rationale": "why this score — name the company you overlap or the space that's open"
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
