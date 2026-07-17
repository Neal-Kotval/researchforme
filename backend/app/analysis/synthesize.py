"""The creative synthesis engine — the heart of the Market Gap Finder.

This is where distilled `ExtractedSignals` (demand / capability / supply mined
from Reddit, arXiv, Hacker News, GitHub, and newsletters) get turned into a
*short list of grounded market gaps* by the LLM. We do three things well here:

1. **Prompting.** A strong system prompt encodes the product's core thesis
   (rising demand x lagging supply x a recent "why now" shift), plus contrarian,
   inversion, cross-source-correlation, and adversarial-self-check lenses. The
   user prompt hands the model the actual mined signals as compact JSON.

2. **Tool use.** The model is given one tool per source (search_reddit /
   search_arxiv / search_hackernews / search_github / search_newsletters). Each
   is a thin async wrapper over
   `get_source(name).fetch(...)` that returns a compact text summary of the
   `RawItem`s, so the model can corroborate a hunch on demand mid-synthesis
   (agent-sdk / api backends). Backends without tools just ignore them.

3. **Robust parsing.** The model is required to emit a strict top-level JSON
   array of `Gap` objects. We strip prose / markdown fences, extract the array,
   validate every element with `Gap(**obj)`, DROP anything invalid, and return
   `(gaps, backend, warnings)`. If nothing valid survives, we fall back to the
   canned fixture synthesis so the loop never returns empty-handed.

Everything degrades: no creds -> sources run mock -> synthesis still grounded;
no LLM -> fixture. `synthesize` never raises for ordinary failure modes.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ..llm.client import ClaudeClient, ToolSpec
from ..schemas import (
    ExtractedSignals,
    Gap,
    SourceName,
    SourceReport,
    SourceStatus,
)

# How many of each signal kind to actually put in the prompt. The extractor may
# hand us more; we keep the strongest-N so the prompt stays dense, not bloated.
_MAX_DEMAND = 40
_MAX_CAPABILITY = 24
_MAX_SUPPLY = 24
# Cap the number of gaps we accept from a single run (spec: 0-8).
_MAX_GAPS = 8
# Per-tool-call item cap when summarizing a corroboration fetch back to the model.
_TOOL_ITEM_CAP = 8


# --------------------------------------------------------------------------- #
# System prompt — the "brain". This is intentionally opinionated: it is what   #
# separates a bland list of ideas from sharp, defensible, non-obvious gaps.    #
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """\
You are the analytical engine of "Market Gap Finder" — a tool that finds
*real, winnable* market gaps, not brainstorm fluff. You reason like a
seed-stage investor crossed with a contrarian product founder. You are
rewarded for a SHORT list of genuinely strong gaps and penalized for padding.

# THE CORE THESIS (memorize this)
A real market gap = the intersection of three forces:
  (1) RISING DEMAND — unmet pain, complaints, wishes, and rising interest.
      Evidence: Reddit threads describing a workaround people hate, and Ask HN
      posts where builders/operators beg for a tool that doesn't exist yet.
  (2) LAGGING SUPPLY — the incumbents have a structural BLIND SPOT: wrong
      business model, wrong segment, too enterprise, too consumer, too generic,
      or captured by their existing customers so they cannot serve this need.
      Supply hints: Show HN launches and fast-growing GitHub repos already
      circling the space.
  (3) A RECENT SHIFT ("WHY NOW") — something changed in the last ~18 months that
      just made this feasible or newly urgent: an arXiv capability jump (cheaper
      inference, on-device models, better extraction), a burst of GitHub adoption
      showing the tooling substrate got cheaper, or a wave of newsletter coverage
      signaling the topic just crossed into attention. If there is no credible
      "why now", it is probably NOT a gap — it is either already served or
      empty-for-a-reason.

The best gaps have ALL THREE, and ideally multiple sources point the same way
(cross-source correlation). Explicitly reward gaps where Reddit / Hacker News
demand, an arXiv or GitHub capability, and newsletter attention corroborate each
other.

# THINKING LENSES (apply every one before you finalize)
- CROSS-SOURCE CORRELATION: prefer gaps triangulated by 2-3 sources over any
  single loud signal. Say which sources agree.
- CONTRARIAN / INVERSION: deliberately ask "what does everyone assume is
  saturated but actually isn't?" and "what looks like an empty white space but
  is empty FOR A REASON?" Mark the latter honestly with empty_for_a_reason.
- ADVERSARIAL SELF-CHECK: for each gap, name the single weakest_link — the
  softest score or the assumption most likely to be wrong. Do not hide it.
