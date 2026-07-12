"""Scout mode — the engine proposes ownable spaces (STRATEGY Phase 1 §5).

Domainless discovery: instead of the founder naming a domain, one cheap wide
pass over ALL registered source adapters ("what is hot right now") plus ONE
cheap-model LLM call turns trending complaint/velocity signals into 10-15
candidate DOMAINS shaped like ownable spaces — each carrying the exact input
signals that triggered it, ready to seed a full exploration.

Discipline (matching the rest of the codebase):

* **Grounding gate** (same as the synthesis evidence gate, eabae99): a candidate
  signal whose URL is not in the supplied input set is dropped; a candidate left
  with no grounded signals is dropped entirely. The LLM cannot invent evidence.
* **Degrade, don't crash**: adapters never raise; on any LLM failure (or output
  that won't parse/ground) we fall back to deterministic token-cluster
  candidates flagged ``degraded=True``. ``scout_spaces`` never raises.
* **Stateless v1**: nothing is persisted; the founder picks a candidate and
  launches a normal project from it.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter

from ..analysis.synthesize import _extract_json_array
from ..schemas import RawItem, SourceReport, SourceStatus
from ..sources.base import FetchResult
from ..sources.registry import get_sources
from .schemas import Project, ScoutCandidate, ScoutResponse, ScoutSignal

# The same cheap tier the engine uses for structural decomposition (SPEC §13.4).
# Read off the Project field default so the two can never drift apart.
DECOMPOSE_MODEL: str = Project.model_fields["decompose_model"].default

# Generic trend-oriented scope handed to every adapter. Each adapter interprets
# it through its own lens (HN Ask/Show/story, GitHub star velocity, Reddit
# rising pain, arXiv recency, newsletter attention) via its normal fetch path,
# so mock fallbacks and ingest caching keep working unchanged.
_TRENDING_AREA = "emerging tools and unmet needs getting traction right now"
TRENDING_QUERIES: list[str] = [
    "trending",
    "pain point",
    "alternative to",
    "why is there no",
    "growing fast",
    "new tool",
]

_MAX_ITEMS_PER_SOURCE = 8   # keep the single LLM call cheap: ~40 items max
_MAX_SNIPPET_CHARS = 200
_MAX_CANDIDATES = 15
_MAX_SIGNALS_PER_CANDIDATE = 4
_MAX_SUB_SEGMENTS = 3
_FALLBACK_MAX_CANDIDATES = 5

_SCOUT_SYSTEM = """\
You are the scout step of an autonomous market-gap explorer. You are given a
batch of items trending RIGHT NOW across Reddit, Hacker News, GitHub, arXiv and
tech newsletters. Propose 10-15 candidate DOMAINS shaped like ownable spaces a
founder could explore — specific enough to own (a niche, a wedge), broad enough
to decompose. Every candidate MUST cite 2-4 of the supplied items (by their
exact URLs) as its triggering signals; never invent or alter a URL. Honour the
founder's brief and avoid-list when given. Output ONLY a JSON array, no prose,
no fences."""


def _prompt(items: list[RawItem], brief: str, avoid: list[str]) -> str:
    lines: list[str] = []
    for it in items:
        snippet = (it.body or "").strip().replace("\n", " ")[:_MAX_SNIPPET_CHARS]
        line = f"- [{it.source.value}] {it.title.strip()} | {it.url}"
        if snippet:
            line += f" | {snippet}"
        lines.append(line)

    ctx = ""
    if brief and brief.strip():
        ctx += f"\n\nFOUNDER BRIEF (shape candidates toward this):\n{brief.strip()[:4000]}"
    terms = [a.strip() for a in avoid if a and a.strip()]
    if terms:
        ctx += "\n\nAVOID (propose nothing in these spaces): " + "; ".join(terms)

    return (
        "TRENDING ITEMS (the ONLY allowed signal URLs):\n"
        + "\n".join(lines)
        + ctx
        + "\n\nPropose 10-15 candidate domains. Return ONLY a JSON array shaped like:\n"
        '[{"domain": "the ownable space (short)", '
        '"rationale": "1-2 sentences naming the triggering signals", '
        '"signals": [{"source": "hackernews", "title": "...", "url": "<exact input url>"}], '
        '"suggested_sub_segments": ["0-3 concrete sub-segments"]}]'
    )


# --------------------------------------------------------------------------- #
# 1) Cheap wide signal pass — every adapter in parallel (pipeline._fetch_all   #
#    pattern), each through its own mock-safe fetch path.                      #
# --------------------------------------------------------------------------- #
async def _fetch_trending() -> tuple[list[RawItem], list[SourceReport]]:
    """Fetch 'what is hot now' from every registered source in parallel."""
    sources = get_sources()
    results: list[FetchResult] = await asyncio.gather(
        *(
            asyncio.to_thread(src.fetch, _TRENDING_AREA, TRENDING_QUERIES, [])
            for src in sources
        )
    )

    items: list[RawItem] = []
    reports: list[SourceReport] = []
    for src, result in zip(sources, results):
        if result is None:  # adapters are contractually non-None; guard anyway.
            result = FetchResult()
        reports.append(
            result.report
            or SourceReport(
                name=src.name,
                status=SourceStatus.EMPTY,
                item_count=len(result.items),
                note="Adapter returned no report; defaulted to EMPTY.",
            )
        )
        top = sorted(result.items, key=lambda i: i.weight, reverse=True)
        items.extend(top[:_MAX_ITEMS_PER_SOURCE])
    return items, reports


# --------------------------------------------------------------------------- #
# 2) Parse + ground the LLM output (same discipline as the evidence gate)      #
# --------------------------------------------------------------------------- #
def _candidates_from_llm(raw: object, items: list[RawItem]) -> list[ScoutCandidate]:
    """Schema-validate LLM candidates; drop every signal (and any candidate)
    not grounded in the supplied input URLs."""
    by_url = {it.url: it for it in items if it.url}
    out: list[ScoutCandidate] = []
    if not isinstance(raw, list):
        return out
    for obj in raw:
        if not isinstance(obj, dict):
            continue
        domain = str(obj.get("domain", "") or "").strip()
        rationale = str(obj.get("rationale", "") or "").strip()
        if not domain or not rationale:
            continue

        signals: list[ScoutSignal] = []
        raw_signals = obj.get("signals") or []
        if isinstance(raw_signals, list):
            for sig in raw_signals:
                if not isinstance(sig, dict):
                    continue
                url = str(sig.get("url", "") or "").strip()
                item = by_url.get(url)
                if item is None:
                    continue  # grounding gate: URL not in the input set → drop.
                title = str(sig.get("title", "") or "").strip() or item.title
                signals.append(
                    ScoutSignal(source=item.source.value, title=title, url=url)
                )
                if len(signals) >= _MAX_SIGNALS_PER_CANDIDATE:
                    break
        if not signals:
            continue  # a candidate with zero grounded signals is dropped whole.

        subs_raw = obj.get("suggested_sub_segments") or obj.get("sub_segments") or []
        subs = (
            [str(s).strip() for s in subs_raw if str(s).strip()][:_MAX_SUB_SEGMENTS]
            if isinstance(subs_raw, list)
            else []
        )
        out.append(
            ScoutCandidate(
                domain=domain[:200],
                rationale=rationale[:600],
                signals=signals,
                suggested_sub_segments=subs,
            )
        )
        if len(out) >= _MAX_CANDIDATES:
            break
    return out


# --------------------------------------------------------------------------- #
# 3) Deterministic fallback — rough token clusters, honestly flagged           #
# --------------------------------------------------------------------------- #
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]{3,}")
_STOPWORDS = frozenset(
    """
    this that with from have your what when where whic which will would could
    should about after before every their there these those them then than
    into over under between using used uses make makes made just still being
    been more most much many some also them why how who does dont cant wont
    show tell open free best good great year years month months week weeks
    today 2024 2025 2026 startup startups company companies people market
    tool tools software product products service services need needs
    """.split()
)


def _tokens(title: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(title.lower()) if t not in _STOPWORDS}


def _fallback_candidates(items: list[RawItem]) -> list[ScoutCandidate]:
    """Cluster trending items by shared significant title tokens.

    Deterministic and honest: each cluster is flagged ``degraded=True`` and
    carries the exact items that formed it. Produces up to 5 rough candidates;
    if nothing overlaps, one catch-all bundle of the strongest items."""
    linked = [it for it in items if it.url]
    counts: Counter[str] = Counter()
    for it in linked:
        counts.update(_tokens(it.title))

    out: list[ScoutCandidate] = []
    used_tokens: set[str] = set()
    for token, n in counts.most_common():
        if n < 2 or len(out) >= _FALLBACK_MAX_CANDIDATES:
            break
        if token in used_tokens:
            continue
        matched = [it for it in linked if token in _tokens(it.title)]
        matched = matched[:_MAX_SIGNALS_PER_CANDIDATE]
        srcs = sorted({it.source.value for it in matched})
        out.append(
            ScoutCandidate(
                domain=token.replace("-", " ").title(),
                rationale=(
                    f"Deterministic fallback cluster (no LLM): {len(matched)} trending "
                    f"items across {', '.join(srcs)} share the term '{token}'."
                ),
                signals=[
                    ScoutSignal(source=it.source.value, title=it.title, url=it.url)
                    for it in matched
                ],
                degraded=True,
            )
        )
        used_tokens.add(token)

    if not out and linked:
        top = sorted(linked, key=lambda i: i.weight, reverse=True)
        top = top[:_MAX_SIGNALS_PER_CANDIDATE]
        out.append(
            ScoutCandidate(
                domain="Unclustered trending signals",
                rationale=(
                    "Deterministic fallback (no LLM): no titles overlapped, so here "
                    "are the strongest trending items as one rough bundle."
                ),
                signals=[
                    ScoutSignal(source=it.source.value, title=it.title, url=it.url)
                    for it in top
                ],
                degraded=True,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Avoid-list filtering (applies to LLM and fallback candidates alike)          #
# --------------------------------------------------------------------------- #
def _apply_avoid(
    candidates: list[ScoutCandidate], avoid: list[str]
) -> list[ScoutCandidate]:
    terms = [a.strip().lower() for a in avoid or [] if a and a.strip()]
    if not terms:
        return candidates

    def _matches(cand: ScoutCandidate) -> bool:
        hay = " ".join(
            [cand.domain, cand.rationale, *cand.suggested_sub_segments]
        ).lower()
        return any(t in hay for t in terms)

    return [c for c in candidates if not _matches(c)]


# --------------------------------------------------------------------------- #
# Public entry point                                                           #
# --------------------------------------------------------------------------- #
async def scout_spaces(
    brief: str, avoid: list[str], client, model: str
) -> ScoutResponse:
    """Propose 10-15 candidate domains from what is trending now. NEVER raises.

    One parallel adapter pass + ONE cheap-model LLM call. Degrades to
    deterministic token clusters (``degraded=True``) on any LLM failure, and to
    an empty candidate list only if the sources themselves returned nothing.
    """
    try:
        items, reports = await _fetch_trending()
    except Exception:  # noqa: BLE001 - adapters shouldn't raise; belt-and-braces.
        items, reports = [], []

    candidates: list[ScoutCandidate] = []
    if items:
        try:
            result = await client.complete(
                _prompt(items, brief, avoid),
                system=_SCOUT_SYSTEM,
                max_turns=1,
                timeout=120,
                model=model,
            )
            candidates = _candidates_from_llm(
                _extract_json_array(result.text or ""), items
            )
        except Exception:  # noqa: BLE001 - scout is best-effort; fall through.
            candidates = []
        if not candidates:
            candidates = _fallback_candidates(items)

    return ScoutResponse(candidates=_apply_avoid(candidates, avoid), sources=reports)
