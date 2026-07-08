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
)

# How many of each signal kind to actually put in the prompt. The extractor may
# hand us more; we keep the strongest-N so the prompt stays dense, not bloated.
_MAX_DEMAND = 40
_MAX_CAPABILITY = 24
_MAX_SUPPLY = 24
# Cap the number of gaps we accept from a single run (spec: 4-8).
_MAX_GAPS = 8
# Per-tool-call item cap when summarizing a corroboration fetch back to the model.
_TOOL_ITEM_CAP = 8


# --------------------------------------------------------------------------- #
# System prompt — the "brain". This is intentionally opinionated: it is what   #
# separates a bland list of ideas from sharp, defensible, non-obvious gaps.    #
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """\
You are the analytical engine of "Market Gap Finder" — a tool that finds
*real, enterable* market gaps, not brainstorm fluff. You reason like a
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
search_github, search_newsletters. Use them SPARINGLY and only to corroborate a
specific hypothesis (e.g., "is there really pain about X?", "did anyone already
ship this?", or "did the tooling actually take off?"). Every URL you cite from a
tool result is fair game as Evidence. If no tools are available, reason only from
the provided signals — do not pretend to have fetched anything.

# COMPETITORS
For each gap, name up to 5 real, specific incumbents/alternatives. For each:
its actual positioning, the segment it targets, a rough price_tier
(free / $ / $$ / $$$ / enterprise), and — most important — the specific
weakness / blind spot that leaves THIS gap open. Generic weaknesses ("bad UX")
are worthless; name the structural reason they can't or won't serve this need.

# SCORING (each 1..5, higher = more attractive)
- demand_strength: how loud/urgent/repeated is the unmet need?
- competitive_openness: how open is the field? (5 = wide open, 1 = crowded)
- trend_tailwind: how strong is the "why now" tailwind?
- feasibility: how buildable by a small team in <6 months?
- willingness_to_pay: will the segment actually pay, and roughly how much?
Also set novelty 1..5: how non-obvious / creative is this framing?
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
- expansion_path: the wedge → product → platform arc — how a sharp first wedge
  grows into a durable company, not a one-trick feature.
- moat: the durable advantage once you're working (proprietary data, network
  effects, distribution, switching cost, or hard tech).
- standalone: true only if this can stand alone as a company; false if honestly
  it's a feature of something bigger.
- standalone_reason: one or two sentences — why it's a company (or, if false,
  why it's really just a feature). Be honest; a false here should drag the gap
  down, not be hidden.
Bias hard toward company-scale ideas. If a candidate is only a feature, either
reframe it into the genuine company around it or drop it.

# OUTPUT CONTRACT — STRICT
Return ONLY a top-level JSON array (no prose, no markdown fences) of 4-8 gap
objects. Each object MUST have exactly this shape:

{
  "title": "short punchy name for the gap",
  "thesis": "one sentence: the bet in a breath",
  "company": {
    "product": "what you actually build (2-3 concrete sentences)",
    "icp": "the specific first customer you sell to",
    "business_model": "how it earns + rough pricing shape",
    "expansion_path": "wedge -> product -> platform arc",
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
  "wedge": "the sharp initial entry point / first customer",
  "riskiest_assumption": "the one thing that must be validated first",
  "weakest_link": "the softest score or biggest doubt, named",
  "why_now": "the recent shift that unlocks this gap",
  "empty_for_a_reason": false,
  "empty_reason": "if true, why the white space is actually a trap",
  "novelty": 1-5,
  "sub_segment": "which sub-segment this serves",
  "tags": ["short", "keywords"]
}

Rules: 3-6 evidence items per gap, each with a real URL from the signals/tools.
Up to 5 competitors, each with a specific weakness. Output the JSON array and
NOTHING else.\
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


def _make_tool_handler(name: SourceName, area: str, sub_segments: list[str]):
    """Build the async handler that fetches from one source for a given query.

    The handler NEVER raises — on any failure it returns a short text note so the
    model can adapt. `fetch` is synchronous per the Source ABC, so we run it in a
    worker thread to stay non-blocking.
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
        return _summarize_items(name, items, note)

    return _handler