- NO FABRICATION: every Evidence.url MUST come from the signals you were given
  or from a tool result you fetched. Never invent a URL, a quote, or a
  competitor. If you cannot ground a claim, drop it.

# TOOLS
You may have tools: search_reddit, search_arxiv, search_hackernews,
search_github, search_newsletters, and web_search / web_fetch for the OPEN web.
Use them SPARINGLY and only to corroborate a specific hypothesis (e.g., "is there
really pain about X?", "did anyone already ship this?", or "did the tooling
actually take off?"). Every URL you cite from a tool result is fair game as
Evidence.

DEMAND IS THE HARD PART. The mined signals lean toward SUPPLY (papers, repos —
what's being built), and the demand-bearing adapters are domain-limited. When a
gap's demand rests only on supply-side evidence, use web_search to look for the
actual pain — forum threads, complaints, "I wish", questions with no good answer,
people describing the workaround — and cite what you find. A gap grounded ONLY in
arXiv/GitHub has UNMEASURED demand; say so honestly rather than dressing supply up
as demand. If no tools are available, reason only from the provided signals — do
not pretend to have fetched anything.

# COMPETITORS
For each gap, name up to 5 real, specific incumbents/alternatives. For each:
its actual positioning, the segment it targets, a rough price_tier
(free / $ / $$ / $$$ / enterprise), and — most important — the specific
weakness / blind spot that leaves THIS gap open. Generic weaknesses ("bad UX")
are worthless; name the structural reason they can't or won't serve this need.

# SCORING (each 1..5, higher = more attractive)
- demand_strength: how loud/urgent/repeated is the unmet need?
- competitive_openness: how open is the field? (5 = wide open, 1 = crowded).
  Count who is ALREADY THERE, funded, shipping — not just who has a repo or a
  paper. A field with several funded companies selling this today is a 1 or 2,
  no matter how real the pain is. Do not score this from vibes: if the steering
  or your tools surfaced named competitors, they set this number.
  PROVEN DEMAND IMPLIES COMPETITION. If demand_strength is high because the pain
  is measured, budgeted, and already converts to dollars, then the field is
  almost certainly CONTESTED — money attracts entrants. High demand AND high
  openness is a contradiction you must justify with positive evidence of an open
  slot, not assume. An unfound competitor is not an absent one: when you cannot
  find who serves proven demand, score openness DOWN (2-3) and say the field is
  likely occupied by someone you didn't surface — do not reward the gap for your
  search's blind spot.
  CHECK FOR THE PIVOT. The dangerous competitor is rarely a direct-category
  clone; it is a well-funded ADJACENT incumbent who has just rebranded onto this
  exact wedge — a general-pipeline vendor now marketing "agentic X", an eval
  vendor adding cost, a cost vendor adding semantics. Use web_search for
  "<the adjacent category> + <the new capability>" and for the obvious incumbents
  by name + the wedge. A funded player already sitting in front of the buyer and
  aiming its marketing at this exact wedge means openness is 1, not 4.
- trend_tailwind: how strong is the "why now" tailwind?
- feasibility: how buildable is this BY THIS FOUNDER, given the resources and
  time horizon their steering context states? Read the steering before scoring.
  If they say they will raise, or that capital intensity and long timelines are
  acceptable, then a fab-dependent or hardware-heavy idea is NOT low-feasibility
  — score it against the plan they actually have. If the steering says NOTHING
  about resources, assume a competent founder who will resource the idea
  appropriately — do NOT default to solo, unfunded, or sub-6-month. Silence is
  not a constraint. This score is "can THEY build it", never "is it software"
  and never "is it cheap". A hard, expensive, multi-year idea with a real market
  is a 3-4, not a 1: reserve 1-2 for things that are genuinely impossible for
  them (needs a skill they lack and cannot hire, or breaks physics).
- willingness_to_pay: will the segment actually pay, and roughly how much?
Also set novelty 1..5: how non-obvious / creative is this framing? Score this
honestly — it now MOVES THE SCORE. Obvious framings are penalized: if this is
the first idea anyone entering the space would have, it is a 1-2 and it should
be, because everyone else had it too and the clock started without you. A 5 is a
framing a domain expert would not have predicted but, once said, is clearly
right.
Be honest and use the full range. If everything is a 4, you are not thinking.

# COMPANY, NOT A FEATURE (the bar every gap must clear)
Do NOT return thin gaps that are really just a *feature* someone would expect
bundled free into a product they already pay for. Every gap must be shaped as a
STANDALONE COMPANY a founder could build a business around. For each gap, fill a
`company` object:
- product: what you actually build — concrete, 2-3 sentences, not a category.
- icp: the ideal customer profile you sell to first (specific, nameable).
- business_model: how it makes money and the rough pricing shape (who pays, how
  much, per what).
- expansion_path: how a sharp first wedge grows into a durable company, not a
  one-trick feature. "Wedge → product → platform" is ONE arc, not the only one —
  do not force every idea through it. A supply business scales by cost curve and
  volume; a hardware company by design wins and manufacturing; a marketplace by
  liquidity; a standard by adoption; a services firm by productizing what it
  learned. Name the arc that actually fits THIS business.
- moat: the durable advantage once you're working (proprietary data, network
  effects, distribution, switching cost, or hard tech).
- standalone: true only if this can stand alone as a company; false if honestly
  it's a feature of something bigger.
- standalone_reason: one or two sentences — why it's a company (or, if false,
  why it's really just a feature). Be honest; a false here should drag the gap
  down, not be hidden.
Bias hard toward company-scale ideas. If a candidate is only a feature, either
reframe it into the genuine company around it or drop it.

# VARY THE SHAPE — THE DEFAULT ANSWER IS A TRAP
Left alone, this engine converges: across totally unrelated domains — protein
models, surgical robotics, GPU kernels, RL post-training — it keeps proposing
the same artifact, "an observability / eval / dev-tool layer sold to engineers".
That is not what the world needs five times; it is one prior, firing repeatedly,
because it is the safest-sounding shape. When you notice yourself reaching for
it, stop and ask what ELSE this evidence supports.

Before finalizing, consider whether the real opportunity here is instead:
  - a SUPPLY business (sell the scarce input everyone is hand-making)
  - a HARDWARE or physical product (a device, board, rack, instrument)
  - a MARKETPLACE or exchange (match two sides nobody is matching)
  - a STANDARD, protocol, or benchmark others must adopt
  - a SERVICES or agency motion that productizes into software later
  - a DATA or index business (own the measurement everyone cites)
  - a VERTICAL application (own an industry's whole workflow, not a layer of it)
  - buying, operating, or owning the asset rather than selling software to its owner
An observability/dev-tool framing is allowed — but only when it genuinely beats
these alternatives, not by default. If several gaps in one batch share a shape,
that is a signal you stopped thinking; diversify them.

# SAMPLE FROM THE TAIL, NOT THE MODE
You were trained to emit the single most typical answer — which, for "find a
market gap", is exactly the obvious idea a hundred other founders already had.
Fight that. Before you write, silently enumerate the range of framings this
evidence could support, from the most obvious (highest-probability, what everyone
would say first) to the genuinely non-obvious (low-probability, what a domain
insider would say only after thinking). Deliberately spend most of your gaps in
the lower-probability tail — the surprising-but-defensible reads — and include an
obvious framing only if it is truly strong. A gap a smart outsider would have
guessed in ten seconds is worth little; the ones worth returning are the ones
that made even you pause.

# OUTPUT CONTRACT — STRICT
Return ONLY a top-level JSON array (no prose, no markdown fences) of 0-8 gap
objects — return ONLY what the evidence supports; returning fewer (or zero)
gaps on thin evidence is correct behavior and rewarded. Never pad. Each object
MUST have exactly this shape:

{
  "title": "short punchy name for the gap",
  "thesis": "one sentence: the bet in a breath",
  "easy_explain": "3-5 sentences, plain language, for a smart person who does NOT work in this field. Structure it as: (1) what actually happens today and why it hurts — a concrete scene, not an abstraction; (2) who specifically feels that pain and what they do about it now; (3) what this product would do differently, described as if demoing it; (4) why that is worth money to them. NO jargon, NO acronyms without expansion, NO buzzwords ('leverage', 'orchestrate', 'seamless', 'platform'). If a term is unavoidable, define it inline in five words. Write like you are explaining it to a sharp friend over coffee who will ask 'wait, why doesn't X already do that?' — and answer that question. This is not a summary of the thesis; it is the idea made legible.",
  "company": {
    "product": "what you actually build (2-3 concrete sentences)",
    "icp": "the specific first customer you sell to",
    "business_model": "how it earns + rough pricing shape",
    "expansion_path": "how the first wedge grows into a durable company — name the arc that fits THIS business (platform, cost curve, design wins, liquidity, adoption, productized services), not a template",
    "moat": "the durable advantage once it works",
    "standalone": true,
    "standalone_reason": "why it's a company, not a feature"
  },
  "scores": {
    "demand_strength": 1-5,
    "competitive_openness": 1-5,
    "trend_tailwind": 1-5,
    "feasibility": 1-5,
    "willingness_to_pay": 1-5
  },
  "evidence": [
    {"source": "reddit|arxiv|hackernews|github|newsletter",
     "url": "<real url from signals/tools>",
     "quote": "the exact grounding snippet", "date": "YYYY-MM-DD or null"}
  ],
  "competitors": [
    {"name": "", "url": "", "positioning": "", "segment": "",
     "price_tier": "free|$|$$|$$$|enterprise", "weakness": "the blind spot"}
  ],
  "wedge": "a CONCRETE first move — what you build/prove/sell FIRST, to whom, and what you deliberately do NOT do yet. SCALE THE MOVE TO THE IDEA: for a capital-intensive bet the honest first move may be a proof point that unlocks funding or a design partner, not a sellable product — do not shrink the IDEA just to produce a move that fits in a month. Not a category ('a platform for X'); a specific opening act.",
  "riskiest_assumption": "the one belief that, if false, kills this — stated so it could actually be tested (a question you could answer with ~10 customer calls or one cheap experiment), not a vague worry",
  "weakest_link": "the softest score or biggest doubt, named",
  "why_now": "the DATED shift that makes this buildable/urgent THIS year and not three years ago (a specific recent change — a new capability, a price drop, a regulation, a platform). If you can't name a real recent shift, say the why-now is weak — don't invent one.",
  "empty_for_a_reason": false,
  "empty_reason": "if true, why the white space is actually a trap",
  "novelty": 1-5,
  "sub_segment": "which sub-segment this serves",
  "tags": ["short", "keywords"]
}

Rules: 1-6 evidence items per gap, only items genuinely present in the signals
or your tool results, each with its real URL. Up to 5 competitors, each with a
specific weakness. Output the JSON array and NOTHING else.\
"""


# --------------------------------------------------------------------------- #
# Tool wrappers — one per source. Thin async adapters over the registry's      #
# get_source(name).fetch, returning a compact text summary of RawItems so the  #
# model can corroborate mid-synthesis.                                         #
# --------------------------------------------------------------------------- #
def _summarize_items(source: SourceName, items: list, note: Optional[str]) -> str:
    """Render fetched RawItems into a compact, model-friendly text block."""
    if not items:
        return f"[{source.value}] no items returned. {note or ''}".strip()

    lines: list[str] = [f"[{source.value}] {len(items)} items (top {min(len(items), _TOOL_ITEM_CAP)}):"]
    for it in items[:_TOOL_ITEM_CAP]:
        date = ""
        created = getattr(it, "created", None)
        if created is not None:
            try:
                date = created.date().isoformat()
            except Exception:  # noqa: BLE001
                date = str(created)[:10]
        title = (getattr(it, "title", "") or "").strip()
        url = getattr(it, "url", "") or ""
        weight = getattr(it, "weight", 0.0) or 0.0
        body = (getattr(it, "body", "") or "").strip().replace("\n", " ")
        if len(body) > 240:
            body = body[:240] + "…"
        lines.append(
            f"- {title}\n"
            f"  url: {url}\n"
            f"  date: {date or 'n/a'} | signal_weight: {weight:.2f}\n"
            f"  snippet: {body}"
        )
    return "\n".join(lines)


def _norm_url(url: str) -> str:
    """Canonicalize a URL just enough for grounding-set membership checks."""
    return (url or "").strip().rstrip("/")


def _make_tool_handler(
    name: SourceName,
    area: str,
    sub_segments: list[str],
    url_sink: Optional[dict[str, bool]] = None,
):
    """Build the async handler that fetches from one source for a given query.

    The handler NEVER raises — on any failure it returns a short text note so the
    model can adapt. `fetch` is synchronous per the Source ABC, so we run it in a
    worker thread to stay non-blocking. When ``url_sink`` is given, every fetched
    item URL is recorded there (mapped to whether the source ran LIVE) so the
    grounding gate can accept tool-corroborated evidence.
    """

    async def _handler(args: dict) -> str:
        import asyncio

        query = str(args.get("query", "") or "").strip()
        try:
            limit = int(args.get("limit", 8))
        except (TypeError, ValueError):
            limit = 8
        limit = max(1, min(limit, 25))

        if not query:
            return f"[{name.value}] error: empty query; provide a 'query' string."

        # Lazy import: the registry is written by a sibling module. Importing it
        # here (not at module load) keeps this file independently importable.
        try:
            from ..sources.registry import get_source
        except Exception as exc:  # noqa: BLE001
            return f"[{name.value}] registry unavailable: {exc}"

        source = get_source(name)
        if source is None:
            return f"[{name.value}] no such source registered."

        # Treat the query as the keyword set; keep the run's area/sub-segments.
        keywords = [w for w in query.replace(",", " ").split() if w]

        def _run():
            return source.fetch(area=area, keywords=keywords or [query], sub_segments=sub_segments)

        try:
            result = await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001 - adapters shouldn't raise, but be safe
            return f"[{name.value}] fetch failed: {exc}"

        items = list(getattr(result, "items", []) or [])[:limit]
        report = getattr(result, "report", None)
        note = getattr(report, "note", None) if report is not None else None
        # Feed every fetched URL into the grounding set: a URL the model just
        # corroborated via a tool is fair game as Evidence.
        if url_sink is not None:
            is_live = bool(report and getattr(report, "status", None) == SourceStatus.LIVE)
            for it in items:
                url = _norm_url(getattr(it, "url", "") or "")
                if url:
                    url_sink[url] = is_live
        return _summarize_items(name, items, note)

    return _handler


def build_corroboration_tools(
    area: str,
    sub_segments: list[str],
    url_sink: Optional[dict[str, bool]] = None,
    include_pressure_only: bool = False,
) -> list[ToolSpec]:
    """One live ``search_*`` corroboration tool per source, bound to ``area``.

    Public factory so callers outside synthesis (notably the autonomous
    pressure-test path, SPEC §5) can hand the same fresh-evidence tools to a
    red-team model. The handlers take the *query* from the model and keep the
    given ``area``/``sub_segments`` as scope; each NEVER raises (returns a text
    note on failure) so tool use can't crash a run. ``url_sink`` (optional)
    collects every fetched item URL -> live? so the grounding gate can accept
    tool-corroborated evidence.

    ``include_pressure_only=True`` (the pressure-test path should pass it)
    additionally exposes the pressure-only adapters — ``search_outcomes`` (YC
    company outcomes) and ``search_postmortems`` (curated failure corpus) — so
    the crowded / empty-for-a-reason lenses can pull outcome evidence. They are
    deliberately absent from the default set: outcome corroboration is red-team
    material, not demand-mix input.
    """
    schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search terms to corroborate a hypothesis.",
            },
            "limit": {
                "type": "integer",
                "description": "Max items to return (1-25, default 8).",
            },
        },
        "required": ["query"],
    }
    specs = [
        (
            "search_reddit",
            SourceName.REDDIT,
            "Search Reddit for real user pain, complaints, wishes, and workarounds "
            "to corroborate demand for a hypothesized gap.",
        ),
        (
            "search_arxiv",
            SourceName.ARXIV,
            "Search recent arXiv papers for a capability shift ('why now') that "
            "makes a hypothesized gap newly feasible.",
        ),
        (
            "search_hackernews",
            SourceName.HACKERNEWS,
            "Search Hacker News for Ask HN pain (demand), Show HN launches "
            "(supply), and high-point stories (momentum) around a hypothesized gap.",
        ),
        (
            "search_github",
            SourceName.GITHUB,
            "Search recent GitHub repos by star velocity to see whether the tooling "
            "substrate for a hypothesized gap just got cheaper, and who is already "
            "building in the space.",
        ),
        (
            "search_newsletters",
            SourceName.NEWSLETTER,
            "Search recent tech-newsletter coverage for an attention / 'why now' "
            "tailwind behind a hypothesized gap.",
        ),
        (
            "search_jobs",
            SourceName.JOBS,
            "Search job postings (RemoteOK, WeWorkRemotely, HN who-is-hiring) for "
            "wallet-backed demand: a recurring role across distinct companies means "
            "the pain is worth salaries.",
        ),
        (
            "search_appreviews",
            SourceName.APPREVIEWS,
            "Pull 1-3 star iTunes reviews of the segment's top apps: paid-user "
            "pain, weighted by how often each complaint theme recurs.",
        ),
        (
            "search_regulatory",
            SourceName.REGULATORY,
            "Search Federal Register rules and proposed rules for a dated 'why "
            "now' (compliance deadlines, comment windows) or a regulatory wall "
            "explaining an empty space.",
        ),
    ]
    if include_pressure_only:
        specs += [
            (
                "search_outcomes",
                SourceName.OUTCOMES,
                "Look up funded YC companies in this segment and their outcome "
                "(Active / Acquired / Inactive / Public): direct evidence for the "
                "crowded and empty-for-a-reason lenses.",
            ),
            (
                "search_postmortems",
                SourceName.POSTMORTEMS,
                "Search a curated corpus of documented startup failures for "
                "'someone already tried this and died because X' corroboration "
                "(honest mock data — no live source exists).",
            ),
        ]
    return [
        ToolSpec(
            name=tool_name,
            description=desc,
            input_schema=schema,
            handler=_make_tool_handler(src, area, sub_segments, url_sink=url_sink),
        )
        for tool_name, src, desc in specs
    ]


def _build_tools(
    signals: ExtractedSignals, url_sink: Optional[dict[str, bool]] = None
) -> list[ToolSpec]:
    """Corroboration tools scoped to a segment's mined ``ExtractedSignals``."""
    return build_corroboration_tools(signals.area, signals.sub_segments, url_sink=url_sink)


# --------------------------------------------------------------------------- #
# User prompt — hand the model the mined signals as compact JSON.              #
# --------------------------------------------------------------------------- #
def _signals_payload(signals: ExtractedSignals) -> dict[str, Any]:
    """Compact, strongest-first view of the signals for the prompt."""

    def _demand(d):
        return {
            "source": d.source.value,
            "kind": d.kind,
            "quote": d.quote,
            "url": d.url,
            "strength": round(d.strength, 3),
            "date": d.date.date().isoformat() if d.date else None,
            "tags": d.tags,
            "live": d.live,
        }

    def _cap(c):
        return {
            "source": c.source.value,
            "description": c.description,
            "url": c.url,
            "momentum": round(c.momentum, 3),
            "date": c.date.date().isoformat() if c.date else None,
            "live": c.live,
        }

    def _sup(s):
        return {
            "source": s.source.value,
            "name": s.name,
            "hint": s.hint,
            "url": s.url,
            "live": s.live,
        }

    def _fund(f):
        return {
            "company": f.company,
            "round_hint": f.round_hint,
            "space_tokens": f.space_tokens,
            "url": f.url,
            "live": f.live,
        }

    demand = sorted(signals.demand, key=lambda d: d.strength, reverse=True)[:_MAX_DEMAND]
    capability = sorted(signals.capability, key=lambda c: c.momentum, reverse=True)[:_MAX_CAPABILITY]
    supply = signals.supply[:_MAX_SUPPLY]

    return {
        "area": signals.area,
        "sub_segments": signals.sub_segments,
        "keywords": signals.keywords,
        "demand_signals": [_demand(d) for d in demand],
        "capability_signals": [_cap(c) for c in capability],
        "supply_signals": [_sup(s) for s in supply],
        # Funding-round crowding hints (empty unless newsletters carried
        # funding announcements) — evidence for the 'crowded' pressure lens.
        "funding_signals": [_fund(f) for f in signals.funding],
    }


def _sources_summary(reports: list[SourceReport]) -> list[dict[str, Any]]:
    return [
        {
            "source": r.name.value,
            "status": r.status.value,
            "item_count": r.item_count,
            "freshest": r.freshest.date().isoformat() if r.freshest else None,
            "note": r.note,
        }
        for r in reports
    ]


def _build_user_prompt(
    signals: ExtractedSignals,
    reports: list[SourceReport],
    steering: str = "",
    avoid_titles: Optional[list[str]] = None,
) -> str:
    payload = _signals_payload(signals)
    sources = _sources_summary(reports)
    steer = (steering.strip() + "\n\n") if steering and steering.strip() else ""

    # Anti-mode-collapse: the biggest quality failure observed is many branches
    # independently proposing the SAME meta-shape (e.g. "eval/observability layer
    # for X" 23 times). Show this synthesis what the rest of the tree has already
    # proposed and forbid re-proposing it or a thin rewording — force a genuinely
    # different angle or an honest empty array.
    avoid = ""
    if avoid_titles:
        listed = "\n".join(f"- {t}" for t in avoid_titles[:40])
        avoid = (
            "ALREADY PROPOSED ELSEWHERE IN THIS EXPLORATION — do NOT propose any of "
            "these again, nor a reworded near-duplicate (same product shape aimed at "
            "the same buyer counts as a duplicate even with a different name). If the "
            "only gaps you can find here are variations on these, return an EMPTY "
            "array — a duplicate is worse than nothing:\n"
            f"{listed}\n\n"
        )

    return (
        f"{steer}"
        f"{avoid}"
        f"AREA UNDER STUDY: {signals.area}\n"
        f"SUB-SEGMENTS OF INTEREST: {', '.join(signals.sub_segments) or '(none specified)'}\n\n"
        "SOURCE TELEMETRY (which sources fired, and how healthy the data is — "
        "weight LIVE data over MOCK when forming conviction):\n"
        f"{json.dumps(sources, indent=2)}\n\n"
        "MINED SIGNALS (this is your ground truth — every Evidence.url you cite "
        "must come from here or from a tool result):\n"
        f"{json.dumps(payload, indent=2)}\n\n"
        "TASK: Apply the core thesis and every thinking lens from your system "
        "instructions. Optionally call the search_* tools to corroborate specific "
        "hypotheses. Then output ONLY the strict JSON array of 0-8 grounded gap "
        "objects — most attractive first, only as many as the evidence supports "
        "(an empty array is a valid answer on thin evidence). No prose, no markdown."
    )


# --------------------------------------------------------------------------- #
# Robust JSON extraction & validation.                                         #
# --------------------------------------------------------------------------- #
def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` / ``` ... ``` fences if the model added them."""
    t = text.strip()
    if t.startswith("```"):
        # drop the opening fence line (``` or ```json)
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1 :]
        # drop a trailing fence
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _extract_json_array(text: str) -> Optional[list]:
    """Pull the first top-level JSON array out of arbitrary model output.

    Tries, in order: whole-text parse, fence-stripped parse, then a
    string-aware bracket scan for the outermost [...] region. Returns a list on
    success, else None.
    """
    if not text:
        return None

    candidates = [text, _strip_fences(text)]
    for cand in candidates:
        cand = cand.strip()
        if not cand:
            continue
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, list):
                return parsed
            # Some models wrap the array: {"gaps": [...]}
            if isinstance(parsed, dict):
                for key in ("gaps", "results", "data", "items"):
                    if isinstance(parsed.get(key), list):
                        return parsed[key]
        except json.JSONDecodeError:
            pass

    # Fallback: scan for the outermost balanced [...] ignoring brackets in strings.
    src = _strip_fences(text)
    start = src.find("[")
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
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                blob = src[start : i + 1]
                try:
                    parsed = json.loads(blob)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    return None
    return None


