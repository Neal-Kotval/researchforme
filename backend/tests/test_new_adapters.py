"""Phase-3 adapter tests (contract C1 + S4): jobs, appreviews, regulatory,
outcomes, postmortems.

Per adapter, four things are pinned — all hermetic, NO network:

* the **live-parse** function handles a canned real-shaped payload (including
  each API's documented gotchas: RemoteOK's legal-notice element, Apple's
  ``{label}`` envelopes / single-object ``entry`` / metadata-first entry,
  Federal Register's null abstract + human-readable ``type``, yc-oss's epoch
  ``launched_at`` and ``Inactive``-means-dead status);
* the **fixture fallback** path: a raising ``_fetch_live`` degrades to MOCK
  with items served, and every fixture URL is unresolvable by construction
  (the provenance sweep in test_fixture_provenance covers the files; here we
  check the items an adapter actually emits);
* **registry** exposure, including the new ``pressure_only`` tier: outcomes +
  postmortems are excluded from the default demand mix but reachable by name
  and via ``include_pressure_only=True``;
* the **pressure corroboration tool builder** exposes search_outcomes /
  search_postmortems only when asked to.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.schemas import SourceName, SourceStatus
from app.sources.appreviews import (
    AppReviewsSource,
    parse_apps,
    parse_review_entries,
    reweight_by_complaint_recurrence,
)
from app.sources.jobs import (
    JobsSource,
    parse_hiring_comments,
    parse_remoteok,
    parse_wwr,
    pick_hiring_thread,
)
from app.sources.outcomes import OutcomesSource, filter_companies
from app.sources.postmortems import PostmortemsSource, match_postmortems
from app.sources.registry import get_source, get_sources
from app.sources.regulatory import RegulatorySource, parse_documents

_FIXTURES = Path("app/sources/fixtures")


@pytest.fixture()
def hermetic_env(tmp_path, monkeypatch):
    """Temp cache + settings reset so fetches never touch a shared cache."""
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "test_cache.db"))

    from app import cache as cache_mod
    from app import config as config_mod

    def _reset() -> None:
        config_mod.get_settings.cache_clear()
        cache_mod._cache = None

    _reset()
    yield
    _reset()


def _boom(*args, **kwargs):
    raise ConnectionError("network unreachable (test)")


# --------------------------------------------------------------------------- #
# Registry + pressure_only tier                                                #
# --------------------------------------------------------------------------- #
def test_registry_default_mix_excludes_pressure_only():
    names = [s.name for s in get_sources()]
    for expected in (SourceName.JOBS, SourceName.APPREVIEWS, SourceName.REGULATORY):
        assert expected in names
    assert SourceName.OUTCOMES not in names
    assert SourceName.POSTMORTEMS not in names


def test_registry_include_pressure_only_exposes_everything():
    names = [s.name for s in get_sources(include_pressure_only=True)]
    assert SourceName.OUTCOMES in names
    assert SourceName.POSTMORTEMS in names
    # ... and it is a strict superset of the demand mix.
    assert set(s.name for s in get_sources()) < set(names)


def test_get_source_reaches_pressure_only_adapters():
    assert isinstance(get_source(SourceName.OUTCOMES), OutcomesSource)
    assert isinstance(get_source(SourceName.POSTMORTEMS), PostmortemsSource)
    assert get_source(SourceName.OUTCOMES).pressure_only is True
    assert get_source(SourceName.POSTMORTEMS).pressure_only is True
    assert get_source(SourceName.JOBS).pressure_only is False


def test_corroboration_tools_gate_pressure_only_sources():
    from app.analysis.synthesize import build_corroboration_tools

    default_names = {t.name for t in build_corroboration_tools("bookkeeping", [])}
    assert {"search_jobs", "search_appreviews", "search_regulatory"} <= default_names
    assert "search_outcomes" not in default_names
    assert "search_postmortems" not in default_names

    pressure_names = {
        t.name
        for t in build_corroboration_tools(
            "bookkeeping", [], include_pressure_only=True
        )
    }
    assert {"search_outcomes", "search_postmortems"} <= pressure_names
    assert default_names < pressure_names


# --------------------------------------------------------------------------- #
# Jobs                                                                         #
# --------------------------------------------------------------------------- #
def test_parse_remoteok_skips_legal_notice_and_maps_fields():
    payload = [
        {"legal": "API terms...", "last_updated": 1767225600},  # element [0]
        {
            "id": "12345",
            "position": "DevOps Engineer",
            "company": "ExampleCo",
            "date": "2026-07-01T10:00:00+00:00",
            "url": "https://remoteok.example.org/remote-jobs/12345",
            "tags": ["devops"],
            "description": "<p>Automate <b>everything</b>.</p>",
        },
    ]
    items = parse_remoteok(payload)
    assert len(items) == 1
    item = items[0]
    assert item.source is SourceName.JOBS
    assert item.title == "DevOps Engineer @ ExampleCo"
    assert "Automate everything" in item.body and "<" not in item.body  # HTML stripped
    assert item.created == datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)


def test_parse_wwr_filters_by_keyword_client_side():
    entries = [
        {
            "title": "Acme: Senior DevOps Engineer",
            "link": "https://weworkremotely.example.org/jobs/1",
            "published": "Tue, 23 Jun 2026 10:00:00 +0000",
            "description": "Own our devops pipeline.",
        },
        {
            "title": "Other: Sales Rep",
            "link": "https://weworkremotely.example.org/jobs/2",
            "published": "Tue, 23 Jun 2026 11:00:00 +0000",
            "description": "Sell things.",
        },
    ]
    items = parse_wwr(entries, ["devops"])
    assert [i.title for i in items] == ["Acme: Senior DevOps Engineer"]
    assert items[0].created is not None  # RFC822 pubDate parsed


def test_parse_hiring_comments_keeps_top_level_only_and_thread_picker_filters():
    hits = [
        {
            "objectID": "48747001",
            "comment_text": "Acme | DevOps | Remote. Real job post.",
            "created_at": "2026-07-02T14:00:00Z",
            "parent_id": 48747000,
            "story_id": 48747000,
        },
        {
            "objectID": "48747002",
            "comment_text": "A reply, not a job.",
            "created_at": "2026-07-02T15:00:00Z",
            "parent_id": 48747001,
            "story_id": 48747000,
        },
    ]
    items = parse_hiring_comments(hits)
    assert len(items) == 1
    assert items[0].url.endswith("id=48747001")

    # The whoishiring account posts several thread types — title must gate.
    threads = [
        {"objectID": "1", "title": "Ask HN: Who wants to be hired? (July 2026)"},
        {"objectID": "2", "title": "Ask HN: Who is hiring? (July 2026)"},
    ]
    assert pick_hiring_thread(threads) == "2"


def test_jobs_fixture_fallback_reports_mock(hermetic_env, monkeypatch):
    monkeypatch.setattr(JobsSource, "_fetch_live", _boom)
    result = JobsSource().fetch("devops automation", ["devops"], [])
    assert result.report.status is SourceStatus.MOCK
    assert "mock" in (result.report.note or "").lower()
    assert result.items, "the fixture must still feed the pipeline"
    for item in result.items:
        host = item.url.split("/")[2]
        assert host == "example.org" or host.endswith(".example.org") or "id=99" in item.url


# --------------------------------------------------------------------------- #
# App reviews                                                                  #
# --------------------------------------------------------------------------- #
def test_parse_apps_sorts_by_rating_count():
    payload = {
        "resultCount": 2,
        "results": [
            {"trackId": 1, "trackName": "Small", "userRatingCount": 10},
            {"trackId": 2, "trackName": "Big", "userRatingCount": 99999},
        ],
    }
    apps = parse_apps(payload, max_apps=1)
    assert [a["trackName"] for a in apps] == ["Big"]


def test_parse_review_entries_handles_all_feed_shapes():
    meta_entry = {"im:name": {"label": "App"}, "title": {"label": "App"}}
    review = {
        "id": {"label": "r1"},
        "author": {"name": {"label": "u"}},
        "im:rating": {"label": "2"},
        "updated": {"label": "2026-07-08T08:32:42-07:00"},
        "title": {"label": "Sync is broken"},
        "content": {"label": "Crashes constantly and support ignores me."},
    }
    five_star = {**review, "id": {"label": "r2"}, "im:rating": {"label": "5"}}

    # list shape, metadata-first, 5-star filtered client-side
    items = parse_review_entries(
        {"feed": {"entry": [meta_entry, review, five_star]}}, "App", 990000001
    )
    assert len(items) == 1
    assert items[0].meta["rating"] == 2
    assert items[0].title.startswith("[App]")
    assert items[0].url == "https://apps.apple.com/us/app/id990000001"

    # single-object shape
    assert len(parse_review_entries({"feed": {"entry": review}}, "App", 1)) == 1
    # absent entry
    assert parse_review_entries({"feed": {}}, "App", 1) == []


def test_reweight_by_complaint_recurrence_boosts_recurring_themes():
    from app.schemas import RawItem

    def _mk(id_, body):
        return RawItem(
            source=SourceName.APPREVIEWS, id=id_, title="t", body=body, weight=1.0
        )

    items = [
        _mk("a", "It crashes on every sync."),
        _mk("b", "Constant crash when I sync my data."),
        _mk("c", "The color scheme is not my taste."),
    ]
    reweight_by_complaint_recurrence(items)
    by_id = {i.id: i.weight for i in items}
    assert by_id["a"] > by_id["c"] and by_id["b"] > by_id["c"]
    assert by_id["c"] == 1.0  # no recurring complaint vocabulary


def test_appreviews_fixture_fallback_reports_mock(hermetic_env, monkeypatch):
    monkeypatch.setattr(AppReviewsSource, "_fetch_live", _boom)
    result = AppReviewsSource().fetch("budgeting apps", ["budgeting"], [])
    assert result.report.status is SourceStatus.MOCK
    assert result.items
    for item in result.items:
        assert item.meta["rating"] <= 3  # the 1-3 star cut applies to mock too
        assert "id99" in item.url  # minted from out-of-range trackIds


# --------------------------------------------------------------------------- #
# Regulatory                                                                   #
# --------------------------------------------------------------------------- #
def test_parse_documents_maps_fields_and_handles_nulls():
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    payload = {
        "results": [
            {
                "title": "Some Final Rule",
                "type": "Rule",  # echoed human-readable, not the RULE enum
                "abstract": None,  # null abstract stays empty — never invented
                "html_url": "https://www.federalregister.example.org/d/1",
                "publication_date": "2026-05-18",
                "effective_on": "2026-11-03",
                "comments_close_on": None,
                "agencies": [{"name": "HHS"}],
            },
            {
                "title": "Some Proposed Rule",
                "type": "Proposed Rule",
                "abstract": "A proposal.",
                "html_url": "https://www.federalregister.example.org/d/2",
                "publication_date": "2026-06-02",
                "effective_on": None,
                "comments_close_on": "2026-08-15",
                "agencies": [],
            },
        ]
    }
    items = parse_documents(payload, now=now)
    assert len(items) == 2
    rule, prorule = items
    assert rule.body == ""  # null abstract -> empty, not fabricated
    assert rule.meta["effective_on"] == "2026-11-03"
    assert rule.meta["comments_close_on"] is None
    assert rule.meta["agencies"] == ["HHS"]
    # Final rule + effective date within 12 months outweighs a proposal.
    assert rule.weight > prorule.weight
    assert prorule.meta["comments_close_on"] == "2026-08-15"
    assert rule.created == datetime(2026, 5, 18, tzinfo=timezone.utc)


def test_regulatory_fixture_fallback_reports_mock(hermetic_env, monkeypatch):
    monkeypatch.setattr(RegulatorySource, "_fetch_live", _boom)
    result = RegulatorySource().fetch("telehealth", ["telehealth"], [])
    assert result.report.status is SourceStatus.MOCK
    assert result.items
    for item in result.items:
        host = item.url.split("/")[2]
        assert host.endswith(".example.org")


# --------------------------------------------------------------------------- #
# Outcomes (pressure-only)                                                     #
# --------------------------------------------------------------------------- #
def test_filter_companies_matches_keywords_and_surfaces_status():
    companies = [
        {
            "name": "DeadCo",
            "slug": "deadco",
            "one_liner": "Bookkeeping sync for freelancers.",
            "batch": "W21",
            "status": "Inactive",  # the directory's spelling of "dead"
            "industry": "Fintech",
            "tags": ["Bookkeeping"],
            "launched_at": 1611100800,  # unix epoch, not ISO
            "url": "https://ycombinator.example.org/companies/deadco",
        },
        {
            "name": "AliveCo",
            "slug": "aliveco",
            "one_liner": "Bookkeeping automation.",
            "batch": "S22",
            "status": "Active",
            "industry": "Fintech",
            "tags": [],
            "launched_at": 1651363200,
            "url": "https://ycombinator.example.org/companies/aliveco",
        },
        {
            "name": "OffTopic",
            "slug": "offtopic",
            "one_liner": "Rocket engines.",
            "batch": "W20",
            "status": "Active",
            "industry": "Space",
            "tags": [],
            "launched_at": 1580515200,
            "url": "https://ycombinator.example.org/companies/offtopic",
        },
    ]
    items = filter_companies(companies, ["bookkeeping"], cap=10)
    assert [i.meta["status"] for i in items] == ["Inactive", "Active"]  # dead first
    assert items[0].title == "DeadCo (W21) — Inactive"
    assert items[0].created == datetime.fromtimestamp(1611100800, tz=timezone.utc)
    assert all("OffTopic" not in i.title for i in items)


def test_outcomes_fixture_fallback_reports_mock(hermetic_env, monkeypatch):
    monkeypatch.setattr(OutcomesSource, "_fetch_live", _boom)
    result = OutcomesSource().fetch("bookkeeping tools", ["bookkeeping"], [])
    assert result.report.status is SourceStatus.MOCK
    assert result.items
    for item in result.items:
        host = item.url.split("/")[2]
        assert host.endswith(".example.org")
        assert item.meta["status"] in {"Active", "Acquired", "Inactive", "Public"}


# --------------------------------------------------------------------------- #
# Post-mortems (pressure-only, honestly MOCK forever)                          #
# --------------------------------------------------------------------------- #
def test_postmortems_is_always_mock_and_never_claims_live(hermetic_env):
    src = PostmortemsSource()
    assert src.live is False  # no keyless live source exists — stay honest
    result = src.fetch("bookkeeping for freelancers", ["bookkeeping"], [])
    assert result.report.status is SourceStatus.MOCK
    assert result.items, "ScaleFactor/LedgerNest-class failures must match"
    for item in result.items:
        assert item.meta["external"] is True  # graveyard merge flag (S4)
        assert item.meta["kill_reason"]
        host = item.url.split("/")[2]
        assert host.endswith(".example.org")  # citations defused per provenance rule


def test_match_postmortems_drops_unrelated_entries():
    corpus = json.loads((_FIXTURES / "postmortems.json").read_text())
    assert len(corpus) >= 30, "contract S4 calls for ~30 curated failures"
    items = match_postmortems(corpus, ["telehealth", "clinics"], cap=12)
    assert items, "healthcare failures (Babylon, Call9...) must match"
    titles = " ".join(i.title for i in items)
    assert "Juicero" not in titles, "unrelated obituaries are not corroboration"
    # Ranked: every returned item genuinely overlaps the query.
    assert all(i.weight > 0 for i in items)


def test_match_postmortems_empty_query_returns_nothing_forced(hermetic_env):
    # A scope with zero overlap yields an honest empty result, not filler.
    result = PostmortemsSource().fetch("zzqx unmatched segment", ["zzqxthing"], [])
    assert result.report.status is SourceStatus.MOCK
    assert result.items == []
    assert result.report.item_count == 0
