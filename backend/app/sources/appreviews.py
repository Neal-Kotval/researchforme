"""App-store reviews source adapter.

Signal thesis: 1–3★ reviews of a segment's *top* apps are demand-side pain
written by people who already paid (or tried to). Recurring complaint themes
across many reviews mark exactly where the incumbents underserve — the most
actionable white-space signal we ingest.

Two keyless Apple endpoints:

  * **iTunes Search API** (``itunes.apple.com/search?entity=software``) finds
    the segment's apps; we sort by ``userRatingCount`` desc so "top" is earned,
    not alphabetical. ~20 calls/min documented — we make ONE.
  * **Customer-review RSS** (``/us/rss/customerreviews/page=1/id={id}/...``)
    per app, ``sortby=mostrecent``. The feed has NO rating filter, so the
    1–3★ cut is client-side. Gotchas handled: Content-Type is
    ``text/javascript`` (parse the body as JSON anyway); ``feed.entry`` may be
    a list, a single object, or absent; the first entry can be app metadata —
    we filter on the presence of ``im:rating`` instead of position.

Weight = recurrence of complaint terms: a review whose complaint vocabulary
echoes across the batch outranks a one-off gripe.

Contract (``base.py``): ``fetch`` NEVER raises; degrades to fixture (MOCK) on
any failure; caches under ns=``ingest:appreviews``; throttles ~1 req/sec.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

from ..cache import get_cache
from ..config import get_settings
from ..schemas import RawItem, SourceName, SourceReport, SourceStatus
from .base import FetchResult, Source
from .common import coerce_dt, dedupe, freshest, query_terms, strip_html

_FIXTURE = Path(__file__).parent / "fixtures" / "appreviews.json"

_SEARCH_URL = "https://itunes.apple.com/search"
_REVIEWS_URL = (
    "https://itunes.apple.com/us/rss/customerreviews/page={page}/id={track_id}"
    "/sortby=mostrecent/json"
)
_APP_URL = "https://apps.apple.com/us/app/id{track_id}"
_HTTP_TIMEOUT = 15.0
_UA = {"User-Agent": "market-gap-finder/0.1"}

# Complaint vocabulary for the recurrence weighting. Stems, matched lowercase.
_COMPLAINT_TERMS = [
    "crash", "bug", "glitch", "freez", "slow", "lag", "broken", "error",
    "expensive", "subscription", "price", "paywall", "cancel", "refund",
    "scam", "charge", "support", "sync", "lost", "delete", "login", "ads",
    "confusing", "missing", "useless", "unusable", "worse", "downgrade",
]
_WORD_RE = re.compile(r"[a-z']+")


# --------------------------------------------------------------------------- #
# Pure parsers — unit-testable on canned payloads, no network.                 #
# --------------------------------------------------------------------------- #
def parse_apps(payload: dict, max_apps: int) -> list[dict]:
    """iTunes Search payload -> top apps by rating count (genuinely 'top')."""
    apps = [
        a
        for a in (payload or {}).get("results", []) or []
        if isinstance(a, dict) and a.get("trackId")
    ]
    apps.sort(key=lambda a: int(a.get("userRatingCount") or 0), reverse=True)
    return apps[: max(1, max_apps)]


def _label(node) -> str:
    """Unwrap Apple's ``{"label": ...}`` envelope; '' on any shape surprise."""
    if isinstance(node, dict):
        return str(node.get("label") or "")
    return str(node or "")