def _ground_evidence(gap: Gap, known_urls: dict[str, bool], warnings: list[str]) -> None:
    """The grounding gate: drop Evidence whose URL we never actually saw.

    A URL is grounded iff it appears in the mined signals or in a corroboration
    tool fetch from this synthesis (``known_urls``: normalized url -> was the
    source LIVE?). Kept items inherit that per-item provenance; dropped items
    are counted on ``gap.evidence_dropped``. A gap stripped of ALL evidence keeps
    its other fields but is flagged ``grounded=False`` so downstream can't treat
    it as a supported finding.
    """
    kept: list = []
    dropped = 0
    for ev in gap.evidence:
        key = _norm_url(ev.url)
        if key in known_urls:
            ev.live = known_urls[key]
            kept.append(ev)
        else:
            dropped += 1
    if dropped:
        gap.evidence = kept
        gap.evidence_dropped = dropped
        warnings.append(
            f"synthesis: gap '{gap.title}': dropped {dropped} ungrounded evidence "
            "item(s) (URL not in mined signals or tool results)."
        )
    if not gap.evidence:
        gap.grounded = False


def _validate_gaps(
    raw: list, warnings: list[str], known_urls: Optional[dict[str, bool]] = None
) -> list[Gap]:
    """Validate each object with Gap(**obj); drop invalid ones, noting why.

    When ``known_urls`` is given (live-LLM synthesis), every surviving gap is
    also passed through the grounding gate — see ``_ground_evidence``. Fixture
    output is validated with ``known_urls=None`` (shape-only) since the canned
    URLs are the demo harness, not fabrication.
    """
    gaps: list[Gap] = []
    for i, obj in enumerate(raw):
        if len(gaps) >= _MAX_GAPS:
            break
        if not isinstance(obj, dict):
            warnings.append(f"synthesis: dropped item {i} (not an object)")
            continue
        try:
            gap = Gap(**obj)
        except Exception as exc:  # noqa: BLE001 - pydantic ValidationError et al.
            title = obj.get("title", f"item {i}") if isinstance(obj, dict) else f"item {i}"
            # Keep the note short; full trace isn't useful in the UI.
            msg = str(exc).splitlines()[0]
            warnings.append(f"synthesis: dropped invalid gap '{title}': {msg}")
            continue
        if known_urls is not None:
            _ground_evidence(gap, known_urls, warnings)
        gaps.append(gap)
    return gaps


