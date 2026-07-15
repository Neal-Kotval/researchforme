"""A degraded (fixture-serving) source must not contaminate a REAL run's corpus.

Reddit 403s in practice and falls back to an unrelated fintech fixture corpus.
Feeding those items into, say, a protein-model branch let synthesis produce a
fintech "gap" on a bio branch (observed: "quarterly tax autopilot" under a
bio-uncertainty node). The gate is decided by whether this is a REAL run — the
resolved LLM backend is not the fixture backend — NOT by whether this particular
node happened to get a live hit. In a real run, mock source items never enter the
corpus, even for a node whose live sources all came back empty (that node is
honestly UNMEASURED, not fintech). Only a deliberate fixture run keeps fixtures.
"""
import asyncio
from types import SimpleNamespace

from app.autonomous import engine
from app.schemas import SourceName, SourceStatus


def _result(name, status):
    return SimpleNamespace(
        report=SimpleNamespace(name=name, status=status),
        items=[f"{name.value}-item"],
    )


class _Src:
    def __init__(self, name, status):
        self.name = name
        self._res = _result(name, status)

    def fetch(self, *_args, **_kwargs):
        return self._res


def _run(sources, backend, monkeypatch):
    monkeypatch.setattr(engine, "get_sources", lambda: sources)
    monkeypatch.setattr(engine, "get_client",
                        lambda: SimpleNamespace(backend=backend))
    scope = SimpleNamespace(keywords=["k"], sub_segments=[])
    return asyncio.run(engine._fetch_all("node", scope))


def test_real_run_drops_mock_even_when_no_source_is_live(monkeypatch):
    """The bug that shipped fintech gaps into a bio run: every live source empty,
    Reddit falls back to mock — and the mock still leaked in. Now it never does."""
    sources = [
        _Src(SourceName.ARXIV, SourceStatus.EMPTY),
        _Src(SourceName.REDDIT, SourceStatus.MOCK),
    ]
    fetched, reports = _run(sources, "agent-sdk", monkeypatch)
    assert SourceName.REDDIT not in fetched            # fintech fixture excluded
    # ...but the degradation is still reported for the confidence cap.
    assert any(r.status is SourceStatus.MOCK for r in reports)


def test_real_run_drops_mock_when_another_source_is_live(monkeypatch):
    sources = [
        _Src(SourceName.ARXIV, SourceStatus.LIVE),
        _Src(SourceName.REDDIT, SourceStatus.MOCK),
    ]
    fetched, _ = _run(sources, "agent-sdk", monkeypatch)
    assert SourceName.ARXIV in fetched
    assert SourceName.REDDIT not in fetched


def test_fixture_run_keeps_fixtures(monkeypatch):
    """A deliberate offline/test run (LLM_BACKEND=fixture) is a fixture run by
    intent — the fixtures ARE the data, so they stay."""
    sources = [
        _Src(SourceName.ARXIV, SourceStatus.MOCK),
        _Src(SourceName.REDDIT, SourceStatus.MOCK),
    ]
    fetched, _ = _run(sources, "fixture", monkeypatch)
    assert SourceName.ARXIV in fetched and SourceName.REDDIT in fetched


def test_real_run_keeps_live_items(monkeypatch):
    sources = [_Src(SourceName.GITHUB, SourceStatus.LIVE)]
    fetched, _ = _run(sources, "agent-sdk", monkeypatch)
    assert SourceName.GITHUB in fetched
