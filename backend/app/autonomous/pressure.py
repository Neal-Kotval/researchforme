"""Adversarial pressure testing → viability score (SPEC §5).

A ``GapCandidate`` produced by the synthesis pipeline is only a *hypothesis*.
Before it earns a viability score (and a ⭐), it is dragged through a gauntlet of
independent **kill-lenses** — each one trying to *destroy* the gap, not confirm
it (the adversarial-verify pattern). A gap that walks out the far side of six
lenses still standing is a very different animal from one nobody tried to kill.

Three things happen here:

1. **The lenses.** Six sharp, single-purpose adversaries (``LENSES``): is the
   white space *empty for a reason*? could an *incumbent* close it in a weekend?
   is the demand a *mirage* of loud complaints with no wallet? is the *why-now*
   hand-waved? is there any *moat* once it works? is it *just a feature*, not a
   company? Rigor picks how many run:
   ``deep`` = all 6, ``standard`` = 4, ``light`` = the 3 cheapest.

2. **The run.** ``pressure_test`` hands the gap + its branch context to the LLM
   (optionally with the same ``search_*`` corroboration tools synthesis uses) and
   asks for a strict JSON array of per-lens verdicts, parsed with the same robust
   extractor ``synthesize`` uses. It **degrades, never raises**: if the call
   fails or nothing parses, it synthesizes a *neutral light* PressureTest derived
   purely from the gap's own 1..5 scores, so scoring still works with NO real LLM.
   Lenses the model *skipped* (partial output) fill as honest ``weakens`` and the
   recorded rigor is downgraded to what was actually evaluated — a gap never
   earns free survivals or rigor credit from its own self-reported scores.

3. **The score.** ``score_viability`` blends the five base scores with the lens
   outcomes — rewarding survivals, penalizing weakens, and hard-penalizing any
   kill — into a 0..100 viability plus a ``Confidence`` that reflects how hard the
   gap was actually tested (rigor + evidence corroboration). A lightly-tested 80
   is therefore visibly distinct from a battle-tested 80.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ..analysis.rank import composite_score
from ..analysis.synthesize import (  # robust JSON-array parser + URL canonicalizer
    _extract_json_array,
    _norm_url,
)
from ..llm.client import ClaudeClient, ToolSpec
from ..schemas import Evidence, Gap, Weights
from .schemas import Confidence, LensVerdict, PressureTest, TestRigor


# --------------------------------------------------------------------------- #
# The six kill-lenses. Each is an *independent adversary* whose job is to       #
# prove its own kill-question "yes". Order is canonical (SPEC §5 table); the    #
# rigor-based subset is chosen from `_LENS_PRIORITY`, cheapest/most-decisive    #
# first, so light ⊂ standard ⊂ deep.                                            #
# --------------------------------------------------------------------------- #
LENSES: list[dict] = [
    {
        "key": "empty_for_a_reason",
        "prompt": (
            "EMPTY-FOR-A-REASON. Try to prove this white space is empty because "
            "it is a TRAP, not an opportunity: a regulatory wall, no real "
            "willingness to pay, a channel that can't be reached economically, or "
            "a structural reason nobody can serve it. If you can build a credible "
            "case that the space is empty FOR A REASON, verdict=kills.\n"
            "CRITICAL — 'NOT EMPTY' IS NOT A SURVIVAL. This lens tests the gap's "
            "white-space premise. If you discover the space is not empty at all "
            "but CROWDED — funded companies already shipping this — then the "
            "premise is false and the gap is worse off, not better. Do NOT return "
            "survives in that case: return weakens, or kills if well-funded "
            "players have already shipped substantially what this proposes to a "
            "buyer who can already buy it. Reserve survives for a space that is "
            "genuinely open AND not a trap. Never award a survival on a finding "
            "your own argument describes as bad news for the gap."
        ),
    },
    {
        "key": "incumbent_countermove",
        "prompt": (
            "INCUMBENT COUNTER-MOVE. Try to prove a well-resourced incumbent "
            "could close this gap in a weekend if it actually mattered — a config "
            "flag, a checkbox feature, a trivial extension of a product they "
            "already ship. If the wall against incumbents is that thin, "
            "verdict=kills; if they'd have to fight their own model, verdict=weakens."
        ),
    },
    {
        "key": "demand_mirage",
        "prompt": (
            "DEMAND MIRAGE. Try to prove the 'demand' is just loud complaints with "
            "no willingness to pay — venting, a nice-to-have, or a problem people "
            "won't actually open their wallet for. Separate real signal from noise. "
            "If the demand is a mirage, verdict=kills. But a mirage must SHIMMER: "
            "killing here requires evidence of demand that exists and does not "
            "convert. Silence is not a mirage — if the demand-bearing sources were "
            "mock/failed/empty, or would never have covered this buyer anyway, the "
            "demand is UNMEASURED: cap at weakens and name the validation that "
            "would settle it."
        ),
    },
    {
        "key": "why_now_fragility",
        "prompt": (
            "WHY-NOW FRAGILITY. Try to prove the enabling 'why now' shift is "
            "hand-waved or does not really exist — that nothing concrete changed "
            "recently to make this newly feasible or newly urgent. If the why-now "
            "collapses under scrutiny, verdict=kills; if it is real but soft, "
            "verdict=weakens."
        ),
    },
    {
        "key": "moat",
        "prompt": (
            "MOAT / DEFENSIBILITY. Assume it works — now try to prove anyone could "
            "copy it instantly, that there is no durable advantage (proprietary "
            "data, network effects, distribution, switching cost, or hard tech). "
            "If it is trivially copyable, verdict=weakens; if success actively "
            "invites a fatal fast-follow, verdict=kills."
        ),
    },
    {
        "key": "just_a_feature",
        "prompt": (
            "JUST A FEATURE, NOT A COMPANY. Try to prove this is merely a *feature* "
            "— not a standalone business: it has no room to expand past a single "
            "capability, no independent business model, and is the kind of thing a "
            "user would reasonably expect bundled FREE into a product they already "
            "pay for. Attack the `company` framing directly: is the product a real "
            "company or a thin add-on? Is the expansion_path (wedge→product→"
            "platform) credible or hand-waved? If it can only ever be a feature of "
            "someone else's product, verdict=kills; if it's a company but a thin "
            "one with a shaky path to scale, verdict=weakens."
        ),
    },
    {
        "key": "venture_scale",
        "prompt": (
            "VENTURE SCALE / CEILING. Assume it works and wins its wedge — now try "
            "to prove it CANNOT become a large company: it is structurally a small "
            "or lifestyle business, not a venture-scale one. Reason about the real "
            "ceiling: how many buyers exist WORLDWIDE (dozens? thousands? "
            "millions?), what each realistically pays, and whether there's a "
            "credible expansion surface beyond the wedge (adjacent buyers, "
            "up-market, a platform underneath). A great product for 200 customers "
            "at $30k is a fine business but NOT a venture bet. Ground this — do NOT "
            "invent a TAM number; reason from the named ICP and expansion_path. If "
            "the market structurally tops out small (a few million $/yr ceiling, no "
            "expansion path), verdict=kills; if it could plausibly reach tens of "
            "millions but a nine-figure outcome is a stretch, verdict=weakens; if "
            "there is a credible path to a large company, verdict=survives and say "
            "concretely why the ceiling is high."
        ),
    },
]

# Selection priority (distinct from the canonical display order above). The
# cheapest, most-decisive lenses come first so `light` runs the 2 that most
# often kill a bad gap; each higher rigor is a superset.
_LENS_PRIORITY: tuple[str, ...] = (
    "demand_mirage",
    "just_a_feature",
    "venture_scale",       # can this be big? — the question YC actually asks
    "incumbent_countermove",  # who already holds the beachhead? — see below
    "empty_for_a_reason",
    "why_now_fragility",
    "moat",
)

_LENS_BY_KEY: dict[str, dict] = {lens["key"]: lens for lens in LENSES}

# How many lenses each rigor level runs. `standard` runs the venture-scale lens
# (slot 3) so even a mid-rigor test asks whether the idea can be big, and the
# incumbent lens (slot 4) so it also asks who is already standing there.
#
# The incumbent promotion is a correction, not a preference. It sat at slot 6 and
# so never ran below `deep` — meaning the DEFAULT run never asked "could a funded
# rival close this?". A power-fleet-economics gap scored 83 while a rival had
# raised $68M from (among others) NVIDIA's own venture arm and shipped inside
# NVIDIA's Vera Rubin DSX reference design. That is an entry-point kill, and the
# only lens shaped to catch it was switched off by default.
_RIGOR_COUNT: dict[str, int] = {"light": 3, "standard": 5, "deep": 7}

_VALID_VERDICTS = frozenset({"survives", "weakens", "kills", "unmeasured"})


# --------------------------------------------------------------------------- #
# System prompt — the adversarial gauntlet's "brain".                          #
# --------------------------------------------------------------------------- #
_PRESSURE_SYSTEM = """\
You are the RED TEAM of "Market Gap Finder". A candidate market gap has been
proposed. Your job is NOT to like it — it is to try, in good faith and with
evidence, to KILL it. You wear several independent adversarial lenses, one at a
time, and for each you attempt to prove that lens's kill-question is TRUE.

