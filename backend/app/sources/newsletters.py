"""Newsletter (RSS) source adapter.

Signal thesis: tech newsletters are a *narrative / attention* proxy. When a
cluster of curators (TLDR, Ben's Bites, The Pragmatic Engineer, Import AI,
Simon Willison, a16z, Lenny's, ...) all start writing about the same shift in a
short window, that shift is (a) real enough to cover and (b) close enough to
market to matter. Recent, on-topic newsletter items therefore read as a "why
now" tailwind and a supply/attention hint for the downstream extractor. Recency
is the item weight so fresher coverage floats to the top.

Two paths, in order of preference:
  * LIVE   — pull each configured feed over HTTP (short timeout, per-feed +
    total wall-clock cap, skip failures), parse with feedparser, keep only
    recent entries (~120 days), strip HTML from summaries, cap the total. Any
    per-feed failure is swallowed; the run keeps going with whatever parsed.
  * MOCK   — last resort (feedparser missing / every feed blocked / nothing
    recent parsed): a rich local fixture so the whole pipeline still runs.

Per the adapter contract in ``base.py``, ``fetch`` must NEVER raise. Every exit
path returns a ``FetchResult`` carrying a ``SourceReport`` that explains the
status (LIVE / MOCK / UNAVAILABLE / EMPTY). Results are cached under
ns='ingest:newsletter' keyed by (area, query_terms) so reruns/reweights cost
zero requests.

RawItem.meta keys emitted (consumed by extract.py):
    {"feed": <feed_url>, "feed_title": <str>, "author": <str|None>,
     "tags": [<str>, ...]}
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from ..cache import get_cache
from ..config import get_settings
from ..schemas import RawItem, SourceName, SourceReport, SourceStatus
from .base import FetchResult, Source

_FIXTURE = Path(__file__).parent / "fixtures" / "newsletters.json"

# Politeness / bounding constants. Newsletters are low-churn, so we fetch each
# configured feed at most once, with a short per-request timeout and a hard cap
# on total wall-clock time across all feeds. Everything is cached afterwards.
_PER_FEED_TIMEOUT = 8.0     # seconds per feed request
_TOTAL_TIME_BUDGET = 20.0   # seconds across ALL feeds in one run
_MAX_FEEDS = 10             # never hit more than this many feeds per run
_RECENT_DAYS = 120         # keep entries newer than this
_REQUEST_DELAY = 0.4       # polite pause between feed requests

# A browser-ish UA; some newsletter hosts (Substack/beehiiv/CDNs) 403 default
# httpx/library UAs but happily serve a normal-looking client.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Cheap HTML tag/entity scrubbing for RSS summaries (which are often HTML).
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&#39;": "'", "&apos;": "'", "&nbsp;": " ", "&hellip;": "...",
    "&mdash;": "-", "&ndash;": "-", "&rsquo;": "'", "&lsquo;": "'",
    "&ldquo;": '"', "&rdquo;": '"',
}


class NewsletterSource(Source):
    """Recent tech-newsletter coverage as an attention / 'why now' tailwind."""

    name = SourceName.NEWSLETTER
    description = (
        "Recent tech-newsletter (RSS) coverage: when curators converge on a "
        "shift, that's an attention tailwind and a 'why now' for a market gap."
    )

    @property
    def live(self) -> bool:
        # Keyless: "live-capable" whenever any feeds are configured. We still
        # fall back to the fixture if feedparser is missing or every feed blocks.
        return get_settings().newsletter_live

    # ------------------------------------------------------------------ #
    # Public entry point                                                 #
    # ------------------------------------------------------------------ #
    def fetch(
        self, area: str, keywords: list[str], sub_segments: list[str]
    ) -> FetchResult:
        settings = get_settings()
        query_terms = self._query_terms(area, keywords)

        # Serve from cache if we have a warm ingest for this scope.
        cache = get_cache()
        cached = cache.get("ingest:newsletter", area, query_terms)
        if cached is not None:
            return self._from_cache(cached, query_terms)

        if self.live:
            result = self._fetch_live(query_terms, settings)
        else:
            # No feeds configured at all -> nothing to hit; serve the fixture.
            result = self._fetch_mock(query_terms, note="no feeds configured; mock")

        # Cache the raw items (serialized) for cheap reruns / reweights.
        try:
            cache.set(
                "ingest:newsletter",
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

    # ------------------------------------------------------------------ #
    # Live path                                                          #
    # ------------------------------------------------------------------ #
    def _fetch_live(self, query_terms: list[str], settings) -> FetchResult:
        """Pull + parse each configured feed under a bounded time budget.

        Order of operations per the contract:
          * cap the feed list (``_MAX_FEEDS``);
          * for each feed: short-timeout GET (skip on any error/non-2xx),
            feedparser-parse, keep recent entries, map to RawItems;
          * stop early once the total wall-clock budget is exhausted;
          * degrade to the fixture if feedparser is missing or nothing parsed.
        Never raises: every failure mode returns a FetchResult.
        """
        try:
            import feedparser  # lazy: a missing dep should degrade, not crash import
        except Exception as exc:  # pragma: no cover - env-dependent
            return self._fetch_mock(
                query_terms,
                note=f"feedparser unavailable ({type(exc).__name__}); mock",
            )

        feeds = list(settings.newsletter_feeds or [])[:_MAX_FEEDS]
        if not feeds:
            return self._fetch_mock(query_terms, note="no feeds configured; mock")

        items: list[RawItem] = []
        started = time.monotonic()
        feeds_ok = 0
        feeds_failed = 0
        last_status: int | None = None
        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            with httpx.Client(
                timeout=_PER_FEED_TIMEOUT, headers=headers, follow_redirects=True
            ) as client:
                for idx, feed_url in enumerate(feeds):
                    # Respect the overall wall-clock budget.
                    if time.monotonic() - started > _TOTAL_TIME_BUDGET:
                        break
                    if idx > 0:
                        time.sleep(_REQUEST_DELAY)  # politeness between hosts

                    text, status = self._get_feed(client, feed_url)
                    if status is not None:
                        last_status = status
                    if not text:
                        feeds_failed += 1
                        continue

                    parsed = self._parse_feed(feedparser, text, feed_url)
                    if parsed:
                        feeds_ok += 1
                        items.extend(parsed)
                    else:
                        feeds_failed += 1
        except Exception as exc:  # never raise out of an adapter
            if not items:
                return self._fetch_mock(
                    query_terms,
                    note=f"live fetch errored ({type(exc).__name__}); mock",
                )

        items = self._dedupe(items)
        # Recency filter + newest-first ordering + cap.
        items = self._recent_only(items, _RECENT_DAYS)
        items.sort(key=lambda i: (i.created or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        items = items[: settings.newsletter_max_items]

        if not items:
            # Distinguish "everything blocked" from "parsed fine but nothing fresh".
            if feeds_ok == 0:
                extra = f" (last HTTP {last_status})" if last_status else ""
                return self._fetch_mock(
                    query_terms, note=f"all {len(feeds)} feeds blocked{extra}; mock"
                )
            return self._fetch_mock(
                query_terms,
                note=f"parsed {feeds_ok} feed(s) but nothing within {_RECENT_DAYS}d; mock",
            )

        report = SourceReport(
            name=self.name,
            status=SourceStatus.LIVE,
            item_count=len(items),
            freshest=self._freshest(items),
            note=f"parsed {feeds_ok}/{len(feeds)} feeds"
            + (f", {feeds_failed} failed" if feeds_failed else ""),
            query_terms=query_terms,
        )
        return FetchResult(items=items, report=report)

    def _get_feed(
        self, client: httpx.Client, feed_url: str
    ) -> tuple[str | None, int | None]:
        """One short-timeout GET for a feed. Returns (text|None, status|None).

        Any network error is swallowed -> (None, None). Non-2xx -> (None, code)
        so the caller can note the last blocking status."""
        try:
            resp = client.get(feed_url)
        except Exception:
            return None, None
        if resp.status_code >= 400:
            return None, resp.status_code
        return (resp.text or ""), resp.status_code

    def _parse_feed(self, feedparser, text: str, feed_url: str) -> list[RawItem]:
        """Parse one feed's XML into RawItems. Swallows any parse error -> []."""
        try:
            feed = feedparser.parse(text)
        except Exception:
            return []

        feed_title = ""
        try:
            feed_title = (getattr(feed, "feed", {}) or {}).get("title", "") or ""
        except Exception:
            feed_title = ""

        out: list[RawItem] = []
        for entry in getattr(feed, "entries", []) or []:
            item = self._entry_to_item(entry, feed_url, feed_title)
            if item is not None:
                out.append(item)
        return out

    def _entry_to_item(
        self, entry: Any, feed_url: str, feed_title: str
    ) -> RawItem | None:
        """Map a single feedparser entry to a RawItem (or None if unusable)."""
        title = _strip_html(getattr(entry, "title", "") or "")
        link = getattr(entry, "link", "") or ""
        if not title and not link:
            return None

        # Summary can live under a few keys; take the richest available.
        raw_summary = (
            getattr(entry, "summary", None)
            or getattr(entry, "description", None)
            or _first_content(entry)
            or ""
        )
        body = _strip_html(raw_summary)

        created = _parse_entry_date(entry)
        entry_id = getattr(entry, "id", None) or link or title

        author = getattr(entry, "author", None)
        if not author:
            # feedparser sometimes nests it under author_detail.name.
            detail = getattr(entry, "author_detail", None)
            if detail is not None:
                author = getattr(detail, "name", None) or (
                    detail.get("name") if isinstance(detail, dict) else None
                )
        author = (author or None) if author != "" else None

        tags = _entry_tags(entry)

        return RawItem(
            source=self.name,
            id=str(entry_id),
            title=title,
            body=body,
            url=link,
            created=created,
            weight=self._weight(created),
            meta={
                "feed": feed_url,
                "feed_title": feed_title,
                "author": author,
                "tags": tags,
            },
        )

    # ------------------------------------------------------------------ #
    # Mock path                                                          #
    # ------------------------------------------------------------------ #
    def _fetch_mock(self, query_terms: list[str], note: str) -> FetchResult:
        """Load fixture items -> RawItems. Robust to a missing/broken fixture."""
        try:
            payload = json.loads(_FIXTURE.read_text())
            entries = payload.get("items", [])
        except Exception as exc:
            return self._empty_report(
                query_terms,
                SourceStatus.MOCK,
                f"mock fixture unavailable: {type(exc).__name__}",
            )

        items: list[RawItem] = []
        for e in entries:
            created = _coerce_dt(e.get("published"))
            body = _strip_html(e.get("summary") or "")
            items.append(
                RawItem(
                    source=self.name,
                    id=str(e.get("id") or e.get("link") or e.get("title", "")),
                    title=_strip_html(e.get("title") or ""),
                    body=body,
                    url=e.get("link") or "",
                    created=created,
                    weight=self._weight(created),
                    meta={
                        "feed": e.get("feed") or "",
                        "feed_title": e.get("feed_title") or "",
                        "author": e.get("author") or None,
                        "tags": list(e.get("tags") or []),
                    },
                )
            )

        items.sort(
            key=lambda i: (i.created or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True,
        )
        report = SourceReport(
            name=self.name,
            status=SourceStatus.MOCK,
            item_count=len(items),
            freshest=self._freshest(items),
            note=note,
            query_terms=query_terms,
        )
        return FetchResult(items=items, report=report)

    # ------------------------------------------------------------------ #
    # Cache rehydration                                                  #
    # ------------------------------------------------------------------ #
    def _from_cache(self, cached: dict, query_terms: list[str]) -> FetchResult:
        try:
            items = [RawItem.model_validate(i) for i in cached.get("items", [])]
            rep_raw = cached.get("report")
            report = SourceReport.model_validate(rep_raw) if rep_raw else None
        except Exception:
            # Corrupt cache entry -> just re-derive a mock so we never raise.
            return self._fetch_mock(query_terms, note="cache miss (corrupt); mock")
        return FetchResult(items=items, report=report)

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    def _query_terms(self, area: str, keywords: list[str]) -> list[str]:
        """Build the ordered, de-duplicated scope terms (for cache key + report).

        Newsletters are fetched wholesale (RSS has no server-side query), so the
        terms don't shape the HTTP call — they only key the cache and annotate
        the report. Downstream extraction does the topical matching.
        """
        terms: list[str] = []
        for t in [area, *keywords]:
            t = (t or "").strip()
            if t and t.lower() not in {x.lower() for x in terms}:
                terms.append(t)
        return terms

    def _weight(self, created: datetime | None) -> float:
        """Pure recency weight in 0..1 (newer = higher).

        Newsletters have no upvote/citation signal, so freshness *is* the
        signal: an item published today scores ~1.0 and decays with a ~90-day
        half-life so month-old coverage still carries real weight while stale
        items fade. Undated entries get a neutral 0.3.
        """
        if created is None:
            return 0.3
        now = datetime.now(timezone.utc)
        age_days = max(0.0, (now - created).total_seconds() / 86400.0)
        half_life_days = 90.0
        return round(0.5 ** (age_days / half_life_days), 4)

    def _recent_only(self, items: list[RawItem], days: int) -> list[RawItem]:
        """Keep undated items + those newer than ``days``. Never raises."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        out: list[RawItem] = []
        for i in items:
            if i.created is None or i.created >= cutoff:
                out.append(i)
        return out

    @staticmethod
    def _freshest(items: list[RawItem]) -> datetime | None:
        dates = [i.created for i in items if i.created is not None]
        return max(dates) if dates else None

    @staticmethod
    def _dedupe(items: list[RawItem]) -> list[RawItem]:
        """De-dupe by id, then by URL (same story syndicated across feeds)."""
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        out: list[RawItem] = []
        for i in items:
            if i.id in seen_ids:
                continue
            if i.url and i.url in seen_urls:
                continue
            seen_ids.add(i.id)
            if i.url:
                seen_urls.add(i.url)
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


# --------------------------------------------------------------------------- #
# Module-level pure helpers                                                    #
# --------------------------------------------------------------------------- #
def _strip_html(raw: str) -> str:
    """Strip tags + decode common entities + collapse whitespace.

    RSS summaries are frequently HTML fragments; downstream extraction wants
    readable plain text. Best-effort and total: never raises.
    """
    if not raw:
        return ""
    try:
        text = _TAG_RE.sub(" ", str(raw))
        for ent, rep in _ENTITIES.items():
            text = text.replace(ent, rep)
        # Numeric entities (&#8217; etc.) -> drop to a space; not worth decoding.
        text = re.sub(r"&#\d+;", " ", text)
        text = _WS_RE.sub(" ", text).strip()
        return text
    except Exception:
        return str(raw).strip()


def _first_content(entry: Any) -> str | None:
    """Pull the first content[].value from a feedparser entry, if present."""
    try:
        content = getattr(entry, "content", None)
        if content:
            first = content[0]
            return getattr(first, "value", None) or (
                first.get("value") if isinstance(first, dict) else None
            )
    except Exception:
        pass
    return None


def _entry_tags(entry: Any) -> list[str]:
    """Collect category/tag terms off a feedparser entry (deduped, capped)."""
    tags: list[str] = []
    for tag in getattr(entry, "tags", []) or []:
        term = getattr(tag, "term", None) or (
            tag.get("term") if isinstance(tag, dict) else None
        )
        if term:
            term = str(term).strip()
            if term and term.lower() not in {t.lower() for t in tags}:
                tags.append(term)
    return tags[:8]


def _parse_entry_date(entry: Any) -> datetime | None:
    """Pull a UTC-aware datetime from a feedparser entry (published/updated)."""
    struct = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
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
        for fmt in (
            "%a, %d %b %Y %H:%M:%S %z",  # RFC 822 (RSS)
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None
