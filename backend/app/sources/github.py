"""GitHub source adapter.

Signal thesis: GitHub is a *capability / supply* tailwind proxy. A burst of
recently-created repositories around a keyword — especially ones accreting
stars fast — is hard evidence that builders are pouring energy into a space and
that the tooling substrate for a product just got cheaper. We therefore query
the last ~18 months of matching repositories, sorted by stars, and turn
*star velocity* (stars per day of age) into the per-item weight so a young repo
that already has thousands of stars outranks an old, slowly-accreting one.

Three paths, mirroring the reddit.py reference adapter:
  * LIVE (keyless)  — the default. Hit the public GitHub Search API
    (https://api.github.com/search/repositories), which needs no auth. The
    unauthenticated Search limit is brutally low (10 req/min), so we spend a
    *tiny* request budget (1–2 calls), stop hard on 403/429, and cache the
    result so reruns/reweights cost zero requests.
  * LIVE (token)    — if ``settings.github_token`` is set, we send a
    ``Authorization: Bearer <token>`` header which raises the Search limit to
    30 req/min. Same code path, just a fatter budget headroom.
  * MOCK            — last resort (rate-limited / blocked / empty): a rich local
    fixture so the whole pipeline still runs end-to-end.

Per the adapter contract in ``base.py``, ``fetch`` must NEVER raise. Every exit
path returns a ``FetchResult`` carrying a ``SourceReport`` that explains the
status (LIVE / MOCK / UNAVAILABLE / EMPTY).

Emitted ``RawItem.meta`` (consumed by extract.py):
  ``repo`` (full_name), ``stars`` (int), ``language`` (str|None), ``topics``
  (list[str]), ``pushed`` (pushed_at ISO), ``star_velocity`` (float), ``forks``
  (int).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from ..cache import get_cache
from ..config import get_settings
from ..schemas import RawItem, SourceName, SourceReport, SourceStatus
from .base import FetchResult, Source

_SEARCH_URL = "https://api.github.com/search/repositories"
_FIXTURE = Path(__file__).parent / "fixtures" / "github.json"
_HTTP_TIMEOUT = 15.0

# Politeness: seconds to wait between successive Search calls. The keyless
# Search limit is 10 req/min, so a small delay plus a 1–2 call budget keeps us
# comfortably clear of the ceiling even on back-to-back runs.
_REQUEST_DELAY = 2.0

# Recency window for "recently created" repos, in days (~18 months).
_WINDOW_DAYS = 548

# GitHub's Accept header opts us into topics + the stable v3 media type.
_ACCEPT = "application/vnd.github+json"
_UA = "market-gap-finder/0.1"


class GitHubSource(Source):
    """Recent-repo star-velocity from GitHub as a capability/supply tailwind."""

    name = SourceName.GITHUB
    description = (
        "GitHub recently-created repos ranked by star velocity: a burst of fast "
        "growing projects on a keyword signals the tooling substrate just got "
        "cheaper (a 'why now') and hints at who is already building."
    )

    @property
    def live(self) -> bool:
        # The Search API is keyless-capable, so we're always "live-capable"; a
        # token (if present) only raises the rate limit. We still degrade to the
        # fixture if the network blocks / rate-limits us at call time.
        return get_settings().github_live

    # ------------------------------------------------------------------ #
    # Public entry point                                                 #
    # ------------------------------------------------------------------ #
    def fetch(self, area: str, keywords: list[str], sub_segments: list[str]) -> FetchResult:
        query_terms = self._query_terms(area, keywords)

        # Serve from cache if we have a warm ingest for this scope. Because the
        # keyless Search limit is so low, caching hard is the whole game.
        cache = get_cache()
        cached = cache.get("ingest:github", area, query_terms)
        if cached is not None:
            return self._from_cache(cached, query_terms)

        result = self._fetch_live(query_terms)

        # Cache the serialized items/report for cheap reruns / reweights. Only
        # genuinely-live results are worth pinning; mock is cheap to re-derive,
        # but we still cache it briefly so a rate-limited run doesn't hammer the
        # API on every reweight. (TTL is governed globally by the cache.)
        try:
            cache.set(
                "ingest:github",
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
    # Live path (keyless Search API; optional token raises the limit)     #
    # ------------------------------------------------------------------ #
    def _fetch_live(self, query_terms: list[str]) -> FetchResult:
        """Query the GitHub Search API under a tiny request budget.

        Never raises: on a network error, 403/429 rate-limit, or empty result
        we degrade to the fixture (MOCK) — or, if mock is disabled, report an
        accurate UNAVAILABLE/EMPTY status.
        """
        settings = get_settings()
        token = settings.github_token
        headers = {
            "Accept": _ACCEPT,
            "User-Agent": _UA,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # Hard per-run request budget. Keyless is 10/min; even with a token we
        # only ever need one search, with one polite retry against a relaxed
        # query if the first is empty.
        budget = {"remaining": 2, "blocked": False, "first": True, "last_status": None}

        q_primary = self._build_query(query_terms, phrase=True)
        rows: list[dict] = []
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT, headers=headers) as client:
                rows = self._search(client, q_primary, budget)
                # Nothing matched and we still have budget + weren't blocked:
                # retry once with a looser (unquoted) query before giving up.
                if not rows and not budget["blocked"] and budget["remaining"] > 0:
                    q_loose = self._build_query(query_terms, phrase=False)
                    if q_loose != q_primary:
                        rows = self._search(client, q_loose, budget)
        except Exception as exc:  # never raise out of an adapter
            return self._degrade(
                query_terms,
                note=f"live GitHub fetch errored ({type(exc).__name__}); {exc}",
                blocked=False,
            )

        if budget["blocked"]:
            return self._degrade(
                query_terms,
                note=f"GitHub Search rate-limited (HTTP {budget['last_status']})",
                blocked=True,
            )

        items = self._rows_to_items(rows)
        if not items:
            # Clean query, genuinely nothing recent. Prefer an honest EMPTY, but
            # keep the pipeline populated with mock if a status code hinted a
            # soft block we couldn't classify as a hard 403/429.
            if settings.allow_mock and budget["last_status"] in (403, 429):
                return self._fetch_mock(
                    query_terms,
                    note=f"GitHub soft-blocked (HTTP {budget['last_status']}); served mock",
                )
            return self._empty_report(
                query_terms,
                SourceStatus.EMPTY,
                "GitHub returned no recently-created repos for these keywords.",
            )

        items.sort(key=lambda i: i.weight, reverse=True)
        items = items[: settings.github_max_repos]
        auth = "token" if token else "keyless"
        report = SourceReport(
            name=self.name,
            status=SourceStatus.LIVE,
            item_count=len(items),
            freshest=self._freshest(items),
            note=f"GitHub Search ({auth}); recent repos by star velocity",
            query_terms=query_terms,
        )
        return FetchResult(items=items, report=report)

    def _search(self, client: httpx.Client, q: str, budget: dict) -> list[dict]:
        """One budgeted Search call. Returns repo dicts, or [] on any failure.

        Enforces the inter-request delay and stops the whole run on a 403/429
        (GitHub signals its rate limit with either) by setting ``budget['blocked']``.
        """
        if budget["blocked"] or budget["remaining"] <= 0:
            return []
        if not budget["first"]:
            time.sleep(_REQUEST_DELAY)
        budget["first"] = False
        budget["remaining"] -= 1

        params = {
            "q": q,
            "sort": "stars",
            "order": "desc",
            "per_page": max(1, min(100, get_settings().github_max_repos)),
        }
        try:
            resp = client.get(_SEARCH_URL, params=params)
        except Exception:
            return []

        budget["last_status"] = resp.status_code
        # GitHub returns 403 (primary/secondary rate limit) or 429 for abuse.
        if resp.status_code in (403, 429):
            # If it's an auth-scope 403 rather than a rate limit we'd still want
            # to stop hitting it this run, so treat both as blocked.
            budget["blocked"] = True
            return []
        if resp.status_code >= 400:
            return []
        try:
            return resp.json().get("items", []) or []
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Row -> RawItem mapping                                             #
    # ------------------------------------------------------------------ #
    def _rows_to_items(self, rows: list[dict]) -> list[RawItem]:
        """Map GitHub repo payloads (live or fixture-shaped) to RawItems."""
        now = datetime.now(timezone.utc)
        out: list[RawItem] = []
        for repo in rows:
            full_name = repo.get("full_name") or repo.get("repo") or ""
            if not full_name:
                continue
            created = _coerce_dt(repo.get("created_at"))
            stars = int(repo.get("stargazers_count") or repo.get("stars") or 0)
            forks = int(repo.get("forks_count") or repo.get("forks") or 0)
            language = repo.get("language")
            topics = repo.get("topics") or []
            if not isinstance(topics, list):
                topics = []
            pushed = repo.get("pushed_at") or repo.get("pushed")
            velocity = _star_velocity(stars, created, now)
            url = repo.get("html_url") or f"https://github.com/{full_name}"

            out.append(
                RawItem(
                    source=self.name,
                    id=str(full_name),
                    title=str(full_name),
                    body=str(repo.get("description") or "").strip(),
                    url=str(url),
                    created=created,
                    weight=velocity,
                    meta={
                        "repo": str(full_name),
                        "stars": stars,
                        "language": language if language else None,
                        "topics": [str(t) for t in topics],
                        "pushed": str(pushed) if pushed else None,
                        "star_velocity": velocity,
                        "forks": forks,
                    },
                )
            )
        return out

    # ------------------------------------------------------------------ #
    # Mock path                                                          #
    # ------------------------------------------------------------------ #
    def _fetch_mock(self, query_terms: list[str], note: str) -> FetchResult:
        """Load fixture repos -> RawItems. Robust to a missing/broken fixture."""
        rows = _load_fixture()
        if not rows:
            return self._empty_report(
                query_terms,
                SourceStatus.MOCK,
                "mock fixture unavailable",
            )
        items = self._rows_to_items(rows)
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

    def _degrade(self, query_terms: list[str], note: str, blocked: bool) -> FetchResult:
        """Degrade a failed/blocked live call: mock if allowed, else UNAVAILABLE."""
        settings = get_settings()
        if settings.allow_mock:
            return self._fetch_mock(query_terms, note=f"{note}; served mock")
        return self._empty_report(query_terms, SourceStatus.UNAVAILABLE, note)

    # ------------------------------------------------------------------ #
    # Cache rehydration                                                  #
    # ------------------------------------------------------------------ #
    def _from_cache(self, cached: dict, query_terms: list[str]) -> FetchResult:
        try:
            items = [RawItem.model_validate(i) for i in cached.get("items", [])]
            rep_raw = cached.get("report")
            report = SourceReport.model_validate(rep_raw) if rep_raw else None
        except Exception:
            # Corrupt cache entry -> re-derive a mock so we never raise.
            return self._fetch_mock(query_terms, note="cache miss (corrupt); mock")
        return FetchResult(items=items, report=report)

    # ------------------------------------------------------------------ #
    # Query helpers                                                      #
    # ------------------------------------------------------------------ #
    def _query_terms(self, area: str, keywords: list[str]) -> list[str]:
        """Build the ordered, de-duplicated search-term list from the scope."""
        terms: list[str] = []
        for t in [area, *keywords]:
            t = (t or "").strip()
            if t and t.lower() not in {x.lower() for x in terms}:
                terms.append(t)
        return terms[:6]

    def _build_query(self, query_terms: list[str], phrase: bool) -> str:
        """GitHub Search qualifier string: keywords + a created:> recency bound.

        ``phrase=True`` quotes multi-word terms for precision; ``phrase=False``
        is the looser retry that just OR's bare words. Search matches name /
        description / README by default; we scope to the recency window with a
        ``created:>YYYY-MM-DD`` qualifier so we only surface *recent* repos.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS)).date().isoformat()
        terms = query_terms[:4] or ["developer tools"]
        if phrase:
            parts = [f'"{t}"' if " " in t else t for t in terms]
        else:
            # Loosen: split multi-word terms into bare words, dedupe.
            words: list[str] = []
            for t in terms:
                for w in t.split():
                    w = w.strip()
                    if w and w.lower() not in {x.lower() for x in words}:
                        words.append(w)
            parts = words[:6] or ["developer"]
        keyword_clause = " OR ".join(parts)
        return f"{keyword_clause} created:>{cutoff}"

    # ------------------------------------------------------------------ #
    # Small shared helpers                                               #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _freshest(items: list[RawItem]) -> datetime | None:
        dates = [i.created for i in items if i.created is not None]
        return max(dates) if dates else None

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
def _star_velocity(stars: int, created: datetime | None, now: datetime) -> float:
    """Stars accrued per day of repo age = ``stars / max(age_days, 1)``.

    A young repo with a lot of stars scores far higher than an old repo with the
    same count, which is exactly the "just took off" signal we want to surface.
    Undated repos fall back to a de-rated raw star count so they don't dominate.
    """
    if created is None:
        return round(float(max(0, stars)) * 0.1, 4)
    age_days = max(1.0, (now - created).total_seconds() / 86400.0)
    return round(float(max(0, stars)) / age_days, 4)


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
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _load_fixture() -> list[dict]:
    """Load the bundled realistic mock repos. Returns [] if unreadable."""
    try:
        payload = json.loads(_FIXTURE.read_text())
    except Exception:
        return []
    if isinstance(payload, dict):
        return payload.get("repos", []) or []
    if isinstance(payload, list):
        return payload
    return []
