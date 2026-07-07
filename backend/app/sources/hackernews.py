"""Hacker News source adapter.

Signal thesis: Hacker News is a *tech-native demand + supply* radar. Three item
shapes carry different meaning for a market-gap hunt:

  * **Ask HN** ("is there a tool that...", "why is there no...", complaints about
    existing options) — the strongest *demand* / unmet-need signal. These read as
    builders and operators describing pain in their own words.
  * **Show HN** — a *launch* / supply hint: someone already shipped something in
    the space. We tag these ``kind="show"`` so the extractor can treat them as
    incumbent/adjacent supply rather than pure demand.
  * **High-point stories** — *momentum*: a link the community pushed to the top,
    a proxy for "this topic is heating up right now" (a why-now tailwind).

Fetching is keyless via the Algolia HN Search API
(https://hn.algolia.com/api/v1/search), which needs no credentials. We query the
area keywords across ``ask_hn``, ``show_hn`` and ``story`` tags, restricted to
roughly the last 18 months via ``numericFilters=created_at_i>``. Points +
recency become the item weight so fresher, more-upvoted pain floats to the top.

Contract (see ``base.py``): ``fetch`` NEVER raises. It degrades LIVE -> MOCK on
any network/parse failure (a rich local fixture keeps the whole pipeline
runnable), reports EMPTY when a clean query matched nothing, and caches
everything under ns=``ingest:hackernews`` keyed by (area, query_terms) so reruns
and reweights cost zero requests. Rate-limit discipline mirrors reddit.py: a
hard per-run request budget, a polite delay between calls, and an immediate stop
on HTTP 429/403.
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

_FIXTURE = Path(__file__).parent / "fixtures" / "hackernews.json"

_ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"
_HTTP_TIMEOUT = 15.0
_HN_ITEM = "https://news.ycombinator.com/item?id={id}"

# HN item tags we care about. We fire one query per tag (Ask HN / Show HN /
# story) so each kind is represented, rather than letting stories drown out the
# rarer-but-higher-signal Ask/Show posts.
_TAG_QUERIES = ["ask_hn", "show_hn", "story"]

# Keyless politeness: Algolia is generous but unauthenticated, so we still cap
# the number of requests per run, pause between them, and stop on 429/403.
_MAX_REQUESTS = 4          # <= len(_TAG_QUERIES) + headroom
_REQUEST_DELAY = 0.7       # seconds between calls
_MONTHS_BACK = 18
_HITS_FLOOR = 10           # never ask Algolia for fewer than this per query

# Crude HTML tag stripper for Algolia's ``*_text`` fields (they can carry <p>,
# <a>, <i> markup and HTML entities). We keep it dependency-free.
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#x27;": "'",
    "&#x2F;": "/",
    "&#39;": "'",
    "&nbsp;": " ",
}


class HackerNewsSource(Source):
    """Tech-native demand/supply/momentum radar over Hacker News (Algolia)."""

    name = SourceName.HACKERNEWS
    description = (
        "Mines Hacker News via the keyless Algolia API: Ask HN pain/complaints as "
        "demand, Show HN launches as supply hints, and high-point stories as "
        "topic momentum. Points and recency weight the strongest signal."
    )

    @property
    def live(self) -> bool:
        # Algolia HN search is keyless -> always live-capable. We still fall back
        # to the fixture if the network is unavailable / rate-limited at runtime.
        return get_settings().hackernews_live

    # --------------------------------------------------------------------- #
    # Public entry point                                                    #
    # --------------------------------------------------------------------- #
    def fetch(
        self, area: str, keywords: list[str], sub_segments: list[str]
    ) -> FetchResult:
        query_terms = self._query_terms(area, keywords)

        # Serve a warm ingest for this scope if we have one (zero requests).
        cache = get_cache()
        cached = cache.get("ingest:hackernews", area, query_terms)
        if cached is not None:
            return self._from_cache(cached, query_terms)

        result = self._fetch_live(area, query_terms)

        # Cache the serialized items/report for cheap reruns & reweights.
        try:
            cache.set(
                "ingest:hackernews",
                {
                    "items": [i.model_dump(mode="json") for i in result.items],
                    "report": result.report.model_dump(mode="json")
                    if result.report
                    else None,
                },
                area,
                query_terms,
            )
        except Exception:  # caching is best-effort; never break the fetch
            pass

        return result

    # --------------------------------------------------------------------- #
    # Live path (keyless Algolia)                                           #
    # --------------------------------------------------------------------- #
    def _fetch_live(self, area: str, query_terms: list[str]) -> FetchResult:
        """Query Algolia per tag under a strict budget; degrade to mock on error.

        One query per tag (ask_hn / show_hn / story), each restricted to the last
        ~18 months. Any transport/parse failure, or a hard rate-limit, degrades to
        the fixture (MOCK) so the pipeline still runs. A clean-but-empty result
        reports EMPTY.
        """
        settings = get_settings()
        hits_per_query = max(_HITS_FLOOR, int(settings.hackernews_hits))
        query = self._algolia_query(query_terms)
        cutoff_i = int(
            (datetime.now(timezone.utc) - timedelta(days=_MONTHS_BACK * 30)).timestamp()
        )
        budget = {"remaining": _MAX_REQUESTS, "blocked": False, "first": True}

        items: list[RawItem] = []
        try:
            headers = {"User-Agent": "market-gap-finder/0.1"}
            with httpx.Client(timeout=_HTTP_TIMEOUT, headers=headers) as client:
                for tag in _TAG_QUERIES:
                    if budget["blocked"] or budget["remaining"] <= 0:
                        break
                    hits = self._search_tag(
                        client, query, tag, hits_per_query, cutoff_i, budget
                    )
                    for hit in hits:
                        item = self._to_raw_item(hit, tag)
                        if item is not None:
                            items.append(item)
        except Exception as exc:  # never raise out of an adapter
            if not items and settings.allow_mock:
                return self._fetch_mock(
                    query_terms,
                    note=f"live HN fetch errored ({type(exc).__name__}); mock",
                )

        items = self._dedupe(items)
        if not items:
            # Distinguish "blocked" from "nothing matched" for honest telemetry.
            if budget["blocked"]:
                note = "HN Algolia rate-limited (429/403); served mock"
                return self._fetch_mock(query_terms, note=note)
            if budget.get("last_status"):
                note = f"HN Algolia error (HTTP {budget['last_status']}); served mock"
                return self._fetch_mock(query_terms, note=note)
            # Query genuinely returned nothing recent.
            return self._empty_report(
                query_terms,
                SourceStatus.EMPTY,
                "HN Algolia returned no recent Ask/Show/stories for these keywords",
            )

        items.sort(key=lambda i: i.weight, reverse=True)
        items = items[: max(1, int(settings.hackernews_hits))]
        report = SourceReport(
            name=self.name,
            status=SourceStatus.LIVE,
            item_count=len(items),
            freshest=self._freshest(items),
            note="keyless HN Algolia (ask_hn + show_hn + story)",
            query_terms=query_terms,
        )
        return FetchResult(items=items, report=report)

    def _search_tag(
        self,
        client: httpx.Client,
        query: str,
        tag: str,
        hits: int,
        cutoff_i: int,
        budget: dict,
    ) -> list[dict]:
        """One budgeted Algolia search for a single tag. Swallows all errors.

        Enforces the inter-request delay and stops the whole run on 429/403 by
        flipping ``budget['blocked']`` (respect the rate limit)."""
        if budget["blocked"] or budget["remaining"] <= 0:
            return []
        if not budget["first"]:
            time.sleep(_REQUEST_DELAY)
        budget["first"] = False
        budget["remaining"] -= 1

        params = {
            "query": query,
            "tags": tag,
            "numericFilters": f"created_at_i>{cutoff_i}",
            "hitsPerPage": hits,
        }
        try:
            resp = client.get(_ALGOLIA_SEARCH, params=params)
        except Exception:
            return []
        if resp.status_code in (429, 403):
            budget["blocked"] = True
            budget["last_status"] = resp.status_code
            return []
        if resp.status_code >= 400:
            budget["last_status"] = resp.status_code
            return []
        try:
            return resp.json().get("hits", []) or []
        except Exception:
            return []

    # --------------------------------------------------------------------- #
    # Shared hit -> RawItem mapping (used by both live and mock)            #
    # --------------------------------------------------------------------- #
    def _to_raw_item(self, hit: dict, tag: str) -> RawItem | None:
        """Map an Algolia HN hit to a RawItem, tagging its ``kind``.

        ``kind`` drives downstream interpretation: ``ask`` -> demand/pain,
        ``show`` -> supply/launch hint, ``story`` -> momentum.
        """
        object_id = hit.get("objectID")
        if not object_id:
            return None

        title = (hit.get("title") or hit.get("story_title") or "").strip()
        body = self._clean_html(hit.get("story_text") or hit.get("comment_text") or "")
        if not title and not body:
            return None

        created = self._parse_created(hit)
        points = self._as_int(hit.get("points"))
        num_comments = self._as_int(hit.get("num_comments"))
        kind = self._kind(hit, tag)
        tags = list(hit.get("_tags") or [])

        return RawItem(
            source=self.name,
            id=str(object_id),
            title=title,
            body=body,
            url=_HN_ITEM.format(id=object_id),
            created=created,
            weight=self._weight(points, created, kind),
            meta={
                "kind": kind,
                "points": points,
                "num_comments": num_comments,
                "author": hit.get("author") or "",
                "hn_id": str(object_id),
                "tags": tags,
            },
        )

    # --------------------------------------------------------------------- #
    # Mock path                                                             #
    # --------------------------------------------------------------------- #
    def _fetch_mock(self, query_terms: list[str], note: str) -> FetchResult:
        """Load fixture hits -> RawItems. Robust to a missing/broken fixture."""
        try:
            payload = json.loads(_FIXTURE.read_text())
            hits = payload.get("hits", [])
        except Exception as exc:
            return self._empty_report(
                query_terms,
                SourceStatus.MOCK,
                f"mock fixture unavailable: {type(exc).__name__}",
            )

        items: list[RawItem] = []
        for hit in hits:
            # Derive the tag from the fixture's own _tags so kind is accurate.
            tag = self._tag_from_hit(hit)
            item = self._to_raw_item(hit, tag)
            if item is not None:
                items.append(item)

        items = self._dedupe(items)
        items.sort(key=lambda i: i.weight, reverse=True)
        report = SourceReport(
            name=self.name,
            status=SourceStatus.MOCK,
            item_count=len(items),
            freshest=self._freshest(items),
            note=note,
            query_terms=query_terms,
        )
        return FetchResult(items=items, report=report)

    # --------------------------------------------------------------------- #
    # Cache rehydration                                                     #
    # --------------------------------------------------------------------- #
    def _from_cache(self, cached: dict, query_terms: list[str]) -> FetchResult:
        try:
            items = [RawItem.model_validate(i) for i in cached.get("items", [])]
            rep_raw = cached.get("report")
            report = SourceReport.model_validate(rep_raw) if rep_raw else None
        except Exception:
            # Corrupt cache entry -> re-derive a mock so we never raise.
            return self._fetch_mock(query_terms, note="cache miss (corrupt); mock")
        return FetchResult(items=items, report=report)

    # --------------------------------------------------------------------- #
    # Helpers                                                               #
    # --------------------------------------------------------------------- #
    def _query_terms(self, area: str, keywords: list[str]) -> list[str]:
        """Build the ordered, de-duplicated search-term list from the scope."""
        terms: list[str] = []
        for t in [area, *keywords]:
            t = (t or "").strip()
            if t and t.lower() not in {x.lower() for x in terms}:
                terms.append(t)
        return terms

    @staticmethod
    def _algolia_query(query_terms: list[str]) -> str:
        """Algolia does a full-text match on a plain string; join the top terms.

        We keep it short (Algolia ranks by relevance) and drop internal quotes so
        the query string stays clean."""
        joined = " ".join(t.replace('"', " ") for t in query_terms[:5]).strip()
        return _WS_RE.sub(" ", joined) or "software"

    def _kind(self, hit: dict, tag: str) -> str:
        """Classify the item as ask / show / story.

        Prefer the hit's own ``_tags`` (authoritative) and fall back to the query
        tag we fetched it under, then to a title-prefix sniff."""
        hit_tags = {str(t).lower() for t in (hit.get("_tags") or [])}
        if "ask_hn" in hit_tags:
            return "ask"
        if "show_hn" in hit_tags:
            return "show"
        if tag == "ask_hn":
            return "ask"
        if tag == "show_hn":
            return "show"
        title = (hit.get("title") or hit.get("story_title") or "").lstrip().lower()
        if title.startswith("ask hn"):
            return "ask"
        if title.startswith("show hn"):
            return "show"
        return "story"

    @staticmethod
    def _tag_from_hit(hit: dict) -> str:
        """Best-guess the query tag for a fixture hit from its ``_tags``."""
        hit_tags = {str(t).lower() for t in (hit.get("_tags") or [])}
        if "ask_hn" in hit_tags:
            return "ask_hn"
        if "show_hn" in hit_tags:
            return "show_hn"
        return "story"

    def _weight(self, points: int, created: datetime | None, kind: str) -> float:
        """Points as the base signal, boosted for recency and by item kind.

        HN points read like Reddit upvotes: raw community magnitude. We keep that
        magnitude readable, decay it gently with age (last ~90 days near full,
        floor 0.5x by a year+), then nudge by kind so the pipeline sees the
        strongest *demand* first: Ask HN pain is boosted, high-point stories
        (momentum) get a mild bump, and Show HN launches (supply) sit at baseline.
        """
        base = float(max(0, points))
        if created is not None:
            now = datetime.now(timezone.utc)
            days = max(0.0, (now - created).total_seconds() / 86400.0)
            recency = max(0.5, 1.0 - (days / 365.0) * 0.4)  # 1.0 -> ~0.6 at 1yr
        else:
            recency = 0.8
        kind_factor = {"ask": 1.35, "story": 1.1, "show": 1.0}.get(kind, 1.0)
        return round(base * recency * kind_factor, 2)

    def _parse_created(self, hit: dict) -> datetime | None:
        """Prefer the epoch ``created_at_i``; fall back to ISO ``created_at``."""
        epoch = hit.get("created_at_i")
        if epoch is not None:
            try:
                return datetime.fromtimestamp(float(epoch), tz=timezone.utc)
            except (TypeError, ValueError):
                pass
        return self._coerce_dt(hit.get("created_at"))

    @staticmethod
    def _coerce_dt(val) -> datetime | None:
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
            return None

    @classmethod
    def _clean_html(cls, text: str) -> str:
        """Strip HTML tags + decode the common entities Algolia returns."""
        if not text:
            return ""
        out = _TAG_RE.sub(" ", text)
        for ent, rep in _ENTITIES.items():
            out = out.replace(ent, rep)
        return _WS_RE.sub(" ", out).strip()

    @staticmethod
    def _as_int(val) -> int:
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _freshest(items: list[RawItem]) -> datetime | None:
        dates = [i.created for i in items if i.created is not None]
        return max(dates) if dates else None

    @staticmethod
    def _dedupe(items: list[RawItem]) -> list[RawItem]:
        seen: set[str] = set()
        out: list[RawItem] = []
        for i in items:
            if i.id in seen:
                continue
            seen.add(i.id)
            out.append(i)
        return out

    def _empty_report(
        self, query_terms: list[str], status: SourceStatus, note: str
    ) -> FetchResult:
        return FetchResult(
            items=[],
            report=SourceReport(
                name=self.name,
                status=status,
                item_count=0,
                note=note,
                query_terms=query_terms,
            ),
        )