def parse_review_entries(payload: dict, app_name: str, track_id) -> list[RawItem]:
    """Customer-review RSS JSON -> 1–3★ RawItems for one app.

    Handles all three ``feed.entry`` shapes (absent / single object / list)
    and filters on the presence of ``im:rating`` so a leading app-metadata
    entry is skipped without assuming position.
    """
    entry = ((payload or {}).get("feed") or {}).get("entry")
    if entry is None:
        entries: list = []
    elif isinstance(entry, list):
        entries = entry
    else:
        entries = [entry]

    items: list[RawItem] = []
    for e in entries:
        if not isinstance(e, dict) or "im:rating" not in e:
            continue  # app-metadata entry (or malformed) — not a review
        try:
            rating = int(_label(e.get("im:rating")))
        except (TypeError, ValueError):
            continue
        if rating > 3:
            continue  # the feed can't filter by rating; the 1–3★ cut is ours
        title = _label(e.get("title")).strip()
        text = strip_html(_label(e.get("content")))
        if not title and not text:
            continue
        review_id = _label(e.get("id")) or f"{track_id}:{title[:40]}"
        author = ""
        if isinstance(e.get("author"), dict):
            author = _label((e["author"] or {}).get("name"))
        items.append(
            RawItem(
                source=SourceName.APPREVIEWS,
                id=f"appreview:{review_id}",
                title=f"[{app_name}] {title}" if app_name else title,
                body=text,
                url=_APP_URL.format(track_id=track_id),
                created=coerce_dt(_label(e.get("updated")) or None),
                weight=1.0,
                meta={
                    "app": app_name,
                    "track_id": str(track_id),
                    "rating": rating,
                    "author": author,
                    "version": _label(e.get("im:version")),
                },
            )
        )
    return items


def reweight_by_complaint_recurrence(items: list[RawItem]) -> list[RawItem]:
    """Weight = 1 + how much a review's complaint vocabulary recurs in-batch.

    A term only counts once it appears in ≥2 distinct reviews (recurring theme,
    not a one-off), and each extra echoing review adds a little more. Mutates
    ``weight`` in place and returns the list sorted strongest-first.
    """
    per_item_terms: list[set[str]] = []
    term_counts: dict[str, int] = {}
    for item in items:
        words = set(_WORD_RE.findall(f"{item.title} {item.body}".lower()))
        hits = {t for t in _COMPLAINT_TERMS if any(w.startswith(t) for w in words)}
        per_item_terms.append(hits)
        for t in hits:
            term_counts[t] = term_counts.get(t, 0) + 1

    for item, hits in zip(items, per_item_terms):
        recurring = [t for t in hits if term_counts.get(t, 0) >= 2]
        bonus = sum(min(term_counts[t] - 1, 5) for t in recurring)
        item.weight = round(1.0 + 0.25 * bonus, 2)
    items.sort(key=lambda i: i.weight, reverse=True)
    return items


