"""Startup post-mortem corpus adapter.

Signal thesis: the fastest way to pressure-test "why is this space empty?" is
to read the obituaries of the companies that already tried. Matching
post-mortems are appended as corroboration context to the
``empty_for_a_reason`` and ``crowded`` lenses, and merged into the graveyard
endpoint flagged ``external: true``.

**Honesty note (S4):** the research pass (2026-07-12) found NO keyless, stable
public source — Failory's graveyard is JS-rendered (needs a headless browser),
CB Insights is a single fragile editorial page, and the GitHub datasets are
unproven single-owner repos. Per the build contract, this adapter therefore
ships a curated seed corpus (``fixtures/postmortems.json``: ~30 real,
well-documented startup failures with segment keywords, kill reason, year and
a citation pointer) served through the same Source ABC and **always reported
as MOCK** (``live`` is False by construction) until a real live source exists.
The corpus facts are genuine public knowledge; the citation URLs are defused
``*.example.org`` mirrors per the fixture-provenance rule (fixtures must never
carry resolvable URLs).

This adapter is **pressure_only**: excluded from the default demand mix and
only consulted at pressure-test time (and by the graveyard endpoint).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_settings
from ..schemas import RawItem, SourceName, SourceReport, SourceStatus
from .base import FetchResult, Source
from .common import dedupe, freshest, query_terms

_FIXTURE = Path(__file__).parent / "fixtures" / "postmortems.json"

_WORD_RE = re.compile(r"[a-z0-9']+")
_STOP = {
    "the", "a", "an", "and", "or", "for", "of", "to", "in", "on", "with",
    "tools", "tool", "app", "apps", "software", "platform", "service",
    "services", "startup", "startups", "company", "companies",
}


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOP}


# --------------------------------------------------------------------------- #
# Pure matcher — unit-testable, no I/O.                                        #
# --------------------------------------------------------------------------- #
def match_postmortems(corpus: list, terms: list[str], cap: int) -> list[RawItem]:
    """Rank the curated corpus by token overlap with the segment terms.

    Overlap is scored against each entry's ``segment_keywords`` (weighted
    double — they exist precisely for this match), name, kill reason and
    summary. Zero-overlap entries are dropped: an unrelated obituary is not
    corroboration.
    """
    query = _tokens(" ".join(terms))
    scored: list[tuple[float, RawItem]] = []
    for entry in corpus or []:
        if not isinstance(entry, dict) or not entry.get("company"):
            continue
        kw_tokens = _tokens(" ".join(str(k) for k in (entry.get("segment_keywords") or [])))
        other_tokens = _tokens(
            " ".join(
                [
                    str(entry.get("company") or ""),
                    str(entry.get("kill_reason") or ""),
                    str(entry.get("summary") or ""),
                ]
            )
        )
        score = 2.0 * len(query & kw_tokens) + 1.0 * len(query & other_tokens)
        if query and score <= 0:
            continue
        company = str(entry.get("company") or "")
        year = entry.get("year")
        kill_reason = str(entry.get("kill_reason") or "")
        created = None
        try:
            if year:
                created = datetime(int(year), 1, 1, tzinfo=timezone.utc)
        except (TypeError, ValueError):
            created = None
        item = RawItem(
            source=SourceName.POSTMORTEMS,
            id=f"postmortem:{company.lower().replace(' ', '-')}",
            title=f"{company} ({year}) — {kill_reason}" if year else f"{company} — {kill_reason}",
            body=str(entry.get("summary") or ""),
            url=str(entry.get("citation_url") or ""),
            created=created,
            weight=round(max(score, 0.1), 2),
            meta={
                "kill_reason": kill_reason,
                "year": year,
                "segment_keywords": [str(k) for k in (entry.get("segment_keywords") or [])],
                "external": True,  # graveyard merge flag (S4)
            },
        )
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[: max(1, cap)]]


class PostmortemsSource(Source):
    """Curated startup-failure corpus (honest MOCK — no live source exists)."""

    name = SourceName.POSTMORTEMS
    description = (
        "Curated corpus of ~30 documented startup failures (segment keywords, "
        "kill reason, year): 'someone already tried this and died because X' "
        "corroboration for the empty-for-a-reason and crowded lenses. Served "
        "from a seed corpus — no keyless live source exists, so it is honestly "
        "reported as mock data."
    )
    pressure_only = True  # excluded from the default demand mix / _fetch_all

    @property
    def live(self) -> bool:
        return get_settings().postmortems_live  # always False (see config)

    # ------------------------------------------------------------------ #
    def fetch(
        self, area: str, keywords: list[str], sub_segments: list[str]
    ) -> FetchResult:
        """Serve keyword-matched corpus entries. Always MOCK, never raises."""
        terms = query_terms(area, keywords)
        settings = get_settings()
        try:
            corpus = json.loads(_FIXTURE.read_text())
        except Exception as exc:
            return FetchResult(
                items=[],
                report=SourceReport(
                    name=self.name,
                    status=SourceStatus.MOCK,
                    item_count=0,
                    note=f"curated corpus unavailable: {type(exc).__name__}",
                    query_terms=terms,
                ),
            )

        try:
            items = match_postmortems(
                corpus, terms, cap=max(1, int(settings.postmortems_max_items))
            )
        except Exception:
            items = []
        items = dedupe(items)
        note = (
            "curated post-mortem corpus (no keyless live source exists; "
            f"{len(items)} of {len(corpus)} entries matched)"
        )
        return FetchResult(
            items=items,
            report=SourceReport(
                name=self.name,
                status=SourceStatus.MOCK,  # honest: this is seed data, not live
                item_count=len(items),
                freshest=freshest(items),
                note=note,
                query_terms=terms,
            ),
        )
