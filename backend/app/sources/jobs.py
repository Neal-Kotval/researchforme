"""Jobs source adapter.

Signal thesis: *hiring is demand with a wallet attached*. When distinct
companies keep paying salaries for the same role/stack around a segment, the
pain is real enough to fund headcount — a far stronger willingness-to-pay
signal than complaints. The recurring-role pattern across companies is the
signal the extractor and lenses should look for.

Three keyless feeds, each best-effort (a failing feed never sinks the others):

  * **RemoteOK JSON** (``/api?tag=...``) — rich structured postings. Gotcha:
    element [0] is a legal notice, not a job; we filter for objects that carry
    ``position``. Requires a real User-Agent.
  * **WeWorkRemotely RSS** (``/remote-jobs.rss``) — category feeds only, so we
    keyword-filter client-side. Cloudflare-fronted: browser-ish UA + cache.
  * **HN "Who is hiring"** — Algolia: find the latest thread by the
    ``whoishiring`` author (title-filtered, because the same account also posts
    "Who wants to be hired?"), then search its comments for the keywords.

Contract (``base.py``): ``fetch`` NEVER raises; degrades to the local fixture
(MOCK) on total failure; caches under ns=``ingest:jobs``. A strict per-run
request budget + polite delay keep all three feeds happy.
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

_FIXTURE = Path(__file__).parent / "fixtures" / "jobs.json"

_REMOTEOK_API = "https://remoteok.com/api"
_WWR_FEED = "https://weworkremotely.com/remote-jobs.rss"
_ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"
_ALGOLIA_BY_DATE = "https://hn.algolia.com/api/v1/search_by_date"
_HN_ITEM = "https://news.ycombinator.com/item?id={id}"
_HTTP_TIMEOUT = 15.0
_UA = {
    "User-Agent": "Mozilla/5.0 (compatible; market-gap-finder/0.1; +market-gap-finder)"
}
_WHO_IS_HIRING = re.compile(r"who is hiring", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Pure parsers — unit-testable on canned payloads, no network.                 #
# --------------------------------------------------------------------------- #
def parse_remoteok(payload: list) -> list[RawItem]:
    """RemoteOK ``/api`` JSON -> RawItems. Skips the leading legal notice."""
    items: list[RawItem] = []
    for row in payload or []:
        if not isinstance(row, dict) or not row.get("position"):
            continue  # element [0] is a {legal, last_updated} notice
        position = str(row.get("position") or "").strip()
        company = str(row.get("company") or "").strip()
        url = str(row.get("url") or "").strip()
        if not position or not url:
            continue
        items.append(
            RawItem(
                source=SourceName.JOBS,
                id=f"remoteok:{row.get('id') or row.get('slug') or url}",
                title=f"{position} @ {company}" if company else position,
                body=strip_html(row.get("description")),
                url=url,
                created=coerce_dt(row.get("date")),
                weight=1.0,
                meta={
                    "feed": "remoteok",
                    "company": company,
                    "tags": list(row.get("tags") or []),
                    "location": row.get("location") or "",
                },
            )
        )
    return items


def parse_wwr(entries: list, terms: list[str]) -> list[RawItem]:
    """WeWorkRemotely feedparser entries -> keyword-filtered RawItems.

    The feed is category-wide (no keyword search), so relevance is our job:
    keep entries whose title/description mention any query term.
    """
    def _get(e, key, alt=""):
        if isinstance(e, dict):
            return e.get(key) or (e.get(alt) if alt else "") or ""
        return getattr(e, key, "") or (getattr(e, alt, "") if alt else "") or ""

    lowered = [t.lower() for t in terms if t]
    items: list[RawItem] = []
    for e in entries or []:
        title = str(_get(e, "title")).strip()
        desc = _get(e, "description", "summary")
        link = _get(e, "link")
        pub = _get(e, "published", "pubDate")
        body = strip_html(desc)
        blob = f"{title} {body}".lower()
        if lowered and not any(t in blob for t in lowered):
            continue
        if not title or not link:
            continue
        items.append(
            RawItem(
                source=SourceName.JOBS,
                id=f"wwr:{link}",
                title=title,  # WWR titles read "Company: Role"
                body=body,
                url=str(link),
                created=_parse_rfc822(pub),
                weight=1.0,
                meta={"feed": "weworkremotely"},
            )
        )
    return items


def parse_hiring_comments(hits: list) -> list[RawItem]:
    """Algolia comment hits from a 'Who is hiring' thread -> RawItems.

    Only top-level comments are job posts; replies are filtered out by
    ``parent_id == story_id``. Title = the first line (company | role).
    """
    items: list[RawItem] = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        object_id = hit.get("objectID")
        text = strip_html(hit.get("comment_text") or "")
        if not object_id or not text:
            continue
        if hit.get("parent_id") and hit.get("story_id") and hit["parent_id"] != hit["story_id"]:
            continue  # a reply, not a job post
        first_line = text.split(". ")[0][:120].strip() or text[:120]
        items.append(
            RawItem(
                source=SourceName.JOBS,
                id=f"hn:{object_id}",
                title=first_line,
                body=text,
                url=_HN_ITEM.format(id=object_id),
                created=coerce_dt(hit.get("created_at")),
                weight=1.0,
                meta={"feed": "hn_who_is_hiring", "author": hit.get("author") or ""},
            )
        )
    return items


def pick_hiring_thread(hits: list) -> str | None:
    """From `author_whoishiring` stories, pick the actual hiring thread id.

    The same account posts "Who wants to be hired?" and "Freelancer?" threads —
    filter strictly on the title.
    """
    for hit in hits or []:
        if isinstance(hit, dict) and _WHO_IS_HIRING.search(str(hit.get("title") or "")):
            return str(hit.get("objectID") or "") or None
    return None


def _parse_rfc822(val) -> object:
    """RFC822 pubDate -> aware datetime (email.utils; None on failure)."""
    if not val:
        return None
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(str(val))
    except Exception:
        return coerce_dt(val)


class JobsSource(Source):
    """Hiring-as-demand radar: RemoteOK + WeWorkRemotely + HN who-is-hiring."""

    name = SourceName.JOBS
    description = (
        "Mines job postings (RemoteOK, WeWorkRemotely, HN 'Who is hiring') as "
        "wallet-backed demand: a recurring role across distinct companies means "
        "the pain is worth salaries, not just complaints."
    )

    @property
    def live(self) -> bool:
        return get_settings().jobs_live

    # ------------------------------------------------------------------ #
    def fetch(
        self, area: str, keywords: list[str], sub_segments: list[str]
    ) -> FetchResult:
        terms = query_terms(area, keywords)
        cache = get_cache()
        cached = cache.get("ingest:jobs", area, terms)
        if cached is not None:
            return self._from_cache(cached, terms)

        try:
            result = self._fetch_live(area, terms)
        except Exception as exc:  # belt-and-braces: fetch NEVER raises
            return self._fetch_mock(
                terms, note=f"live jobs fetch errored ({type(exc).__name__}); mock"
            )
        try:
            cache.set(
                "ingest:jobs",
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
        """Best-effort across the three feeds under one request budget."""
        settings = get_settings()
        budget = {"remaining": max(1, int(settings.jobs_max_requests)), "first": True}
        items: list[RawItem] = []
        feeds_ok: list[str] = []
        try:
            with httpx.Client(
                timeout=_HTTP_TIMEOUT, headers=_UA, follow_redirects=True
            ) as client:
                got = self._remoteok(client, terms, budget)
                if got:
                    feeds_ok.append("remoteok")
                items.extend(got)

                got = self._wwr(client, terms, budget)
                if got:
                    feeds_ok.append("weworkremotely")
                items.extend(got)

                got = self._hn_hiring(client, terms, budget)
                if got:
                    feeds_ok.append("hn_who_is_hiring")
                items.extend(got)
        except Exception as exc:  # belt-and-braces: never raise out of fetch
            if not items and settings.allow_mock:
                return self._fetch_mock(
                    terms, note=f"live jobs fetch errored ({type(exc).__name__}); mock"
                )

        items = dedupe(items)
        if not items:
            if settings.allow_mock:
                return self._fetch_mock(
                    terms, note="all job feeds empty/unreachable; served mock"
                )
            return self._empty(terms, SourceStatus.EMPTY, "no job feeds returned items")

        items.sort(key=lambda i: (i.created is not None, i.created), reverse=True)
        items = items[: max(1, int(settings.jobs_max_items))]
        report = SourceReport(
            name=self.name,
            status=SourceStatus.LIVE,
            item_count=len(items),
            freshest=freshest(items),
            note=f"keyless job feeds: {', '.join(feeds_ok)}",
            query_terms=terms,
        )
        return FetchResult(items=items, report=report)

    # -- individual feeds (each swallows its own errors) ----------------- #
    def _get_json(self, client: httpx.Client, url: str, budget: dict, params=None):
        if budget["remaining"] <= 0:
            return None
        if not budget["first"]:
            time.sleep(get_settings().jobs_request_delay)
        budget["first"] = False
        budget["remaining"] -= 1
        try:
            resp = client.get(url, params=params)
            if resp.status_code >= 400:
                return None
            return json.loads(resp.text)  # RemoteOK/Algolia are JSON bodies
        except Exception:
            return None

    def _remoteok(self, client, terms: list[str], budget: dict) -> list[RawItem]:
        tag = re.sub(r"[^a-z0-9]+", "-", (terms[0] if terms else "").lower()).strip("-")
        payload = self._get_json(
            client, _REMOTEOK_API, budget, params={"tag": tag} if tag else None
        )
        if not isinstance(payload, list):
            return []
        try:
            return parse_remoteok(payload)
        except Exception:
            return []

    def _wwr(self, client, terms: list[str], budget: dict) -> list[RawItem]:
        if budget["remaining"] <= 0:
            return []
        if not budget["first"]:
            time.sleep(get_settings().jobs_request_delay)
        budget["first"] = False
        budget["remaining"] -= 1
        try:
            import feedparser  # lazy: a missing dep degrades, never crashes import

            resp = client.get(_WWR_FEED)
            if resp.status_code >= 400:
                return []
            feed = feedparser.parse(resp.text)
            return parse_wwr(list(feed.entries or []), terms)
        except Exception:
            return []

    def _hn_hiring(self, client, terms: list[str], budget: dict) -> list[RawItem]:
        found = self._get_json(
            client,
            _ALGOLIA_BY_DATE,
            budget,
            params={"tags": "story,author_whoishiring", "hitsPerPage": 6},
        )
        thread_id = pick_hiring_thread((found or {}).get("hits", []))
        if not thread_id:
            return []
        comments = self._get_json(
            client,
            _ALGOLIA_SEARCH,
            budget,
            params={
                "tags": f"comment,story_{thread_id}",
                "query": " ".join(terms[:4]),
                "hitsPerPage": 30,
            },
        )
        try:
            return parse_hiring_comments((comments or {}).get("hits", []))
        except Exception:
            return []

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
            items.extend(parse_remoteok(payload.get("remoteok", [])))
            items.extend(parse_wwr(payload.get("weworkremotely", []), terms=[]))
            items.extend(parse_hiring_comments(payload.get("hn_hiring", [])))
        except Exception:
            pass
        items = dedupe(items)
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
