"""Semantic duplicate detection — the synonym case lexical matching cannot reach.

Lexical dedup (``engine.is_duplicate_title``) catches duplicates that share
words: "Reward-Function CI" vs "Verifier CI" (Jaccard 0.600). It cannot catch
duplicates that share only a *concept*, because their surface forms are
synonyms. Observed, in one live run of a single tree:

    The Underwriters Lab for Reward Models          (44)
    The Underwriters Laboratories of RL Rewards     (38)
    The Rating Agency for RL Reward Functions       (38)
    Moody's for RL Environments                     (29)
    Consumer Reports for RL Environments            (23)

Five names for "an independent body that rates RL reward functions". Pairwise
token overlap is near zero — "Moody's" and "Consumer Reports" share nothing with
"Underwriters Lab" — so no lexical threshold can merge them without also merging
genuinely distinct ideas. They came from five different branches, each blind to
the others, and each cost a full Opus pressure test and a slot in the shortlist.

So: ask a model. One cheap call per candidate, against the titles+theses already
in the tree, BEFORE the expensive pressure test. This is the one dedup stage that
can reason about meaning.

Design rules:
* CHEAP MODEL. This runs per candidate; it uses the project's decompose model
  (Haiku-class), not the synthesis model.
* FAILS OPEN. Any error, timeout, or unparseable answer keeps the gap. A false
  merge silently destroys a real idea and the founder never learns it existed —
  strictly worse than a duplicate they can see and ignore.
* SAME IDEA, NOT SAME AREA. Two ideas in one sub-segment are not duplicates;
  two ideas a founder would pick between as "the same company" are.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ..llm.client import ClaudeClient

_SYSTEM = """\
You judge whether a proposed startup idea is THE SAME IDEA as one already on a
list — not whether it is in the same market.

SAME IDEA means a founder could not build both as separate companies: the buyer,
the artifact sold, and the core mechanic are the same, and the difference is
only naming, framing, or scope. Synonyms are the common case — "Moody's for X",
"The Rating Agency for X", "Consumer Reports for X", and "The Underwriters Lab
for X" are ONE idea wearing four names. So are "Factory" and "Foundry", "CI" and
"fuzzing harness", "index" and "rating".

DIFFERENT IDEAS share a market but differ in what is actually sold or to whom:
"sell the environments" vs "rate other people's environments" vs "insure against
their failure" are three companies, not one — the artifact differs.

Bias toward DIFFERENT when genuinely unsure. Wrongly merging deletes an idea the
founder never gets to see; wrongly keeping one costs them a scroll.

Return ONLY a JSON object, no prose, no fences:
{"duplicate_of": <0-based index into the list, or null>, "why": "<8 words max>"}
"""


def _prompt(title: str, thesis: str, existing: list[tuple[str, str]]) -> str:
    lines = ["CANDIDATE:", f"  title: {title}", f"  thesis: {thesis}", "", "EXISTING:"]
    for i, (t, th) in enumerate(existing):
        lines.append(f"  [{i}] {t}")
        if th:
            lines.append(f"      {th[:180]}")
    lines.append("")
    lines.append(
        "Is the CANDIDATE the same idea as any EXISTING entry? "
        'Answer {"duplicate_of": <index or null>, "why": "..."}'
    )
    return "\n".join(lines)


_JSON_RE = re.compile(r"\{.*\}", re.S)


def _parse(text: str, n: int) -> Optional[int]:
    """Extract a valid 0..n-1 index, or None. Anything unparseable -> None (keep)."""
    if not text:
        return None
    match = _JSON_RE.search(text.strip())
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except Exception:  # noqa: BLE001 - a malformed answer keeps the gap
        return None
    idx = obj.get("duplicate_of")
    if idx is None or isinstance(idx, bool) or not isinstance(idx, int):
        return None
    return idx if 0 <= idx < n else None


# Cap the comparison list: the prompt must stay small and cheap, and the most
# recently proposed gaps are the ones a fresh branch is most likely to echo.
_MAX_EXISTING = 30


async def semantic_duplicate_of(
    title: str,
    thesis: str,
    existing: list[tuple[str, str]],
    client: ClaudeClient,
    model: str,
) -> Optional[int]:
    """Index of the existing idea this candidate duplicates, or None.

    Never raises: on any failure the candidate is kept (returns None). See the
    module docstring for why the failure mode is deliberately asymmetric.
    """
    if not title or not existing:
        return None
    pool = existing[-_MAX_EXISTING:]
    offset = len(existing) - len(pool)
    try:
        result = await client.complete(
            _prompt(title, thesis or "", pool),
            system=_SYSTEM,
            model=model,
            max_turns=1,
            timeout=45,
        )
    except Exception:  # noqa: BLE001 - dedup must never break an expansion
        return None
    if getattr(result, "backend", "") == "fixture":
        # A fixture answer is canned freelancer-finance JSON, not a judgement.
        return None
    idx = _parse(result.text or "", len(pool))
    return None if idx is None else idx + offset