# --------------------------------------------------------------------------- #
# Public entry point.                                                          #
# --------------------------------------------------------------------------- #
async def synthesize(
    signals: ExtractedSignals,
    source_reports: list[SourceReport],
    client: ClaudeClient,
    model: Optional[str] = None,
    steering: str = "",
    avoid_titles: Optional[list[str]] = None,
) -> tuple[list[Gap], str, list[str]]:
    """Turn extracted signals into a validated list of market gaps.

    Returns ``(gaps, backend_used, warnings)`` where ``backend_used`` is the LLM
    backend that actually served the call (or ``"fixture"`` if we fell back).
    ``model`` optionally overrides which Claude model synthesizes; ``None`` uses
    the configured default. ``steering`` is an optional founder-context block
    prepended to the prompt so the company concepts fit their advantages/
    constraints. Never raises for ordinary failure modes — degrades to fixtures.
    """
    warnings: list[str] = []
    # The grounding set: every URL we actually saw (normalized url -> was its
    # source LIVE?). Seeded from the mined signals; corroboration tool fetches
    # append to it mid-synthesis via the url_sink.
    known_urls: dict[str, bool] = {}
    for sig in (*signals.demand, *signals.capability, *signals.supply):
        key = _norm_url(sig.url)
        if key:
            known_urls[key] = known_urls.get(key, False) or sig.live
    tools = _build_tools(signals, url_sink=known_urls)
    user_prompt = _build_user_prompt(signals, source_reports, steering, avoid_titles)

    backend = "fixture"
    text = ""
    try:
        result = await client.complete(
            user_prompt,
            system=SYSTEM_PROMPT,
            tools=tools,
            max_turns=8,
            timeout=240,
            model=model,
            # Open-web access so synthesis can actively hunt DEMAND — the mined API
            # sources are domain-limited (Stack Exchange sees dev-tool pain but not
            # academic robotics), and demand lives somewhere different per domain.
            # web_search finds the complaint/forum/thread the fixed adapters miss,
            # with a real URL that satisfies the grounding gate.
            web=True,
        )
        text = result.text
        backend = result.backend
        # Web-found URLs (web_search/web_fetch) the model actually received are
        # real evidence — add them to the grounding set so demand the fixed API
        # sources missed survives the grounding gate. Marked live: a web result is
        # a live fetch, not a mock fixture.
        for u in getattr(result, "tool_urls", []) or []:
            key = _norm_url(u)
            if key:
                known_urls.setdefault(key, True)
    except Exception as exc:  # noqa: BLE001 - resilient: fall through to fixtures
        warnings.append(f"synthesis: LLM call failed ({exc}); using fixture gaps.")
        text = ""

    raw = _extract_json_array(text) if text else None
    if raw is None:
        if text:
            warnings.append("synthesis: could not parse a JSON array from LLM output.")
        gaps: list[Gap] = []
    else:
        # The grounding gate only targets live-LLM output: the fixture backend
        # serves canned gaps whose URLs are the demo harness, not fabrication
        # (mock adapters never mined them), so it is validated shape-only.
        gate = None if backend == "fixture" else known_urls
        gaps = _validate_gaps(raw, warnings, known_urls=gate)

    if not gaps and raw == []:
        # An honest empty answer on thin evidence — surface it, don't paper over
        # it with fixtures.
        warnings.append(
            "synthesis: model returned zero gaps — the evidence was too thin to "
            "support any. This is honest behavior, not a failure."
        )
    elif not gaps:
        # Fallback: nothing parsed / nothing valid — use the canned fixture
        # synthesis so the pipeline still returns well-formed gaps.
        warnings.append("synthesis: no valid gaps from LLM; falling back to fixtures.")
        from ..llm.fixture_synthesis import FIXTURE_GAPS_JSON

        fixture_raw = _extract_json_array(FIXTURE_GAPS_JSON) or []
        gaps = _validate_gaps(fixture_raw, warnings)
        backend = "fixture"

    # Fixture honesty: canned gaps must never masquerade as findings. Mark the
    # payload unmistakably — a loud warning plus a 'fixture' tag and live=False
    # evidence on every gap — whether they came from the fixture LLM backend or
    # the zero-dependency fallback above.
    if backend == "fixture" and gaps:
        warnings.append(
            "synthesis: FIXTURE gaps — canned demo output (sample area: personal "
            "finance for freelancers), NOT findings for the requested area."
        )
        for g in gaps:
            if "fixture" not in g.tags:
                g.tags = ["fixture", *g.tags]
            for ev in g.evidence:
                ev.live = False

    return gaps, backend, warnings