def build_corroboration_tools(area: str, sub_segments: list[str]) -> list[ToolSpec]:
    """One live ``search_*`` corroboration tool per source, bound to ``area``.

    Public factory so callers outside synthesis (notably the autonomous
    pressure-test path, SPEC §5) can hand the same fresh-evidence tools to a
    red-team model. The handlers take the *query* from the model and keep the
    given ``area``/``sub_segments`` as scope; each NEVER raises (returns a text
    note on failure) so tool use can't crash a run.
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
    ]
    return [
        ToolSpec(
            name=tool_name,
            description=desc,
            input_schema=schema,
            handler=_make_tool_handler(src, area, sub_segments),
        )
        for tool_name, src, desc in specs
    ]


def _build_tools(signals: ExtractedSignals) -> list[ToolSpec]:
    """Corroboration tools scoped to a segment's mined ``ExtractedSignals``."""
    return build_corroboration_tools(signals.area, signals.sub_segments)


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
        }

    def _cap(c):
        return {
            "source": c.source.value,
            "description": c.description,
            "url": c.url,
            "momentum": round(c.momentum, 3),
            "date": c.date.date().isoformat() if c.date else None,
        }

    def _sup(s):
        return {
            "source": s.source.value,
            "name": s.name,
            "hint": s.hint,
            "url": s.url,
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
    signals: ExtractedSignals, reports: list[SourceReport], steering: str = ""
) -> str:
    payload = _signals_payload(signals)
    sources = _sources_summary(reports)
    steer = (steering.strip() + "\n\n") if steering and steering.strip() else ""
    return (
        f"{steer}"
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
        "hypotheses. Then output ONLY the strict JSON array of 4-8 grounded gap "
        "objects — most attractive first. No prose, no markdown."
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


def _validate_gaps(raw: list, warnings: list[str]) -> list[Gap]:
    """Validate each object with Gap(**obj); drop invalid ones, noting why."""
    gaps: list[Gap] = []
    for i, obj in enumerate(raw):
        if len(gaps) >= _MAX_GAPS:
            break
        if not isinstance(obj, dict):
            warnings.append(f"synthesis: dropped item {i} (not an object)")
            continue
        try:
            gaps.append(Gap(**obj))
        except Exception as exc:  # noqa: BLE001 - pydantic ValidationError et al.
            title = obj.get("title", f"item {i}") if isinstance(obj, dict) else f"item {i}"
            # Keep the note short; full trace isn't useful in the UI.
            msg = str(exc).splitlines()[0]
            warnings.append(f"synthesis: dropped invalid gap '{title}': {msg}")
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
    tools = _build_tools(signals)
    user_prompt = _build_user_prompt(signals, source_reports, steering)

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
        )
        text = result.text
        backend = result.backend
    except Exception as exc:  # noqa: BLE001 - resilient: fall through to fixtures
        warnings.append(f"synthesis: LLM call failed ({exc}); using fixture gaps.")
        text = ""

    raw = _extract_json_array(text) if text else None
    if raw is None:
        if text:
            warnings.append("synthesis: could not parse a JSON array from LLM output.")
        gaps: list[Gap] = []
    else:
        gaps = _validate_gaps(raw, warnings)

    # Fallback: if nothing valid survived, use the canned fixture synthesis so
    # the pipeline always returns real, well-formed gaps.
    if not gaps:
        warnings.append("synthesis: no valid gaps from LLM; falling back to fixtures.")
        from ..llm.fixture_synthesis import FIXTURE_GAPS_JSON

        fixture_raw = _extract_json_array(FIXTURE_GAPS_JSON) or []
        gaps = _validate_gaps(fixture_raw, warnings)
        backend = "fixture"

    return gaps, backend, warnings
