"""A degraded (fixture-serving) source must not contaminate a live run's corpus.

Reddit 403s in practice and falls back to an unrelated fixture corpus. Feeding
those items into, say, a protein-model exploration let the red team reason over
bookkeeping-SaaS content as if it were market evidence and drive demand to a
*false* zero. Fixtures are dropped whenever any source is live; when nothing is
live (offline/test), they are kept — a fixture run is then the explicit intent.
"""
import asyncio
from types import SimpleNamespace

import pytest

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


def _run(sources, monkeypatch):
    monkeypatch.setattr(engine, "get_sources", lambda: sources)
    scope = SimpleNamespace(keywords=["k"], sub_segments=[])
    return asyncio.run(engine._fetch_all("node", scope))


def test_mock_items_excluded_when_a_source_is_live(monkeypatch):
    sources = [
        _Src(SourceName.ARXIV, SourceStatus.LIVE),
        _Src(SourceName.REDDIT, SourceStatus.MOCK),
    ]
    fetched, reports = _run(sources, monkeypatch)

    assert SourceName.ARXIV in fetched
    # The fixture corpus must not reach the synthesizer...
    assert SourceName.REDDIT not in fetched
    # ...but the run must still know the source degraded (confidence cap).
    assert {r.status for r in reports} == {SourceStatus.LIVE, SourceStatus.MOCK}


def test_mock_items_kept_when_nothing_is_live(monkeypatch):
    sources = [
        _Src(SourceName.ARXIV, SourceStatus.MOCK),
        _Src(SourceName.REDDIT, SourceStatus.MOCK),
    ]
    fetched, _ = _run(sources, monkeypatch)
    assert SourceName.ARXIV in fetched and SourceName.REDDIT in fetched


def test_empty_source_does_not_count_as_live(monkeypatch):
    # An EMPTY source is not evidence; it must not license dropping fixtures.
    sources = [
        _Src(SourceName.HACKERNEWS, SourceStatus.EMPTY),
        _Src(SourceName.REDDIT, SourceStatus.MOCK),
    ]
    fetched, _ = _run(sources, monkeypatch)
    assert SourceName.REDDIT in fetched
