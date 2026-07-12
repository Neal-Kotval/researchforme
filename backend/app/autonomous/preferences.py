"""Preference distillation (Phase 4 H3, docs/strategy/phase234-build.md).

ONE cheap-model pass over the user's accumulated triage verdicts (interested /
passed + reasons + gap titles, cross-project) → a proposed
``learned_preferences`` text stored as the single ``ap_preferences`` row with
``status: "pending"``. The user reviews / edits / confirms it via
``GET|POST /api/preferences``; ONLY ``status == "active"`` text is ever
appended to :func:`~app.autonomous.intake.steering_context_block` output,
under a "LEARNED PREFERENCES (user-confirmed)" heading. Pending or dismissed
text is never injected into any prompt.

Honest degrade (NOT degrade-to-canned): distilled preferences steer every
future run, so a backend that can't produce a real distillation must say so.
If the LLM is unreachable or returns unusable output (the fixture backend's
canned gaps array, an empty string), :func:`distill_preferences` raises
:class:`PreferencesUnavailable` and the router turns that into a 503 with an
honest detail — never an invented preference.
"""

from __future__ import annotations

import json
from typing import Optional

from ..analysis.synthesize import _strip_fences
from ..llm.client import ClaudeClient
from .schemas import Node, Preferences, Project

# Distillation is a cheap-model pass (same tier as decompose / scout / digest).
DISTILL_MODEL: str = Project.model_fields["decompose_model"].default


class PreferencesUnavailable(RuntimeError):
    """No usable distillation could be generated — honest 503, never invented."""


_DISTILL_SYSTEM = """\
You distill a founder's taste from their own triage verdicts on market-gap
candidates. You are given every verdict they recorded — gap title, domain,
interested/passed, and their stated reason. Describe the pattern in what they
pass on and what they lean into, as 3-6 short imperative lines a market
explorer can obey (e.g. "Avoid consumer-social spaces — passed 4 as
too_crowded."). Ground EVERY line in the supplied verdicts; never invent a
preference they did not express. Output ONLY a JSON object, no prose, no
fences."""

_MAX_PREFS_CHARS = 2000


def _distill_prompt(triaged: list[tuple[Node, Optional[str]]]) -> str:
    payload = [
        {
            "title": node.title,
            "domain": domain or "",
            "triage": node.triage,
            "reason": node.triage_reason,
        }
        for node, domain in triaged[:60]
    ]
    return (
        "THE FOUNDER'S TRIAGE VERDICTS (the only evidence you may use):\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n\n"
        "Distill their taste. Return ONLY a JSON object shaped like:\n"
        '{"learned_preferences": "3-6 short lines, one per pattern, '
        'each grounded in the verdicts above"}'
    )


def _parse_distilled(raw: str) -> Optional[str]:
    """The proposed preferences text, or None on any shape problem.

    Rejects unparseable output and the fixture backend's canned gaps array
    (a list, not an object) — the caller degrades to an honest 503.
    """
    try:
        obj = json.loads(_strip_fences(raw or ""))
    except Exception:  # noqa: BLE001 - unparseable → caller raises Unavailable.
        return None
    if not isinstance(obj, dict):
        return None
    text = str(obj.get("learned_preferences", "") or "").strip()
    return text[:_MAX_PREFS_CHARS] or None


async def distill_preferences(
    triaged: list[tuple[Node, Optional[str]]],
    client: ClaudeClient,
    model: str = DISTILL_MODEL,
) -> str:
    """ONE cheap-model call → proposed preferences text (H3).

    Raises :class:`PreferencesUnavailable` when no backend can produce a real
    distillation (the fixture backend included). ``triaged`` must be non-empty
    — the router 400s before calling with zero verdicts.
    """
    if not triaged:
        raise ValueError("Cannot distill preferences from zero triage verdicts.")
    try:
        result = await client.complete(
            _distill_prompt(triaged),
            system=_DISTILL_SYSTEM,
            max_turns=1,
            timeout=90,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001 - honest 503, never invented.
        raise PreferencesUnavailable(
            f"The LLM backend could not distill preferences ({type(exc).__name__})."
        ) from exc
    text = _parse_distilled(result.text or "")
    if text is None:
        raise PreferencesUnavailable(
            f"The '{result.backend}' backend produced no usable preference "
            "distillation. Configure a real LLM backend and retry."
        )
    return text


def learned_preferences_block(store) -> str:
    """The user-confirmed preferences as a prompt block, or "".

    ONLY ``status == "active"`` text is ever rendered — a pending proposal or
    a dismissed row injects nothing. Degrades to "" on any store failure so
    prompt building never breaks over preferences.
    """
    try:
        prefs: Optional[Preferences] = store.get_preferences()
    except Exception:  # noqa: BLE001 - degrade, don't crash prompt building.
        return ""
    if prefs is None or prefs.status != "active":
        return ""
    text = (prefs.learned_preferences or "").strip()
    if not text:
        return ""
    return "LEARNED PREFERENCES (user-confirmed):\n" + text