class AppReviewsSource(Source):
    """Low-star app-review pain miner over the iTunes Search + RSS APIs."""

    name = SourceName.APPREVIEWS
    description = (
        "Pulls 1-3 star iTunes reviews of the segment's top apps (keyless "
        "search + customer-review RSS): paid-user pain, weighted by how often "
        "each complaint theme recurs across the batch."
    )

    @property
    def live(self) -> bool:
        return get_settings().appreviews_live

    # ------------------------------------------------------------------ #
    def fetch(
        self, area: str, keywords: list[str], sub_segments: list[str]
    ) -> FetchResult:
        terms = query_terms(area, keywords)
        cache = get_cache()
        cached = cache.get("ingest:appreviews", area, terms)
        if cached is not None:
            return self._from_cache(cached, terms)

        try:
            result = self._fetch_live(area, terms)
        except Exception as exc:  # belt-and-braces: fetch NEVER raises
            return self._fetch_mock(
                terms,
                note=f"live app-review fetch errored ({type(exc).__name__}); mock",
            )
        try:
            cache.set(
                "ingest:appreviews",
                {
                    "items": [i.model_dump(mode="json") for i in result.items],
                    "report": result.report.model_dump(mode="json")
                    if result.report
                    else None,
                },
                area,
                terms,
            )
        except Exception:
            pass
        return result

    # ------------------------------------------------------------------ #
    def _fetch_live(self, area: str, terms: list[str]) -> FetchResult:
        settings = get_settings()
        items: list[RawItem] = []
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT, headers=_UA) as client:
                apps = self._search_apps(client, terms, settings.appreviews_max_apps)
                for app in apps:
                    time.sleep(settings.appreviews_request_delay)
                    items.extend(
                        self._reviews_for(
                            client,
                            app,
                            pages=max(1, min(10, settings.appreviews_pages_per_app)),
                        )
                    )
        except Exception as exc:  # never raise out of an adapter
            if not items and settings.allow_mock:
                return self._fetch_mock(
                    terms,
                    note=f"live app-review fetch errored ({type(exc).__name__}); mock",
                )

        items = dedupe(items)
        if not items:
            if settings.allow_mock:
                return self._fetch_mock(
                    terms, note="iTunes search/reviews unreachable or empty; served mock"
                )
            return self._empty(
                terms, SourceStatus.EMPTY, "no low-star reviews found for these keywords"
            )

        items = reweight_by_complaint_recurrence(items)
        items = items[: max(1, int(settings.appreviews_max_items))]
        report = SourceReport(
            name=self.name,
            status=SourceStatus.LIVE,
            item_count=len(items),
            freshest=freshest(items),
            note="keyless iTunes search + customer-review RSS (1-3 star, client-side cut)",
            query_terms=terms,
        )
        return FetchResult(items=items, report=report)

    def _search_apps(self, client, terms: list[str], max_apps: int) -> list[dict]:
        params = {
            "term": " ".join(terms[:4]) or "software",
            "entity": "software",
            "limit": max(1, max_apps) * 2,  # headroom for the rating-count sort
            "country": "us",
        }
        try:
            resp = client.get(_SEARCH_URL, params=params)
            if resp.status_code >= 400:
                return []
            # Content-Type is text/javascript — parse the body as JSON anyway.
            return parse_apps(json.loads(resp.text), max_apps)
        except Exception:
            return []

    def _reviews_for(self, client, app: dict, pages: int) -> list[RawItem]:
        track_id = app.get("trackId")
        app_name = str(app.get("trackName") or "")
        out: list[RawItem] = []
        for page in range(1, pages + 1):
            try:
                resp = client.get(_REVIEWS_URL.format(page=page, track_id=track_id))
                if resp.status_code >= 400:
                    break  # occasional 403 bursts — stop this app, keep others
                out.extend(
                    parse_review_entries(json.loads(resp.text), app_name, track_id)
                )
            except Exception:
                break
            if page < pages:
                time.sleep(get_settings().appreviews_request_delay)
        return out

    # -- mock / cache / empty -------------------------------------------- #
    def _fetch_mock(self, terms: list[str], note: str) -> FetchResult:
        try:
            payload = json.loads(_FIXTURE.read_text())
        except Exception as exc:
            return self._empty(
                terms, SourceStatus.MOCK, f"mock fixture unavailable: {type(exc).__name__}"
            )
        items: list[RawItem] = []
        try:
            for app in payload.get("apps", []):
                track_id = app.get("trackId")
                feed = (payload.get("reviews") or {}).get(str(track_id)) or {}
                items.extend(
                    parse_review_entries(feed, str(app.get("trackName") or ""), track_id)
                )
        except Exception:
            pass
        items = reweight_by_complaint_recurrence(dedupe(items))
        report = SourceReport(
            name=self.name,
            status=SourceStatus.MOCK,
            item_count=len(items),
            freshest=freshest(items),
            note=note,
            query_terms=terms,
        )
        return FetchResult(items=items, report=report)

    def _from_cache(self, cached: dict, terms: list[str]) -> FetchResult:
        try:
            items = [RawItem.model_validate(i) for i in cached.get("items", [])]
            rep_raw = cached.get("report")
            report = SourceReport.model_validate(rep_raw) if rep_raw else None
        except Exception:
            return self._fetch_mock(terms, note="cache miss (corrupt); mock")
        return FetchResult(items=items, report=report)

    def _empty(self, terms: list[str], status: SourceStatus, note: str) -> FetchResult:
        return FetchResult(
            items=[],
            report=SourceReport(
                name=self.name, status=status, item_count=0, note=note, query_terms=terms
            ),
        )
