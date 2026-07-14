"""Stack Exchange source adapter — developer demand, keyless.

Signal thesis: a question asked on Stack Overflow (or a sibling site) is a person
describing a problem they have RIGHT NOW, in their own words, with a wallet-backed
context (they're doing paid work and stuck). Two shapes carry the strongest
market-gap signal:

  * **High-view, UNANSWERED questions** — demand with no good answer. "Lots of
    people hit this and nobody has solved it" is exactly the unmet-need signal
    the engine's paper/repo sources cannot see. This is the point of the adapter.
  * **High-view, high-score questions** — a hot pain even where an answer exists;
    a workaround people tolerate is a product waiting to happen.

This matters because the demand-bearing sources the engine had were unreliable:
Reddit 403s into fixtures, and Hacker News indexes launches more than daily pain.
Stack Exchange is keyless, deterministic, and squarely about developer pain — the
domains this tool explores (dev tools, infra, ML) live there.

Fetching is keyless via the Stack Exchange API 2.3 search/advanced endpoint
(https://api.stackexchange.com/2.3/search/advanced), no credentials required
(keyless quota is ~300 requests/day/IP — ample with caching). We query the area
keywords, sorted by relevance, and weight each question by views × recency with a
strong boost for being unanswered.

Contract (see ``base.py``): ``fetch`` NEVER raises. It degrades LIVE -> MOCK on any
network/parse failure, reports EMPTY when a clean query matched nothing, and caches
under ns=``ingest:stackexchange`` keyed by (area, query_terms) so reruns cost zero
requests. Rate-limit discipline mirrors the other keyless adapters: a hard per-run
request budget, a polite delay, and an immediate stop on 429/403 or a
``backoff``-carrying response.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..cache import get_cache
from ..config import get_settings
from ..schemas import RawItem, SourceName, SourceReport, SourceStatus
from .base import FetchResult, Source

_FIXTURE = Path(__file__).parent / "fixtures" / "stackexchange.json"

_SE_SEARCH = "https://api.stackexchange.com/2.3/search/advanced"
_HTTP_TIMEOUT = 15.0
_MONTHS_BACK = 30

# The sites we sweep. Stack Overflow is the bulk of developer pain; the others
# broaden into adjacent technical demand without a per-site request explosion.
_SITES = ("stackoverflow",)

_MAX_REQUESTS = 2
_REQUEST_DELAY = 0.7


class StackExchangeSource(Source):
    """Developer demand from Stack Exchange questions (keyless, cached)."""

    name = SourceName.STACKEXCHANGE

    @property
    def live(self) -> bool:
        # Keyless: always attempts a live fetch (no credential to gate on).
        return True

    def fetch(
        self, area: str, keywords: list[str], sub_segments: list[str]
    ) -> FetchResult:
        query_terms = _clean_terms(keywords or [area])
        cache = get_cache()

        cached = cache.get("ingest:stackexchange", area, query_terms)
        if cached is not None:
            items = [RawItem.model_validate(r) for r in cached.get("items", [])]
            report = SourceReport.model_validate(cached["report"])
            return FetchResult(items=items, report=report)

        result = self._fetch_live(area, query_terms)
        cache.set(
            "ingest:stackexchange",
            area,
            query_terms,
            {
                "items": [i.model_dump(mode="json") for i in result.items],
                "report": result.report.model_dump(mode="json"),
            },
        )
        return result

    # ------------------------------------------------------------------ #
    def _fetch_live(self, area: str, query_terms: list[str]) -> FetchResult:
        settings = get_settings()
        cutoff = int(
            (datetime.now(timezone.utc) - timedelta(days=_MONTHS_BACK * 30)).timestamp()
        )
        # Stack Exchange `q` is all-must-match, so a long joined query returns
        # nothing (the same trap arXiv had). Fire one request PER keyword instead,
        # up to the request budget, and merge — each term gets a fair search.
        queries = [t[:180] for t in query_terms[:_MAX_REQUESTS]] or [area[:180]]
        budget = {"remaining": _MAX_REQUESTS, "blocked": False, "first": True,
                  "last_status": None}

        items: list[RawItem] = []
        try:
            headers = {"User-Agent": "market-gap-finder/0.1"}
            with httpx.Client(timeout=_HTTP_TIMEOUT, headers=headers,
                              follow_redirects=True) as client:
                for q in queries:
                    if budget["blocked"] or budget["remaining"] <= 0:
                        break
                    for hit in self._search(client, q, _SITES[0], cutoff, budget):
                        item = self._to_raw_item(hit, _SITES[0])
                        if item is not None:
                            items.append(item)
        except Exception as exc:  # noqa: BLE001 - never raise out of an adapter
            if not items and settings.allow_mock:
                return self._mock(query_terms,
                                  note=f"live SE fetch errored ({type(exc).__name__}); mock")

        items = _dedupe(items)
        if not items:
            if budget["blocked"]:
                return self._mock(query_terms,
                                  note="Stack Exchange throttled (429/403/backoff); mock")
            if budget["last_status"]:
                return self._mock(query_terms,
                                  note=f"Stack Exchange error (HTTP {budget['last_status']}); mock")
            return self._empty(query_terms)

        items.sort(key=lambda i: i.weight, reverse=True)
        items = items[: max(1, int(getattr(settings, "stackexchange_hits", 30)))]
        report = SourceReport(
            name=self.name,
            status=SourceStatus.LIVE,
            item_count=len(items),
            freshest=_freshest(items),
            note="keyless Stack Exchange search (unanswered-weighted)",
            query_terms=query_terms,
        )
        return FetchResult(items=items, report=report)

    def _search(self, client: httpx.Client, q: str, site: str, cutoff: int,
                budget: dict) -> list[dict]:
        if budget["blocked"] or budget["remaining"] <= 0:
            return []
        if not budget["first"]:
            time.sleep(_REQUEST_DELAY)
        budget["first"] = False
        budget["remaining"] -= 1
        params = {
            "order": "desc",
            "sort": "relevance",
            "q": q,
            "site": site,
            "fromdate": cutoff,
            "pagesize": 40,
            # The default filter already returns title, tags, view_count,
            # answer_count, is_answered, score, link, creation_date — everything
            # the weighting needs. A custom filter id here silently returned empty.
        }
        try:
            resp = client.get(_SE_SEARCH, params=params)
        except Exception:  # noqa: BLE001
            budget["blocked"] = True
            return []
        if resp.status_code in (429, 403):
            budget["blocked"] = True
            return []
        if resp.status_code >= 400:
            budget["last_status"] = resp.status_code
            return []
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            budget["last_status"] = resp.status_code
            return []
        # The API signals throttling via a `backoff` field even on a 200 — honor it.
        if data.get("backoff"):
            budget["blocked"] = True
        return data.get("items", []) or []

    def _to_raw_item(self, hit: dict, site: str) -> RawItem | None:
        title = _unescape(str(hit.get("title", ""))).strip()
        if not title:
            return None
        qid = hit.get("question_id")
        link = hit.get("link", "") or (f"https://{site}.com/q/{qid}" if qid else "")
        created_i = hit.get("creation_date")
        created = (
            datetime.fromtimestamp(created_i, tz=timezone.utc)
            if isinstance(created_i, (int, float)) else None
        )
        views = int(hit.get("view_count", 0) or 0)
        score = int(hit.get("score", 0) or 0)
        answers = int(hit.get("answer_count", 0) or 0)
        is_answered = bool(hit.get("is_answered", False))

        # Weight: demand = views, recency-decayed, with a strong boost when the
        # question is UNANSWERED (unmet need) — that's the whole point.
        now = datetime.now(timezone.utc)
        months_old = ((now - created).days / 30) if created else 24
        recency = max(0.2, 1.0 - months_old / (_MONTHS_BACK + 6))
        unmet = 2.0 if not is_answered else (1.4 if answers == 0 else 1.0)
        weight = (views + score * 10) * recency * unmet

        excerpt = _unescape(str(hit.get("body_markdown") or hit.get("excerpt") or ""))
        return RawItem(
            source=self.name,
            id=str(qid or link or title)[:200],
            title=title,
            body=excerpt[:600],
            url=link,
            created=created,
            weight=round(weight, 2),
            meta={
                "views": views, "score": score, "answers": answers,
                "is_answered": is_answered, "site": site,
                "tags": hit.get("tags", []),
                # A demand-kind hint the extractor can lean on.
                "kind": "unanswered_pain" if not is_answered else "hot_pain",
            },
        )

    # -------------------------------------------------------------- mock/empty
    def _mock(self, query_terms: list[str], note: str) -> FetchResult:
        settings = get_settings()
        if not settings.allow_mock or not _FIXTURE.exists():
            return self._empty(query_terms, note=note)
        try:
            raw = json.loads(_FIXTURE.read_text())
        except Exception:  # noqa: BLE001
            return self._empty(query_terms, note=note)
        items = [RawItem.model_validate(r) for r in raw][:30]
        report = SourceReport(
            name=self.name, status=SourceStatus.MOCK, item_count=len(items),
            freshest=_freshest(items), note=note, query_terms=query_terms,
        )
        return FetchResult(items=items, report=report)

    def _empty(self, query_terms: list[str], note: str | None = None) -> FetchResult:
        return FetchResult(
            items=[],
            report=SourceReport(
                name=self.name, status=SourceStatus.EMPTY, item_count=0,
                note=note or "Stack Exchange returned no recent questions for these keywords",
                query_terms=query_terms,
            ),
        )


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _clean_terms(terms: list[str]) -> list[str]:
    seen: list[str] = []
    for t in terms:
        t = (t or "").strip()
        if t and t.lower() not in {s.lower() for s in seen}:
            seen.append(t)
    return seen[:6]


def _dedupe(items: list[RawItem]) -> list[RawItem]:
    out, seen = [], set()
    for it in items:
        if it.id in seen:
            continue
        seen.add(it.id)
        out.append(it)
    return out


def _freshest(items: list[RawItem]):
    dates = [i.created for i in items if i.created]
    return max(dates) if dates else None


_TAG_RE = re.compile(r"<[^>]+>")
_ENTS = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
         "&#215;": "×"}


def _unescape(s: str) -> str:
    s = _TAG_RE.sub("", s)
    for k, v in _ENTS.items():
        s = s.replace(k, v)
    return re.sub(r"\s+", " ", s).strip()