# RULES OF ENGAGEMENT
- Each lens is independent. Reason it on its own merits; do not let one lens's
  verdict soften another. A gap only earns a high score by surviving lenses that
  genuinely tried to destroy it.
- Be specific and structural. "It might be hard" is worthless. Name the exact
  regulatory wall, the exact incumbent feature, the exact reason the demand won't
  convert to dollars. Generic doubts do not kill a gap.
- NO FABRICATION. Any Evidence.url you cite must come from the gap's own evidence,
  the branch context you were given, or a tool result you actually fetched. Never
  invent a URL, a quote, a competitor, or a statistic. If you can't ground a
  kill, downgrade your verdict honestly.
- Use tools (search_reddit / search_arxiv / search_hackernews / search_github /
  search_newsletters, and web_search / web_fetch for the OPEN web) SPARINGLY and
  only to corroborate a specific kill or rescue (e.g. "is the demand real?", "did
  the enabling shift actually happen?", "is there ALREADY a company doing this?").
  web_search is how you find a competitor that has a marketing site but no GitHub
  repo or arXiv paper — check it before concluding the field is open. Open a
  suspected competitor's actual site with web_fetch rather than guessing. If no
  tools are available, reason only from what you were given.
- ABSENCE OF EVIDENCE IS NOT EVIDENCE OF ABSENCE. A source that failed, was
  served from fixtures ("Mock sources" in the context), returned nothing, or
  simply does not cover this domain tells you NOTHING about the market. You may
  NOT kill a gap on a silence you cannot attribute to the market itself. Before
  any "kills" verdict that rests on missing signal, ask: would this source have
  carried this signal if it were real? Consumer forums do not discuss B2B
  infrastructure; app-store reviews do not cover developer tooling; a 403'd
  fetch measures the fetcher, not the demand. If the honest state is UNMEASURED,
  return verdict="unmeasured" and name the specific check that WOULD settle it.
  Killing an unmeasured gap is the single worst error you can make: it destroys a
  real opportunity and teaches the founder nothing. A gap you cannot measure is a
  gap to go validate, not a gap to bury.
- "SURVIVES" MEANS YOU TRIED AND FAILED TO KILL IT — NOT THAT YOU DIDN'T LOOK.
  A survival is a positive claim: you went looking for the kill and the gap held
  up anyway. If you did not actually check, the verdict is "unmeasured", not
  "survives". This distinction is the whole point: an unchecked gap and a
  battle-tested one must never score the same. Before returning "survives", ask
  yourself what you actually did to try to kill it, and say so in the argument.
  For any competitive lens, "I found no competitor" earns "survives" ONLY if you
  ran a real search for one; absent that, it is "unmeasured".

# VERDICTS (per lens)
- "kills"      — you built a credible, specific case that this lens's kill-question
                 is TRUE; the gap is fatally wounded on this axis.
- "weakens"    — the lens lands a real hit but not a fatal one; a genuine concern.
- "survives"   — you ATTEMPTED the kill and the gap withstood it; say concretely
                 what you tried and why it holds up.
- "unmeasured" — you could not settle this axis from the evidence and tools
                 available. Not a hit, not a pass — an open question. Name the
                 specific check that would settle it.

# OUTPUT CONTRACT — STRICT
Return ONLY a top-level JSON array (no prose, no markdown fences), one object per
lens you were asked to apply, each EXACTLY:

{
  "lens": "<the lens key, verbatim>",
  "verdict": "survives|weakens|kills|unmeasured",
  "argument": "the specific, structural case for this verdict",
  "evidence": [
    {"source": "reddit|arxiv|hackernews|github|newsletter",
     "url": "<real url from gap/context/tools>",
     "quote": "the exact grounding snippet", "date": "YYYY-MM-DD or null"}
  ]
}

Evidence is optional per lens (0-3 items) but every item must be real. Output the
JSON array and NOTHING else.\
"""


# --------------------------------------------------------------------------- #
# Prompt construction                                                          #
# --------------------------------------------------------------------------- #
def _gap_payload(gap: Gap) -> dict[str, Any]:
    """A compact, model-friendly view of the gap under attack."""
    return {
        "title": gap.title,
        "thesis": gap.thesis,
        "why_now": gap.why_now,
        "wedge": gap.wedge,
        "riskiest_assumption": gap.riskiest_assumption,
        "weakest_link": gap.weakest_link,
        "empty_for_a_reason": gap.empty_for_a_reason,
        "empty_reason": gap.empty_reason,
        "sub_segment": gap.sub_segment,
        "novelty": gap.novelty,
        "company": gap.company.model_dump() if gap.company else None,
        "scores": gap.scores.model_dump(),
        "competitors": [
            {
                "name": c.name,
                "positioning": c.positioning,
                "segment": c.segment,
                "price_tier": c.price_tier,
                "weakness": c.weakness,
            }
            for c in gap.competitors
        ],
        "evidence": [
            {"source": e.source.value, "url": e.url, "quote": e.quote, "date": e.date}
            for e in gap.evidence
        ],
    }


def _build_pressure_prompt(gap: Gap, context: str, lenses: list[dict]) -> str:
    """Hand the model the gap, its branch context, and the lenses to apply."""
    lens_block = "\n".join(f"- {lens['key']}: {lens['prompt']}" for lens in lenses)
    payload = json.dumps(_gap_payload(gap), indent=2)
    ctx = (context or "").strip() or "(no additional branch context provided)"
    return (
        "CANDIDATE GAP UNDER ATTACK (this is your ground truth — every Evidence.url "
        "you cite must come from here, the branch context, or a tool result):\n"
        f"{payload}\n\n"
        "BRANCH CONTEXT (where this gap sits in the exploration + a compact view of "
        "the corroborating signals mined for it):\n"
        f"{ctx}\n\n"
        "APPLY EXACTLY THESE LENSES (one verdict object each, in this order):\n"
        f"{lens_block}\n\n"
        "For each lens, genuinely try to prove its kill-question true, grounding "
        "every claim. Optionally call the search_* tools to corroborate a kill or a "
        "rescue. Then output ONLY the strict JSON array of verdict objects — one per "
        "lens above, in order. No prose, no markdown."
    )


# --------------------------------------------------------------------------- #
# Rigor → lens subset                                                          #
# --------------------------------------------------------------------------- #
def _normalize_rigor(rigor: str) -> TestRigor:
    r = (rigor or "").strip().lower()
    return r if r in _RIGOR_COUNT else "standard"  # type: ignore[return-value]


def _select_lenses(rigor: TestRigor) -> list[dict]:
    """Pick the rigor-appropriate lens subset in canonical display order.

    The *which* is chosen by `_LENS_PRIORITY` (cheapest/most-decisive first) so
    lower rigor is a strict subset of higher; the *order* returned follows the
    canonical `LENSES` order so the UI always lists lenses consistently.
    """
    n = _RIGOR_COUNT.get(rigor, 3)
    chosen = set(_LENS_PRIORITY[:n])
    return [lens for lens in LENSES if lens["key"] in chosen]


# --------------------------------------------------------------------------- #
# Robust verdict parsing.                                                      #
# --------------------------------------------------------------------------- #
def _parse_evidence(raw: Any, known_urls: dict[str, bool]) -> list[Evidence]:
    """Validate each evidence object with ``Evidence(**obj)``; drop invalid ones.

    Every kept item is stamped with its TRUE provenance from ``known_urls``
    (normalized url -> was its source LIVE when fetched?): a URL the red team
    pulled from a mock/degraded adapter — or one it never fetched at all — is
    ``live=False``. The model's own (schema-default ``live=True``) claim is
    never trusted, so fixture corroboration can't masquerade as live evidence.
    """
    out: list[Evidence] = []
    if not isinstance(raw, list):
        return out
    for obj in raw:
        if not isinstance(obj, dict):
            continue
        try:
            ev = Evidence(**obj)
        except Exception:  # noqa: BLE001 - pydantic ValidationError et al.
            continue
        ev.live = known_urls.get(_norm_url(ev.url), False)
        out.append(ev)
    return out


def _parse_verdicts(
    raw: list, selected_keys: set[str], known_urls: dict[str, bool]
) -> dict[str, LensVerdict]:
    """Fold the model's JSON array into ``{lens_key: LensVerdict}``.

    Only verdicts whose ``lens`` matches a selected key are kept; unknown verdict
    strings degrade to the neutral ``"weakens"`` rather than being dropped.
    ``known_urls`` carries the per-URL live/mock provenance for the evidence
    stamp (see ``_parse_evidence``).
    """
    by_key: dict[str, LensVerdict] = {}
    for obj in raw:
        if not isinstance(obj, dict):
            continue
        key = str(obj.get("lens", "")).strip().lower()
        if key not in selected_keys or key in by_key:
            continue
        verdict = str(obj.get("verdict", "")).strip().lower()
        if verdict not in _VALID_VERDICTS:
            verdict = "weakens"
        argument = str(obj.get("argument", "") or "").strip()
        by_key[key] = LensVerdict(
            lens=key,
            verdict=verdict,  # type: ignore[arg-type]
            argument=argument,
            evidence=_parse_evidence(obj.get("evidence"), known_urls),
        )
    return by_key


# --------------------------------------------------------------------------- #
# Deterministic degrade — a neutral test derived from the gap's own scores.    #
# --------------------------------------------------------------------------- #
def _lens_base_score(gap: Gap, key: str) -> int:
    """The 1..5 score most relevant to a lens (higher ⇒ more likely to survive)."""
    s = gap.scores
    return {
        "empty_for_a_reason": s.demand_strength,       # real demand ⇒ not a trap
        "incumbent_countermove": s.competitive_openness,  # open field ⇒ survives
        "demand_mirage": s.willingness_to_pay,         # real WTP ⇒ not a mirage
        "why_now_fragility": s.trend_tailwind,         # strong tailwind ⇒ survives
        "moat": gap.novelty,                            # novelty ⇒ defensibility
        # A gap that doesn't claim (or has no) standalone-company framing should
        # weaken under this lens, not coast through on a missing-key default.
        "just_a_feature": 4 if (gap.company and gap.company.standalone) else 2,
        # Venture scale: a claimed expansion path (wedge→product→platform) is the
        # only structural signal we have without an LLM — use it as the neutral
        # proxy for "can this get big", middling by default.
        "venture_scale": 3 if (gap.company and gap.company.expansion_path) else 2,
    }.get(key, 3)


def _neutral_verdict(gap: Gap, key: str) -> LensVerdict:
    """Derive one lens verdict from the gap's own scores — no LLM required.

    Deliberately *neutral* (benefit-of-the-doubt): a middling gap survives; only a
    genuinely soft axis weakens; and a gap that flags itself ``empty_for_a_reason``
    is killed by that specific lens, since it has admitted the trap.
    """
    if key == "empty_for_a_reason" and gap.empty_for_a_reason:
        return LensVerdict(
            lens=key,
            verdict="kills",
            argument=(
                "Heuristic fallback: the gap flags itself empty_for_a_reason — "
                f"{gap.empty_reason or 'the white space is likely a trap'}."
            ),
        )
    score = _lens_base_score(gap, key)
    verdict = "survives" if score >= 3 else "weakens"
    return LensVerdict(
        lens=key,
        verdict=verdict,
        argument=(
            f"Heuristic fallback (no LLM verdict available): the '{key}' axis rates "
            f"{score}/5 on the gap's own scores, so it {verdict} a neutral test."
        ),
    )


def _unevaluated_verdict(key: str) -> LensVerdict:
    """Fill for a lens the model skipped: an honest ``weakens``, never a free pass.

    Deliberately NOT derived from the gap's own self-reported scores — a model
    that engages only one lens must not collect free survivals for the rest.
    """
    return LensVerdict(
        lens=key,
        verdict="weakens",
        argument=(
            f"Lens '{key}' was not evaluated by the model; an unexamined axis "
            "counts against the gap, not for it."
        ),
    )


_RIGOR_ORDER: tuple[TestRigor, ...] = ("light", "standard", "deep")


def _effective_rigor(requested: TestRigor, evaluated: int) -> TestRigor:
    """The rigor actually *earned*: capped by how many lenses the model evaluated.

    A deep run where the model returned only 1 usable verdict was, in truth, a
    light test — recording it as deep would launder unearned confidence credit
    into ``_confidence_for``. Never upgrades past the requested rigor.
    """
    earned: TestRigor = "light"
    for rigor in _RIGOR_ORDER:
        if evaluated >= _RIGOR_COUNT[rigor]:
            earned = rigor
    if _RIGOR_ORDER.index(earned) < _RIGOR_ORDER.index(requested):
        return earned
    return requested


def _tally(lenses: list[LensVerdict]) -> tuple[int, int, int]:
    """Count (survived, weakened, killed) across a list of verdicts.

    ``unmeasured`` is deliberately counted in none of the three: it earns no
    survival bonus and takes no penalty. An axis nobody could check is an open
    question, not support — see ``score_viability``.
    """
    survived = sum(1 for lv in lenses if lv.verdict == "survives")
    weakened = sum(1 for lv in lenses if lv.verdict == "weakens")
    killed = sum(1 for lv in lenses if lv.verdict == "kills")
    return survived, weakened, killed


def _unmeasured_count(lenses: list[LensVerdict]) -> int:
    """How many axes the gauntlet could not settle either way."""
    return sum(1 for lv in lenses if lv.verdict == "unmeasured")


def _assemble(lenses: list[LensVerdict], rigor: TestRigor) -> PressureTest:
    """Wrap a list of verdicts into a PressureTest with counts + a one-line summary."""
    survived, weakened, killed = _tally(lenses)
    unmeasured = _unmeasured_count(lenses)
    n = len(lenses)
    if killed:
        verdict_word = "KILLED"
    elif weakened > survived:
        verdict_word = "WEAKENED"
    elif unmeasured * 2 > n:
        # More axes open than settled — the gauntlet did not actually test this.
        # Saying "SURVIVED" here is the lie the whole unmeasured verdict exists
        # to stop.
        verdict_word = "UNTESTED"
    else:
        verdict_word = "SURVIVED"
    summary = (
        f"{verdict_word}: {survived}/{n} lenses survived, {weakened} weakened, "
        f"{killed} killed, {unmeasured} unmeasured ({rigor} rigor)."
    )
    return PressureTest(
        lenses=lenses,
        survived=survived,
        weakened=weakened,
        killed=killed,
        unmeasured=unmeasured,
        test_rigor=rigor,
        summary=summary,
    )


def _neutral_pressure_test(gap: Gap, rigor: TestRigor = "light") -> PressureTest:
    """A fully-deterministic PressureTest from the gap's scores (LLM-free)."""
    lenses = [_neutral_verdict(gap, lens["key"]) for lens in _select_lenses(rigor)]
    return _assemble(lenses, rigor)


# --------------------------------------------------------------------------- #
# Public entry point — run the gauntlet.                                        #
# --------------------------------------------------------------------------- #
async def pressure_test(
    gap: Gap,
    context: str,
    client: ClaudeClient,
    model: str,
    rigor: str,
    tools: Optional[list[ToolSpec]] = None,
    url_sink: Optional[dict[str, bool]] = None,
) -> PressureTest:
    """Attack ``gap`` from the rigor-appropriate lenses → a ``PressureTest``.

    ``rigor`` picks the subset (``deep`` = 6, ``standard`` = 4, ``light`` = 3).
    ``tools`` are the optional ``search_*`` corroboration tools (as built by
    ``synthesize._build_tools``) so a lens can pull fresh evidence. ``url_sink``
    is the dict those tools record fetches into (normalized url -> source ran
    LIVE?); the handlers mutate it mid-call, and it is read back afterwards so
    every cited evidence item carries its true per-source live/mock provenance
    (fixture corroboration must never be stamped live).

    NEVER raises. On any failure — the LLM call erroring, empty output, or output
    from which not a single usable verdict parses — it degrades to a *neutral
    light* PressureTest synthesized from the gap's own scores, so downstream
    scoring always has something honest to work with even with NO real LLM.
    """
    rig = _normalize_rigor(rigor)
    lenses = _select_lenses(rig)
    selected_keys = {lens["key"] for lens in lenses}

    try:
        prompt = _build_pressure_prompt(gap, context, lenses)
        text = ""
        try:
            result = await client.complete(
                prompt,
                system=_PRESSURE_SYSTEM,
                tools=tools,
                max_turns=6,
                timeout=240,
                model=model,
                # Let the red team reach the open web: check whether a company
                # already ships this (a marketing site the API sources can't see),
                # and whether the demand is real. Directly answers "did we miss a
                # competitor / a company website?".
                web=True,
            )
            text = result.text or ""
        except Exception:  # noqa: BLE001 - resilient: fall through to the neutral test
            text = ""

        # The grounding/provenance map: the gap's own evidence (already stamped
        # by the synthesis grounding gate) plus every URL the corroboration
        # tools fetched during THIS call. Read after complete() — the tool
        # handlers populate ``url_sink`` mid-call.
        known_urls: dict[str, bool] = {}
        for ev in gap.evidence:
            key = _norm_url(ev.url)
            if key:
                known_urls[key] = known_urls.get(key, False) or ev.live
        for url, is_live in (url_sink or {}).items():
            known_urls[url] = known_urls.get(url, False) or is_live

        raw = _extract_json_array(text) if text else None
        by_key = _parse_verdicts(raw, selected_keys, known_urls) if raw else {}

        # No usable verdicts at all -> honest neutral (light) fallback.
        if not by_key:
            return _neutral_pressure_test(gap, "light")

        # Fill any lens the model skipped as an honest "weakens" (never a free
        # survive derived from the gap's own scores), and downgrade the recorded
        # rigor to the lens count the model actually evaluated.
        assembled = [
            by_key.get(lens["key"]) or _unevaluated_verdict(lens["key"])
            for lens in lenses
        ]
        return _assemble(assembled, _effective_rigor(rig, len(by_key)))
    except Exception:  # noqa: BLE001 - absolute last-resort guard; never raise
        return _neutral_pressure_test(gap, "light")


# --------------------------------------------------------------------------- #
# Adversarial self-critique — after scoring, find the ONE strongest reason the  #
# score itself is wrong (SPEC feature C). This is a meta-check on the gauntlet:  #
# a confident-looking 82 that has an obvious "…but the whole segment is a fad"   #
# hole should say so, on the record.                                            #
# --------------------------------------------------------------------------- #
_CRITIQUE_SYSTEM = """\
You are a skeptical investor doing one final gut-check on a market-gap score.
You are given a candidate gap, the adversarial pressure-test result, and the
numeric viability score (0-100) the system assigned it. Your job: name the SINGLE
strongest reason that score is WRONG — too high (most common) or too low. One
sharp, specific, structural reason, grounded in what you were given. No fabricated
facts. Output ONE or TWO plain sentences, no preamble, no markdown, no lists."""


async def adversarial_self_critique(
    gap: Gap,
    viability: int,
    test: PressureTest,
    client: ClaudeClient,
    model: str,
) -> str:
    """Return the single strongest reason ``viability`` is wrong (or "" on failure).

    A cheap, single-turn meta-check run after scoring. NEVER raises: on any
    failure it returns an empty string, so the node simply carries no critique.
    """
    try:
        tally = f"{test.survived} survived / {test.weakened} weakened / {test.killed} killed"
        prompt = (
            f"CANDIDATE GAP:\n{json.dumps(_gap_payload(gap), indent=2)}\n\n"
            f"PRESSURE TEST ({test.test_rigor} rigor): {tally}. {test.summary}\n\n"
            f"ASSIGNED VIABILITY SCORE: {viability}/100.\n\n"
            "Name the single strongest reason this score is wrong. One or two "
            "sentences, specific and structural, grounded only in the above."
        )
        result = await client.complete(
            prompt, system=_CRITIQUE_SYSTEM, max_turns=1, timeout=90, model=model
        )
        text = (result.text or "").strip()
        # Guard against a model that ignores the format and dumps JSON/markdown.
        if text.startswith("{") or text.startswith("["):
            return ""
        return text[:600]
    except Exception:  # noqa: BLE001 - a critique is a bonus; never break scoring.
        return ""


# --------------------------------------------------------------------------- #
# Viability scoring.                                                           #
# --------------------------------------------------------------------------- #
# Per-lens deltas applied on top of the base composite (0..100). Survivals nudge
# up, weakens bite, and any kill is hard-penalized (plus a hard viability cap).
_SURVIVE_BONUS = 6.0
_WEAKEN_PENALTY = 9.0
_KILL_PENALTY = 26.0
_KILL_CAP = 40          # a gap with any kill verdict cannot score above this

# Obviousness penalty / originality bonus, applied from gap.novelty (1..5).
# `novelty` was collected, shown to the red team, used as a moat proxy in the
# LLM-free fallback — and left OUT of SCORE_KEYS, so it moved the score by
# exactly nothing. The engine had no incentive to be creative and reliably
# converged on the same safe shape (an observability/dev-tool layer) across
# unrelated domains, because obvious-and-demanded scores identically to
# original-and-demanded.
#
# This is not "reward weirdness". An obvious idea is genuinely worse on the
# merits: if it is the first thing anyone would think of, everyone has, and the
# competitive clock started without you. Novelty 3 is neutral.
_NOVELTY_DELTA: dict[int, float] = {1: -8.0, 2: -4.0, 3: 0.0, 4: 3.0, 5: 6.0}


def score_viability(gap: Gap, test: PressureTest) -> tuple[int, Confidence]:
    """Blend the gap's base scores with the pressure-test outcome → (0..100, confidence).

    Viability starts from the default-weighted composite of the five 1..5 scores,
    linearly stretched onto 0..100, then adjusted by the gauntlet: reward each
    survival, penalize each weaken, and hard-penalize any kill (a single kill also
    caps the score). Corroborating evidence pulled by the lenses adds a small
    bonus. The result is clamped to 0..100.

    Confidence reflects how *hard* the gap was actually tested — ``test_rigor``,
    how well-grounded the gap is, and whether the lenses corroborated with live
    evidence — so a lightly-tested score reads as less trustworthy than a
    battle-tested one, independent of the number itself.

    An ``unmeasured`` lens scores ZERO — no bonus, no penalty. Only a survival
    the red team actually earned pays. This is the difference between "we tried
    to kill it and failed" and "nobody looked", which the engine previously paid
    identically: four unchecked lenses used to hand a gap +24 for being ignored.

    Corroboration does NOT add to viability. It used to add +2 per lens-fetched
    evidence item (capped +10) with no regard for which way the evidence cut —
    so a red team that documented seven funded competitors handed the gap it was
    attacking the full +10. Evidence volume is a statement about how well-TESTED
    a gap is, not how GOOD it is, so it belongs in confidence (where it already
    lives) and nowhere else.
    """
    # Base: default-weighted composite (1..5) stretched to 0..100.
    composite = composite_score(gap.scores, Weights())  # 1..5
    viability = (composite - 1.0) / 4.0 * 100.0

    survived, weakened, killed = _tally(test.lenses)
    viability += _SURVIVE_BONUS * survived
    viability -= _WEAKEN_PENALTY * weakened
    viability -= _KILL_PENALTY * killed

    # Obviousness is a real defect; originality is a real edge.
    viability += _NOVELTY_DELTA.get(int(gap.novelty), 0.0)

    if killed:
        viability = min(viability, float(_KILL_CAP))

    viability_int = int(max(0, min(100, round(viability))))
    corroboration = sum(len(lv.evidence) for lv in test.lenses)
    return viability_int, _confidence_for(gap, test, corroboration)


# The "high" bar (all must hold, on top of the medium points): the recorded
# rigor actually earned (standard/deep — `_effective_rigor` already downgraded
# barely-engaged runs), at least this many LIVE corroboration evidence items
# pulled by the lenses, zero kills, and a majority of evaluated lenses that
# either survives or weakens *with evidence* (an evidence-free weaken — e.g. a
# skipped-lens fill — is an unexamined axis, not support).
_HIGH_MIN_LIVE_CORROBORATION = 2
_HIGH_RIGORS = frozenset({"standard", "deep"})


def _confidence_for(gap: Gap, test: PressureTest, corroboration: int) -> Confidence:
    """Low/medium/high from rigor + how grounded/corroborated the gap is.

    Points: rigor (deep=2, standard=1, light=0) + a point when the gap is well
    grounded (>= 3 of its own evidence items) + a point when the lenses pulled
    corroborating evidence of their own. 2 → medium (the workhorse tier),
    else low.

    "high" is deliberately expensive — 3+ points AND all of: standard/deep
    rigor actually earned, >= 2 LIVE corroboration items (mock/fixture fetches
    carry live=False and cannot count), no kill verdict, and a majority of the
    evaluated lenses surviving or weakening with evidence. The gap's own
    (possibly hallucinated) evidence can lift it to medium at most.
    """
    points = {"deep": 2, "standard": 1, "light": 0}.get(test.test_rigor, 0)
    if len(gap.evidence) >= 3:
        points += 1
    if corroboration > 0:  # lenses corroborated with their own fetched evidence
        points += 1

    live_corroboration = sum(
        1 for lv in test.lenses for ev in lv.evidence if ev.live
    )
    _, _, killed = _tally(test.lenses)
    supported = sum(
        1
        for lv in test.lenses
        if lv.verdict == "survives" or (lv.verdict == "weakens" and lv.evidence)
    )
    earns_high = (
        test.test_rigor in _HIGH_RIGORS
        and live_corroboration >= _HIGH_MIN_LIVE_CORROBORATION
        and killed == 0
        and supported * 2 > len(test.lenses)
    )
    if points >= 3 and earns_high:
        return "high"
    if points >= 2:
        return "medium"
    return "low"
