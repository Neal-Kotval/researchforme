"""arXiv source adapter.

Signal thesis: arXiv is a *capability tailwind* proxy. When a cluster of
keywords accrues a burst of recent papers, the underlying capability just got
cheaper / more feasible — a "why now" for a market gap. We therefore fetch the
last ~18 months of matching papers, sorted newest-first, and turn publication
*volume + recency* into a per-item momentum weight (recent papers score higher).

Contract:
  - Never raises. Any network / parse / dependency failure falls back to a rich
    local fixture and reports MOCK; a clean-but-empty query reports EMPTY.
  - Real calls hit the open arXiv Atom API (no key required), parsed with
    feedparser. Results are cached under ns='ingest:arxiv'.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from ..cache import get_cache
from ..config import get_settings
from ..schemas import RawItem, SourceName, SourceReport, SourceStatus
from .base import FetchResult, Source

# HTTPS on purpose: the http:// endpoint 301-redirects, and httpx does not follow
# redirects by default — that silently downgraded every live fetch to the fixture.
_ARXIV_API = "https://export.arxiv.org/api/query"
_FIXTURE = Path(__file__).parent / "fixtures" / "arxiv.json"
_HTTP_TIMEOUT = 15.0


class ArxivSource(Source):
    """Recent-publication momentum from arXiv as a feasibility tailwind."""

    name = SourceName.ARXIV
    description = (
        "arXiv recent-paper momentum: bursts of new research on a keyword signal "
        "that the underlying capability just became feasible (a 'why now')."
    )

    @property
    def live(self) -> bool:
        # arXiv is an open API — always "live-capable". We still fall back to the
        # fixture if the network (or feedparser) is unavailable at call time.
        return get_settings().arxiv_live

    # ------------------------------------------------------------------ #
    # Public entry point                                                 #
    # ------------------------------------------------------------------ #
    def fetch(self, area: str, keywords: list[str], sub_segments: list[str]) -> FetchResult:
        settings = get_settings()
        query_terms = _clean_terms(keywords or [area])
        cache = get_cache()

        # --- cache lookup (raw Atom-derived rows) ---------------------------
        cached = cache.get("ingest:arxiv", area, query_terms)
        if cached is not None:
            return self._result_from_rows(cached, query_terms, cached_status=True)

        # --- real fetch, degrading to the fixture on any failure ------------
        try:
            rows = self._fetch_live(query_terms, settings.arxiv_max_results,
                                    settings.arxiv_months_back)
            status = SourceStatus.LIVE
            note: str | None = None
            if not rows:
                # Query succeeded but nothing recent matched.
                rows = []
                status = SourceStatus.EMPTY
                note = "arXiv returned no recent papers for these keywords."
        except Exception as exc:  # never raise — degrade to mock
            rows = _load_fixture()
            status = SourceStatus.MOCK
            note = f"Live arXiv fetch failed ({type(exc).__name__}); using fixture. {exc}"

        # Cache only genuinely-live rows so we don't pin mock data.
        if status is SourceStatus.LIVE and rows:
            cache.set("ingest:arxiv", rows, area, query_terms)

        return self._result_from_rows(
            rows, query_terms, cached_status=False, status=status, note=note
        )

    # ------------------------------------------------------------------ #
    # Live path                                                          #
    # ------------------------------------------------------------------ #
    def _fetch_live(self, terms: list[str], max_results: int, months_back: int) -> list[dict]:
        """Query the arXiv Atom API and return normalized paper rows.

        Raises on network / dependency error so `fetch` can fall back to mock.
        """
        import feedparser  # lazy: a missing dep should degrade, not crash import

        search_query = _build_search_query(terms)
        params = {
            "search_query": search_query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "start": 0,
            "max_results": max(1, int(max_results)),
        }
        resp = httpx.get(_ARXIV_API, params=params, timeout=_HTTP_TIMEOUT,
                         headers={"User-Agent": "market-gap-finder/0.1"},
                         follow_redirects=True)
        resp.raise_for_status()

        feed = feedparser.parse(resp.text)
        cutoff = datetime.now(timezone.utc) - timedelta(days=months_back * 30)

        rows: list[dict] = []
        for entry in getattr(feed, "entries", []):
            published = _parse_entry_date(entry)
            if published is not None and published < cutoff:
                continue  # older than our momentum window
            rows.append(_row_from_entry(entry, published))
        return rows

    # ------------------------------------------------------------------ #
    # Shared row -> RawItem mapping                                      #
    # ------------------------------------------------------------------ #
    def _result_from_rows(
        self,
        rows: list[dict],
        query_terms: list[str],
        *,
        cached_status: bool,
        status: SourceStatus | None = None,
        note: str | None = None,
    ) -> FetchResult:
        # Momentum: recency-weighted. Newest papers carry the strongest tailwind.
        now = datetime.now(timezone.utc)
        items: list[RawItem] = []
        freshest: datetime | None = None

        for row in rows:
            created = _coerce_dt(row.get("published"))
            if created is not None and (freshest is None or created > freshest):
                freshest = created
            momentum = _momentum(created, now)
            items.append(
                RawItem(
                    source=SourceName.ARXIV,
                    id=str(row.get("id") or row.get("link") or row.get("title", ""))[:200],
                    title=str(row.get("title", "")).strip(),
                    body=str(row.get("abstract", "")).strip(),
                    url=str(row.get("link", "")),
                    created=created,
                    weight=momentum,
                    meta={
                        "categories": row.get("categories", []),
                        "authors": row.get("authors", []),
                        "momentum": round(momentum, 4),
                    },
                )
            )

        if status is None:
            status = SourceStatus.LIVE if items else SourceStatus.EMPTY
        if cached_status and note is None:
            note = "Served from ingest cache."

        report = SourceReport(
            name=SourceName.ARXIV,
            status=status,
            item_count=len(items),
            freshest=freshest,
            note=note,
            query_terms=query_terms,
        )
        return FetchResult(items=items, report=report)


# --------------------------------------------------------------------------- #
# Helpers (module-level; pure functions)                                      #
# --------------------------------------------------------------------------- #
def _clean_terms(terms: list[str]) -> list[str]:
    """Dedupe, trim, drop empties; keep a sane cap for the query."""
    seen: list[str] = []
    for t in terms:
        t = (t or "").strip()
        if t and t.lower() not in {s.lower() for s in seen}:
            seen.append(t)
    return seen[:6]


# Words carrying no retrieval signal inside an AND-of-words clause.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "its", "no", "not", "of", "on", "or", "that", "the", "there", "this",
    "to", "why", "with", "new", "using", "based",
}


def _term_clause(term: str) -> str:
    """One term → an arXiv clause that can actually match.

    An exact-phrase clause (``all:"protein language model latent space"``) hits
    zero papers for anything longer than a few words, and the engine feeds us
    long descriptive keywords — which silently emptied the source. So we OR the
    phrase together with an AND-of-its-content-words form, which matches papers
    discussing the same concept in different wording:

        all:"surgical imitation learning"
        OR (all:"surgical" AND all:"imitation" AND all:"learning")
    """
    phrase = re.sub(r'["\\]', " ", term)
    phrase = re.sub(r"\s+", " ", phrase).strip()
    if not phrase:
        return ""

    words = [w for w in re.findall(r"[A-Za-z0-9+#-]{2,}", phrase)
             if w.lower() not in _STOPWORDS]
    # Keep the AND-clause tight; too many required words is as brittle as a phrase.
    words = words[:4]
    if len(words) < 2:
        return f'all:"{phrase}"'

    conjunction = " AND ".join(f'all:"{w}"' for w in words)
    return f'(all:"{phrase}" OR ({conjunction}))'


def _build_search_query(terms: list[str]) -> str:
    """OR together one matchable clause per keyword, over title+abstract."""
    if not terms:
        return "all:artificial intelligence"
    clauses = [c for c in (_term_clause(t) for t in terms) if c]
    return " OR ".join(clauses) or "all:artificial intelligence"


def _row_from_entry(entry: Any, published: datetime | None) -> dict:
    """Normalize a feedparser entry into our storable/JSON-able row shape."""
    categories = []
    for tag in getattr(entry, "tags", []) or []:
        term = getattr(tag, "term", None) or (tag.get("term") if isinstance(tag, dict) else None)
        if term:
            categories.append(term)

    authors = []
    for a in getattr(entry, "authors", []) or []:
        nm = getattr(a, "name", None) or (a.get("name") if isinstance(a, dict) else None)
        if nm:
            authors.append(nm)

    link = getattr(entry, "link", "") or ""
    raw_id = getattr(entry, "id", "") or link

    return {
        "id": raw_id,
        "title": re.sub(r"\s+", " ", getattr(entry, "title", "") or "").strip(),
        "abstract": re.sub(r"\s+", " ", getattr(entry, "summary", "") or "").strip(),
        "categories": categories,
        "authors": authors,
        "published": published.isoformat() if published else None,
        "link": link,
    }


def _parse_entry_date(entry: Any) -> datetime | None:
    """Pull a UTC-aware datetime from a feedparser entry."""
    struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if struct is not None:
        try:
            return datetime(*struct[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    for key in ("published", "updated"):
        val = getattr(entry, key, None)
        dt = _coerce_dt(val)
        if dt is not None:
            return dt
    return None


def _coerce_dt(val: Any) -> datetime | None:
    """Best-effort parse of an ISO-ish date string / datetime into UTC-aware."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    s = str(val).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        # arXiv sometimes uses '%Y-%m-%dT%H:%M:%SZ'; try a couple of fallbacks.
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(str(val).strip(), fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _momentum(created: datetime | None, now: datetime) -> float:
    """Recency-decayed momentum in 0..1.

    A paper published today scores ~1.0; the weight halves roughly every ~6
    months, so a burst of recent work reads as a strong capability tailwind
    while stale work fades out. Undated items get a neutral 0.3.
    """
    if created is None:
        return 0.3
    age_days = max(0.0, (now - created).total_seconds() / 86400.0)
    half_life_days = 180.0
    return round(math.pow(0.5, age_days / half_life_days), 4)


def _load_fixture() -> list[dict]:
    """Load the bundled realistic mock papers. Returns [] if unreadable."""
    try:
        import json

        return json.loads(_FIXTURE.read_text())
    except Exception:
        return []
