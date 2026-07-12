"""Regulatory source adapter (Federal Register API).

Signal thesis: rules and proposed rules are *why-now* raw material. A final
rule with an effective date creates a compliance deadline (urgent, dated
demand); a proposed rule with an open comment window marks a shift incumbents
haven't priced in yet. Synthesis' ``why_now`` is the primary consumer; the
``empty_for_a_reason`` lens is the secondary one (a regulatory wall is a
classic reason a space is empty).

Keyless: ``federalregister.gov/api/v1/documents.json`` with
``conditions[term]`` + ``conditions[type][]=RULE/PRORULE``. We request an
explicit ``fields[]`` list because ``effective_on``/``comments_close_on`` are
NOT in the default field set (and ``comments_close_on`` is often null — kept
null, never invented). Note the API echoes ``type`` human-readable ("Rule",
"Proposed Rule"), not the enum sent.

Contract (``base.py``): ``fetch`` NEVER raises; ONE request per fetch (the
documented soft limit is ~1000 req/hr — irrelevant at our volume, but cache
anyway under ns=``ingest:regulatory``); degrades to fixture (MOCK) on failure.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..cache import get_cache
from ..config import get_settings
from ..schemas import RawItem, SourceName, SourceReport, SourceStatus
from .base import FetchResult, Source
from .common import coerce_dt, dedupe, freshest, query_terms

_FIXTURE = Path(__file__).parent / "fixtures" / "regulatory.json"

_API_URL = "https://www.federalregister.gov/api/v1/documents.json"
_HTTP_TIMEOUT = 15.0
_UA = {"User-Agent": "market-gap-finder/0.1"}

_FIELDS = [
    "title",
    "abstract",
    "html_url",
    "publication_date",
    "comments_close_on",
    "effective_on",
    "type",
    "agencies",
]


# --------------------------------------------------------------------------- #
# Pure parser — unit-testable on a canned payload, no network.                 #
# --------------------------------------------------------------------------- #
def parse_documents(payload: dict, now: datetime | None = None) -> list[RawItem]:
    """Federal Register ``results[]`` -> RawItems.

    ``abstract`` can be null (body stays empty — never invented). Weight is
    boosted for final Rules and for effective dates within ±12 months of now
    (a live compliance deadline). ``effective_on``/``comments_close_on`` ride
    in ``meta`` for the why_now consumer.
    """
    now = now or datetime.now(timezone.utc)
    items: list[RawItem] = []
    for doc in (payload or {}).get("results", []) or []:
        if not isinstance(doc, dict):
            continue
        title = str(doc.get("title") or "").strip()
        url = str(doc.get("html_url") or "").strip()
        if not title or not url:
            continue
        doc_type = str(doc.get("type") or "")  # echoed human-readable: "Rule"
        effective_on = doc.get("effective_on")  # often null — keep it honest
        weight = 1.0
        if doc_type.lower() == "rule":
            weight += 0.3
        eff_dt = coerce_dt(effective_on)
        if eff_dt is not None and abs((eff_dt - now).days) <= 365:
            weight += 0.3
        agencies = [
            str(a.get("name") or a.get("raw_name") or "")
            for a in (doc.get("agencies") or [])
            if isinstance(a, dict)
        ]
        items.append(
            RawItem(
                source=SourceName.REGULATORY,
                id=f"fedreg:{url}",
                title=title,
                body=str(doc.get("abstract") or ""),
                url=url,
                created=coerce_dt(doc.get("publication_date")),
                weight=round(weight, 2),
                meta={
                    "type": doc_type,
                    "effective_on": effective_on,
                    "comments_close_on": doc.get("comments_close_on"),
                    "agencies": [a for a in agencies if a],
                },
            )
        )
    return items


class RegulatorySource(Source):
    """Rule/proposed-rule radar over the keyless Federal Register API."""

    name = SourceName.REGULATORY
    description = (
        "Searches the Federal Register (keyless) for final and proposed rules "
        "matching the segment: compliance deadlines and comment windows are "
        "dated 'why now' evidence, and regulatory walls explain empty spaces."
    )

    @property
    def live(self) -> bool:
        return get_settings().regulatory_live

    # ------------------------------------------------------------------ #
    def fetch(
        self, area: str, keywords: list[str], sub_segments: list[str]
    ) -> FetchResult:
        terms = query_terms(area, keywords)
        cache = get_cache()
        cached = cache.get("ingest:regulatory", area, terms)
        if cached is not None:
            return self._from_cache(cached, terms)

        try:
            result = self._fetch_live(terms)
        except Exception as exc:  # belt-and-braces: fetch NEVER raises
            return self._fetch_mock(
                terms,
                note=f"live Federal Register fetch failed ({type(exc).__name__}); mock",
            )
        try:
            cache.set(
                "ingest:regulatory",
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
        per_page = max(1, min(20, int(settings.regulatory_max_items)))
        # httpx encodes list-valued params as repeated keys — exactly what the
        # Rails-style conditions[type][] / fields[] parameters need.
        params: list[tuple[str, str]] = [
            ("conditions[term]", " ".join(terms[:4]) or "software"),
            ("conditions[type][]", "RULE"),
            ("conditions[type][]", "PRORULE"),
            ("per_page", str(per_page)),
            ("order", "newest"),
        ]
        params += [("fields[]", f) for f in _FIELDS]

        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT, headers=_UA) as client:
                resp = client.get(_API_URL, params=params)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}")
            payload = resp.json()
            items = parse_documents(payload)
        except Exception as exc:  # never raise out of an adapter
            if settings.allow_mock:
                return self._fetch_mock(
                    terms,
                    note=f"live Federal Register fetch failed ({type(exc).__name__}); mock",
                )
            return self._empty(
                terms, SourceStatus.UNAVAILABLE, f"Federal Register fetch failed: {exc}"
            )

        items = dedupe(items)
        if not items:
            return self._empty(
                terms,
                SourceStatus.EMPTY,
                "Federal Register has no recent rules matching these keywords",
            )

        items.sort(key=lambda i: i.weight, reverse=True)
        report = SourceReport(
            name=self.name,
            status=SourceStatus.LIVE,
            item_count=len(items),
            freshest=freshest(items),
            note="keyless Federal Register API (RULE + PRORULE, newest first)",
            query_terms=terms,
        )
        return FetchResult(items=items, report=report)

    # -- mock / cache / empty -------------------------------------------- #
    def _fetch_mock(self, terms: list[str], note: str) -> FetchResult:
        try:
            payload = json.loads(_FIXTURE.read_text())
            items = parse_documents(payload)
        except Exception as exc:
            return self._empty(
                terms, SourceStatus.MOCK, f"mock fixture unavailable: {type(exc).__name__}"
            )
        items = dedupe(items)
        items.sort(key=lambda i: i.weight, reverse=True)
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
