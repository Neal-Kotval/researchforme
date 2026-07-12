"""Outcomes source adapter (YC public directory via the yc-oss static API).

Signal thesis: what happened to the *funded* companies in a segment is the
sharpest corroboration a pressure test can get. A cluster of ``Inactive`` YC
companies is direct evidence for the ``empty_for_a_reason`` lens ("people
tried; here's the graveyard"); a wall of ``Active``/``Public`` ones feeds
``crowded``. That's why this adapter is **pressure_only**: it is excluded from
the default demand mix (``registry.get_sources()`` / ``_fetch_all``) and only
consulted as a corroboration tool at pressure-test time.

Source: ``yc-oss.github.io/api/companies/all.json`` — a ~2 MB static JSON of
~6k YC companies, refreshed daily by GitHub Actions, hosted on GitHub Pages
(no auth, no rate limit). We download it at most once per cache TTL and filter
in memory against ``one_liner + long_description + tags + industry``.
``status`` is exactly one of ``Active | Acquired | Inactive | Public`` — there
is no "dead" value; dead == ``Inactive``. ``launched_at`` is a unix epoch.

Contract (``base.py``): ``fetch`` NEVER raises; degrades to fixture (MOCK) on
any failure; filtered results cached under ns=``ingest:outcomes``, the raw
directory under the reserved key ``__directory__``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from ..cache import get_cache
from ..config import get_settings
from ..schemas import RawItem, SourceName, SourceReport, SourceStatus
from .base import FetchResult, Source
from .common import coerce_dt, dedupe, freshest, query_terms

_FIXTURE = Path(__file__).parent / "fixtures" / "outcomes.json"

_DIRECTORY_URL = "https://yc-oss.github.io/api/companies/all.json"
_HTTP_TIMEOUT = 30.0  # ~2 MB download
_UA = {"User-Agent": "market-gap-finder/0.1"}


# --------------------------------------------------------------------------- #
# Pure filter — unit-testable on a canned directory, no network.               #
# --------------------------------------------------------------------------- #
def filter_companies(companies: list, terms: list[str], cap: int) -> list[RawItem]:
    """Keyword-match the YC directory in memory -> RawItems.

    The lens payload needs the outcome explicitly, so ``status`` rides both in
    the title ("Name (batch) — status") and in ``meta``. Inactive (the
    directory's spelling of "dead") sorts first: the graveyard is the point.
    """
    lowered = [t.lower() for t in terms if t]
    matched: list[RawItem] = []
    for c in companies or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        blob = " ".join(
            [
                str(c.get("one_liner") or ""),
                str(c.get("long_description") or ""),
                " ".join(str(t) for t in (c.get("tags") or [])),
                str(c.get("industry") or ""),
                str(c.get("subindustry") or ""),
            ]
        ).lower()
        if lowered and not any(t in blob for t in lowered):
            continue
        name = str(c.get("name") or "")
        batch = str(c.get("batch") or "?")
        status = str(c.get("status") or "Unknown")  # Active|Acquired|Inactive|Public
        body = str(c.get("one_liner") or "") or str(c.get("long_description") or "")
        matched.append(
            RawItem(
                source=SourceName.OUTCOMES,
                id=f"yc:{c.get('slug') or c.get('id') or name}",
                title=f"{name} ({batch}) — {status}",
                body=body,
                url=str(c.get("url") or ""),  # ycombinator.com profile page
                created=coerce_dt(c.get("launched_at")),  # unix epoch
                weight=1.0,
                meta={
                    "status": status,
                    "batch": batch,
                    "industry": str(c.get("industry") or ""),
                    "tags": [str(t) for t in (c.get("tags") or [])],
                    "team_size": c.get("team_size"),
                },
            )
        )
    # Graveyard first (Inactive), then the rest — the lenses read the dead.
    matched.sort(key=lambda i: (i.meta.get("status") != "Inactive", i.title.lower()))
    return matched[: max(1, cap)]


class OutcomesSource(Source):
    """Funded-company outcome lookups over the yc-oss static YC directory."""

    name = SourceName.OUTCOMES
    description = (
        "Looks up funded YC companies in this segment and their outcome "
        "(Active / Acquired / Inactive / Public) from the yc-oss static "
        "directory: direct evidence for the crowded and empty-for-a-reason "
        "lenses. Pressure-test corroboration only, not a demand source."
    )
    pressure_only = True  # excluded from the default demand mix / _fetch_all

    @property
    def live(self) -> bool:
        return get_settings().outcomes_live

    # ------------------------------------------------------------------ #
    def fetch(
        self, area: str, keywords: list[str], sub_segments: list[str]
    ) -> FetchResult:
        terms = query_terms(area, keywords)
        cache = get_cache()
        cached = cache.get("ingest:outcomes", area, terms)
        if cached is not None:
            return self._from_cache(cached, terms)

        try:
            result = self._fetch_live(terms)
        except Exception as exc:  # belt-and-braces: fetch NEVER raises
            return self._fetch_mock(
                terms,
                note=f"yc-oss directory fetch failed ({type(exc).__name__}); mock",
            )
        try:
            cache.set(
                "ingest:outcomes",
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
    def _fetch_live(self, terms: list[str]) -> FetchResult:
        settings = get_settings()
        try:
            companies = self._directory()
            items = filter_companies(
                companies, terms, cap=max(1, int(settings.outcomes_max_items))
            )
        except Exception as exc:  # never raise out of an adapter
            if settings.allow_mock:
                return self._fetch_mock(
                    terms,
                    note=f"yc-oss directory fetch failed ({type(exc).__name__}); mock",
                )
            return self._empty(
                terms, SourceStatus.UNAVAILABLE, f"yc-oss directory fetch failed: {exc}"
            )

        items = dedupe(items)
        if not items:
            return self._empty(
                terms,
                SourceStatus.EMPTY,
                "no YC companies matched these keywords in the yc-oss directory",
            )
        inactive = sum(1 for i in items if i.meta.get("status") == "Inactive")
        report = SourceReport(
            name=self.name,
            status=SourceStatus.LIVE,
            item_count=len(items),
            freshest=freshest(items),
            note=f"yc-oss static YC directory ({inactive} Inactive of {len(items)} matched)",
            query_terms=terms,
        )
        return FetchResult(items=items, report=report)

    def _directory(self) -> list:
        """The ~2 MB all.json, downloaded at most once per cache TTL."""
        cache = get_cache()
        cached = cache.get("ingest:outcomes", "__directory__")
        if isinstance(cached, list) and cached:
            return cached
        with httpx.Client(
            timeout=_HTTP_TIMEOUT, headers=_UA, follow_redirects=True
        ) as client:
            resp = client.get(_DIRECTORY_URL)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}")
        companies = resp.json()
        if not isinstance(companies, list):
            raise ValueError("unexpected directory shape")
        try:
            cache.set("ingest:outcomes", companies, "__directory__")
        except Exception:
            pass
        return companies

    # -- mock / cache / empty -------------------------------------------- #
    def _fetch_mock(self, terms: list[str], note: str) -> FetchResult:
        try:
            companies = json.loads(_FIXTURE.read_text())
        except Exception as exc:
            return self._empty(
                terms, SourceStatus.MOCK, f"mock fixture unavailable: {type(exc).__name__}"
            )
        # The fixture is tiny and curated: serve it whole (still keyword-first).
        items = filter_companies(
            companies, terms, cap=max(1, int(get_settings().outcomes_max_items))
        ) or filter_companies(companies, [], cap=max(1, int(get_settings().outcomes_max_items)))
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
