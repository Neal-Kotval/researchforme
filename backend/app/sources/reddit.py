"""Reddit source adapter.

Mines Reddit for *demand* signals: unmet-need phrasing ("I wish there was an
app that...", "is there an app that...", "why does no X exist"), 1-3 star style
complaints, and frustration. Upvotes and recency become the item weight so the
downstream extractor/synthesizer can prioritize load-bearing pain.

Three paths, in order of preference:
  * LIVE (OAuth)      — client-credentials present: query real subreddits via
    https://oauth.reddit.com (60 req/min). Any failure degrades, never raises.
  * LIVE (keyless)    — NO app/keys: blend Reddit's public ``search.json`` with
    DuckDuckGo ``site:reddit.com`` discovery, then pull a couple of thread
    ``.json`` for structured post data (upvotes/comments). Strict rate-limit
    discipline: a hard per-run request budget, a polite delay between calls, and
    an immediate stop on HTTP 429. Results are cached so reruns cost nothing.
  * MOCK              — last resort (public path blocked / empty): a rich fixture
    so the whole pipeline still runs end-to-end.

Per the adapter contract in ``base.py``, ``fetch`` must NEVER raise. Every exit
path returns a ``FetchResult`` carrying a ``SourceReport`` that explains the
status (LIVE / MOCK / UNAVAILABLE / EMPTY).
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..cache import get_cache
from ..config import get_settings
from ..schemas import RawItem, SourceName, SourceReport, SourceStatus
from .base import FetchResult, Source

_FIXTURE = Path(__file__).parent / "fixtures" / "reddit.json"

# Subreddits that tend to surface small-business / builder / consumer pain.
# The area-derived keywords are OR'd into the search query on top of these.
_DEFAULT_SUBREDDITS = [
    "smallbusiness",
    "Entrepreneur",
    "startups",
    "freelance",
    "SaaS",
    "personalfinance",
    "productivity",
    "sysadmin",
    "AskReddit",
]

# Phrases that mark an unmet need. Used both to *seed* the live search query and
# to score how strongly an item reads as demand. Matched case-insensitively.
_NEED_PATTERNS = [
    r"i wish (?:there|it|they)",
    r"is there an app",
    r"is there a tool",
    r"why does no",
    r"why is there no",
    r"why isn'?t there",
    r"does anyone know (?:of )?(?:an?|any) (?:app|tool|software)",
    r"i(?:'d| would) pay",
    r"someone should (?:build|make)",
    r"there has to be (?:a )?(?:better|room)",
    r"so frustrat",
    r"frustrated",
    r"nightmare",
    r"hate (?:that|how|using)",
    r"rant\b",
    r"\b[12] ?star",
    r"\b[123]/5\b",
    r"can'?t believe (?:there|no)",
    r"why does (?:this|it) (?:have to )?(?:be )?so",
]

_NEED_RE = re.compile("|".join(_NEED_PATTERNS), re.IGNORECASE)

_OAUTH_URL = "https://www.reddit.com/api/v1/access_token"
_API_BASE = "https://oauth.reddit.com"

# Keyless path endpoints. Reddit blocks non-browser UAs on the public JSON, so
# the keyless channel presents a realistic browser UA (the OAuth path still uses
# the configured reddit_user_agent). old.reddit.com is a less-locked fallback.
_PUBLIC_SEARCH_HOSTS = [
    "https://www.reddit.com/search.json",
    "https://old.reddit.com/search.json",
]
_DDG_HTML = "https://html.duckduckgo.com/html/"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
# Match real Reddit thread permalinks inside (possibly URL-encoded) DDG HTML.
_PERMALINK_RE = re.compile(
    r"reddit\.com(/r/[A-Za-z0-9_]+/comments/[A-Za-z0-9_]+/[A-Za-z0-9_%\-]*)",
    re.IGNORECASE,
)


class RedditSource(Source):
    """Demand-signal miner over Reddit."""

    name = SourceName.REDDIT
    description = (
        "Mines Reddit posts/comments for unmet-need phrasing, complaints and "
        "frustration; upvotes and recency weight the strongest pain."
    )

    @property
    def live(self) -> bool:
        return get_settings().reddit_live

    # --------------------------------------------------------------------- #
    # Public entry point                                                    #
    # --------------------------------------------------------------------- #
    def fetch(
        self, area: str, keywords: list[str], sub_segments: list[str]
    ) -> FetchResult:
        query_terms = self._query_terms(area, keywords)

        # Serve from cache if we have a warm ingest for this scope.
        cache = get_cache()
        cached = cache.get("ingest:reddit", area, query_terms)
        if cached is not None:
            return self._from_cache(cached, query_terms)

        if self.live:
            result = self._fetch_live(area, query_terms)
        else:
            # No OAuth app: do real research keyless (public JSON + web search),
            # falling back to the fixture only if the public path is blocked/empty.
            result = self._fetch_keyless(area, query_terms)

        # Cache the raw items (serialized) for cheap reruns / reweights.
        try:
            cache.set(
                "ingest:reddit",
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
    # Live path                                                             #
    # --------------------------------------------------------------------- #
    def _fetch_live(self, area: str, query_terms: list[str]) -> FetchResult:
        """Real OAuth + search. Any failure -> UNAVAILABLE (creds but failed)."""
        settings = get_settings()
        try:
            token = self._oauth_token(settings)
            if not token:
                # Creds present but auth failed -> degrade (optionally to mock).
                if settings.allow_mock:
                    return self._fetch_mock(
                        query_terms, note="OAuth failed; serving mock"
                    )
                return self._empty_report(
                    query_terms,
                    SourceStatus.UNAVAILABLE,
                    "Reddit OAuth token request failed",
                )

            headers = {
                "Authorization": f"bearer {token}",
                "User-Agent": settings.reddit_user_agent,
            }
            query = " OR ".join(f'"{t}"' if " " in t else t for t in query_terms[:6])
            subreddits = _DEFAULT_SUBREDDITS[: settings.reddit_subreddit_limit]

            items: list[RawItem] = []
            per_sub = max(5, settings.reddit_post_limit // max(1, len(subreddits)))
            with httpx.Client(timeout=15.0, headers=headers) as client:
                for sub in subreddits:
                    items.extend(
                        self._search_subreddit(client, sub, query, per_sub)
                    )

            items = self._dedupe(items)
            if not items:
                return self._empty_report(
                    query_terms,
                    SourceStatus.EMPTY,
                    "Reddit search returned no matching pain/demand posts",
                )

            items.sort(key=lambda i: i.weight, reverse=True)
            items = items[: settings.reddit_post_limit]
            report = SourceReport(
                name=self.name,
                status=SourceStatus.LIVE,
                item_count=len(items),
                freshest=self._freshest(items),
                note=f"searched {len(subreddits)} subreddits via OAuth",
                query_terms=query_terms,
            )
            return FetchResult(items=items, report=report)

        except Exception as exc:  # never raise out of an adapter
            if settings.allow_mock:
                return self._fetch_mock(
                    query_terms, note=f"live fetch errored ({type(exc).__name__}); mock"
                )
            return self._empty_report(
                query_terms,
                SourceStatus.UNAVAILABLE,
                f"Reddit live fetch error: {type(exc).__name__}: {exc}",
            )

    def _oauth_token(self, settings) -> str | None:
        """Client-credentials grant. Returns a bearer token or None on failure."""
        try:
            resp = httpx.post(
                _OAUTH_URL,
                data={"grant_type": "client_credentials"},
                auth=(settings.reddit_client_id, settings.reddit_client_secret),
                headers={"User-Agent": settings.reddit_user_agent},
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json().get("access_token")
        except Exception:
            return None

    def _search_subreddit(
        self, client: httpx.Client, sub: str, query: str, limit: int
    ) -> list[RawItem]:
        """Search one subreddit; return matched RawItems. Swallows errors."""
        try:
            resp = client.get(
                f"{_API_BASE}/r/{sub}/search",
                params={
                    "q": query,
                    "restrict_sr": "true",
                    "sort": "relevance",
                    "t": "year",
                    "limit": limit,
                },
            )
            resp.raise_for_status()
            children = resp.json().get("data", {}).get("children", [])
        except Exception:
            return []

        out: list[RawItem] = []
        for child in children:
            data = child.get("data", {})
            item = self._to_raw_item(data)
            if item is not None:
                out.append(item)
        return out

    def _to_raw_item(self, data: dict, require_need: bool = True) -> RawItem | None:
        """Map a live Reddit post payload to a RawItem.

        ``require_need`` keeps only posts that read as an unmet need / complaint /
        frustration (used for broad searches). Threads surfaced by a pain-biased
        web-search query pass ``require_need=False`` so we don't drop good hits.
        """
        title = data.get("title") or ""
        body = data.get("selftext") or ""
        blob = f"{title}\n{body}"
        if require_need and not _NEED_RE.search(blob):
            return None

        permalink = data.get("permalink") or ""
        url = f"https://reddit.com{permalink}" if permalink.startswith("/") else (
            data.get("url") or ""
        )
        created = self._epoch_to_dt(data.get("created_utc"))
        # Freshness floor (mirrors HN's 18-month cutoff): stale complaints are
        # dropped outright, not just down-weighted — an old upvoted rant must
        # never anchor a "why now". Dateless posts are kept (weight handles them).
        if created is not None and created < self._freshness_floor():
            return None
        ups = float(data.get("ups") or data.get("score") or 0)

        return RawItem(
            source=self.name,
            id=str(data.get("id") or data.get("name") or url),
            title=title.strip(),
            body=body.strip(),
            url=url,
            created=created,
            weight=self._weight(ups, created),
            meta={
                "subreddit": data.get("subreddit") or "",
                "num_comments": int(data.get("num_comments") or 0),
                "flair": data.get("link_flair_text") or "",
                "ups": int(ups),
                "need_match": bool(_NEED_RE.search(blob)),
            },
        )

    # --------------------------------------------------------------------- #
    # Keyless path (no OAuth app): public JSON + web-search discovery         #
    # --------------------------------------------------------------------- #
    def _fetch_keyless(self, area: str, query_terms: list[str]) -> FetchResult:
        """Real Reddit data without any app/keys, under a strict request budget.

        Blends two low-volume channels so we don't lean on a single endpoint:
          A) Reddit's public ``search.json`` (one site-wide query).
          B) DuckDuckGo ``site:reddit.com`` discovery -> a couple of thread
             ``.json`` fetches for structured post data.
        A hard per-run request budget + inter-request delay + immediate stop on
        429 keep us well under Reddit's unauthenticated rate limit; everything is
        cached, so reruns/reweights cost zero requests.
        """
        settings = get_settings()
        budget = {
            "remaining": settings.reddit_max_requests,
            "blocked": False,
            "first": True,
        }
        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": "application/json,text/html;q=0.9",
            "Accept-Language": "en-US,en;q=0.9",
        }
        items: list[RawItem] = []
        try:
            with httpx.Client(timeout=15.0, headers=headers, follow_redirects=True) as client:
                # Channel A — Reddit public search (one site-wide query).
                query = " OR ".join(
                    f'"{t}"' if " " in t else t for t in query_terms[:4]
                )
                items.extend(self._reddit_public_search(client, query, budget))

                # Channel B — web-search discovery -> a few thread .json fetches.
                if settings.reddit_use_web_search and not budget["blocked"]:
                    permalinks = self._web_search_permalinks(client, query_terms, budget)
                    for pl in permalinks[: settings.reddit_web_results]:
                        if budget["blocked"] or budget["remaining"] <= 0:
                            break
                        item = self._fetch_thread(client, pl, budget)
                        if item is not None:
                            items.append(item)
        except Exception as exc:  # never raise out of an adapter
            if not items and settings.allow_mock:
                return self._fetch_mock(
                    query_terms,
                    note=f"keyless fetch errored ({type(exc).__name__}); mock",
                )

        items = self._dedupe(items)
        if not items:
            if budget["blocked"]:
                note = "public Reddit rate-limited (429); served mock"
            elif budget.get("last_status"):
                note = f"public Reddit blocked (HTTP {budget['last_status']}); served mock"
            else:
                note = "keyless search found no matching pain; served mock"
            return self._fetch_mock(query_terms, note=note)

        items.sort(key=lambda i: i.weight, reverse=True)
        items = items[: settings.reddit_post_limit]
        channels = "public JSON + web search" if settings.reddit_use_web_search else "public JSON"
        report = SourceReport(
            name=self.name,
            status=SourceStatus.LIVE,
            item_count=len(items),
            freshest=self._freshest(items),
            note=f"keyless ({channels}); no app/keys",
            query_terms=query_terms,
        )
        return FetchResult(items=items, report=report)

    def _budgeted_get(
        self, client: httpx.Client, url: str, budget: dict, params: dict | None = None
    ) -> httpx.Response | None:
        """One GET against the per-run budget: enforces the delay + 429 stop.

        Returns the response on 2xx, else None. Sets ``budget['blocked']`` on a
        429 so the rest of the run bails out immediately (respect the limit)."""
        if budget["blocked"] or budget["remaining"] <= 0:
            return None
        if not budget["first"]:
            time.sleep(get_settings().reddit_request_delay)
        budget["first"] = False
        budget["remaining"] -= 1
        try:
            resp = client.get(url, params=params)
        except Exception:
            return None
        if resp.status_code == 429:
            budget["blocked"] = True
            budget["last_status"] = 429
            return None
        if resp.status_code >= 400:
            budget["last_status"] = resp.status_code
            return None
        return resp

    def _reddit_public_search(
        self, client: httpx.Client, query: str, budget: dict, limit: int = 25
    ) -> list[RawItem]:
        """Reddit's keyless ``search.json`` (site-wide), filtered to demand posts.

        Tries www then old.reddit.com (less locked down) within the budget."""
        params = {
            "q": query,
            "sort": "relevance",
            "t": "year",
            "limit": limit,
            "type": "link",
            "raw_json": 1,
        }
        children: list = []
        for host in _PUBLIC_SEARCH_HOSTS:
            if budget["blocked"] or budget["remaining"] <= 0:
                break
            resp = self._budgeted_get(client, host, budget, params=params)
            if resp is None:
                continue
            try:
                children = resp.json().get("data", {}).get("children", [])
            except Exception:
                children = []
            if children:
                break  # got results; don't spend budget on the fallback host
        out: list[RawItem] = []
        for child in children:
            item = self._to_raw_item(child.get("data", {}))
            if item is not None:
                out.append(item)
        return out

    def _web_search_permalinks(
        self, client: httpx.Client, query_terms: list[str], budget: dict
    ) -> list[str]:
        """One DuckDuckGo ``site:reddit.com`` query -> distinct thread permalinks.

        Keyless HTML endpoint; we just regex real reddit thread links out of the
        (URL-decoded) results page. Best-effort: any failure returns []."""
        terms = " ".join(query_terms[:3])
        q = f'site:reddit.com {terms} ("I wish" OR "is there an app" OR frustrating)'
        resp = self._budgeted_get(client, _DDG_HTML, budget, params={"q": q})
        if resp is None:
            return []
        html = resp.text or ""
        try:
            unquoted = urllib.parse.unquote(html)
        except Exception:
            unquoted = html
        seen: set[str] = set()
        links: list[str] = []
        for m in _PERMALINK_RE.finditer(unquoted):
            path = m.group(1)
            # Trim any trailing markup / query junk the regex may have caught.
            for cut in ('"', "<", "&", " "):
                path = path.split(cut)[0]
            if path and path not in seen:
                seen.add(path)
                links.append(f"https://www.reddit.com{path}")
        return links

    def _fetch_thread(
        self, client: httpx.Client, permalink: str, budget: dict
    ) -> RawItem | None:
        """Pull one thread's ``.json`` for structured post data (ups/comments)."""
        url = permalink.rstrip("/") + "/.json"
        resp = self._budgeted_get(client, url, budget, params={"raw_json": 1, "limit": 1})
        if resp is None:
            return None
        try:
            data = resp.json()
            post = data[0]["data"]["children"][0]["data"]
        except Exception:
            return None
        # Discovery was already pain-biased, so don't re-require the need regex.
        return self._to_raw_item(post, require_need=False)

    # --------------------------------------------------------------------- #
    # Mock path                                                             #
    # --------------------------------------------------------------------- #
    def _fetch_mock(self, query_terms: list[str], note: str) -> FetchResult:
        """Load fixture posts -> RawItems. Robust to a missing/broken fixture."""
        try:
            payload = json.loads(_FIXTURE.read_text())
            posts = payload.get("posts", [])
        except Exception as exc:
            return self._empty_report(
                query_terms,
                SourceStatus.MOCK,
                f"mock fixture unavailable: {type(exc).__name__}",
            )

        items: list[RawItem] = []
        for p in posts:
            created = self._epoch_to_dt(p.get("created_utc"))
            ups = float(p.get("ups") or 0)
            blob = f"{p.get('title', '')}\n{p.get('selftext', '')}"
            items.append(
                RawItem(
                    source=self.name,
                    id=str(p.get("id")),
                    title=(p.get("title") or "").strip(),
                    body=(p.get("selftext") or "").strip(),
                    url=p.get("permalink") or "",
                    created=created,
                    weight=self._weight(ups, created),
                    meta={
                        "subreddit": p.get("subreddit") or "",
                        "num_comments": int(p.get("num_comments") or 0),
                        "flair": p.get("link_flair_text") or "",
                        "ups": int(ups),
                        "need_match": bool(_NEED_RE.search(blob)),
                    },
                )
            )

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
            # Corrupt cache entry -> just re-derive a mock so we never raise.
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
    def _freshness_floor() -> datetime:
        """Oldest acceptable post date for the live/keyless paths (config-driven).

        The mock fixture path bypasses this on purpose so old fixture dates keep
        the offline pipeline working end-to-end.
        """
        months = get_settings().reddit_months_back
        return datetime.now(timezone.utc) - timedelta(days=months * 30)

    def _weight(self, ups: float, created: datetime | None) -> float:
        """Upvotes as the base signal, gently boosted for recency.

        Weight ~= ups * recency_factor, where posts in the last ~90 days keep
        near full weight and older posts decay toward 0.6x. Keeps the raw
        upvote magnitude readable while nudging fresher pain to the top.
        """
        if not created:
            return max(0.0, ups)
        now = datetime.now(timezone.utc)
        days = max(0.0, (now - created).total_seconds() / 86400.0)
        # 1.0 at day 0, ~0.6 by a year old, floor 0.5.
        recency = max(0.5, 1.0 - (days / 365.0) * 0.4)
        return round(max(0.0, ups) * recency, 2)

    @staticmethod
    def _epoch_to_dt(epoch) -> datetime | None:
        try:
            return datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        except (TypeError, ValueError):
            return None

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
