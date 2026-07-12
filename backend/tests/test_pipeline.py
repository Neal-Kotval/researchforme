"""End-to-end pipeline test with the zero-dependency fixture backend.

Runs the whole loop (scope -> parallel mock ingest -> extract -> fixture
synthesis -> rank) with ``LLM_BACKEND=fixture`` and no source credentials, then
verifies:

* the report contains at least one ranked gap, each with a valid weighted
  composite, contiguous ranks, and (for the fixture gaps) 5 named competitors;
* re-ranking the cached synthesis with a very different weighting re-orders the
  gaps *sanely* — no re-fetch, and the ordering demonstrably changes.
"""

from __future__ import annotations

import pytest

from app.schemas import AnalyzeRequest, RankRequest, Weights, SCORE_KEYS


@pytest.fixture()
def fixture_env(tmp_path, monkeypatch):
    """Force the fixture LLM backend + a hermetic temp cache + mock sources."""
    monkeypatch.setenv("LLM_BACKEND", "fixture")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "test_cache.db"))
    # Strip any live credentials so every source degrades to its mock path.
    for var in (
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "GITHUB_TOKEN",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    # The foundation memoizes settings and lazily builds singletons; reset them
    # so our env wins both before and after the test.
    from app import cache as cache_mod
    from app import config as config_mod
    from app.llm import client as client_mod

    def _reset() -> None:
        config_mod.get_settings.cache_clear()
        cache_mod._cache = None
        client_mod._client = None

    _reset()
    yield
    _reset()


def _assert_valid_scores(scores) -> None:
    for key in SCORE_KEYS:
        value = getattr(scores, key)
        assert 1 <= value <= 5, f"{key}={value} out of the 1..5 band"


def _assert_well_ranked(gaps) -> None:
    """Ranks are contiguous 1..N and composites are sorted descending in 0..5."""
    assert [g.rank for g in gaps] == list(range(1, len(gaps) + 1))
    composites = [g.composite for g in gaps]
    assert composites == sorted(composites, reverse=True)
    for g in gaps:
        assert 0.0 <= g.composite <= 5.0


@pytest.mark.asyncio
async def test_run_analysis_and_rerank(fixture_env):
    from app.pipeline import rerank_cached, run_analysis

    area = "bookkeeping tools for freelancers"
    req = AnalyzeRequest(area=area, sub_segments=[])

    report = await run_analysis(req)

    # --- the report is grounded in the fixture synthesis ---------------------
    assert report.llm_mode == "fixture"
    assert report.cache_hit is False
    assert len(report.gaps) >= 1
    # Every demand-mix source produced a telemetry row (reddit, arxiv,
    # hackernews, github, newsletter, jobs, appreviews, regulatory) —
    # mock/degraded, since no creds and no network in tests. The two
    # pressure-only adapters (outcomes, postmortems) are correctly absent.
    from app.sources.registry import get_sources

    assert len(report.sources) == len(get_sources())

    # --- at least one ranked gap with 5 competitors and valid scores ---------
    top = report.gaps[0]
    assert top.rank == 1
    assert len(top.gap.competitors) == 5
    _assert_valid_scores(top.gap.scores)
    _assert_well_ranked(report.gaps)

    default_order = [g.gap.title for g in report.gaps]

    # --- rerank the cached synthesis with a very different weighting ----------
    # All weight on trend_tailwind. This must re-order WITHOUT re-fetching.
    trend_heavy = Weights(
        demand_strength=0.0,
        competitive_openness=0.0,
        trend_tailwind=1.0,
        feasibility=0.0,
        willingness_to_pay=0.0,
    )
    rank_req = RankRequest(area=area, sub_segments=[], weights=trend_heavy)
    reranked = rerank_cached(rank_req)

    assert reranked is not None, "expected a cached synthesis to re-rank"
    assert reranked.cache_hit is True
    assert len(reranked.gaps) == len(report.gaps)
    _assert_well_ranked(reranked.gaps)

    reranked_order = [g.gap.title for g in reranked.gaps]
    # A sharply different weighting must produce a different ordering.
    assert reranked_order != default_order
    # And the top gap under trend-heavy weights should be the highest-trend one.
    assert reranked.gaps[0].gap.scores.trend_tailwind == max(
        g.gap.scores.trend_tailwind for g in reranked.gaps
    )


def test_rerank_cached_miss_returns_none(fixture_env):
    """A cold cache yields None so callers can fall back to a full run."""
    from app.pipeline import rerank_cached

    missing = RankRequest(
        area="a totally un-analyzed area", sub_segments=[], weights=Weights()
    )
    assert rerank_cached(missing) is None
