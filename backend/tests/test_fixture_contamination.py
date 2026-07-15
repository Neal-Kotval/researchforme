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


# --------------------------------------------------------------------------- #
# Fixture-GAP firewall: a fixture synthesis on a real run yields no children   #
# (audit finding #1 — canned fintech gaps must never enter a real tree).       #
# --------------------------------------------------------------------------- #
def test_real_run_drops_fixture_synthesized_gaps(monkeypatch):
    import asyncio as _aio
    from types import SimpleNamespace as _NS
    from app.autonomous import engine
    from app.autonomous.schemas import Node, NodeKind, Project

    async def _fake_synth(*_a, **_k):
        # Simulate the LLM fallback landing on fixture mid-run.
        return ([_NS(title="Personal finance for freelancers")], "fixture", [])

    monkeypatch.setattr(engine, "synthesize", _fake_synth)
    monkeypatch.setattr(engine, "_fetch_all",
                        lambda *_a, **_k: _aio.sleep(0, result=({}, [])))
    monkeypatch.setattr(engine, "extract_signals", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "scope_area",
                        lambda *_a, **_k: _NS(keywords=["k"], sub_segments=[]))
    monkeypatch.setattr(engine, "steering_context_block", lambda _p: "")
    monkeypatch.setattr(engine, "get_client", lambda: _NS(backend="agent-sdk"))

    node = Node(id="n", project_id="p", kind=NodeKind.SEGMENT, title="protein models")
    project = Project(id="p", domain="protein models")
    children = _aio.run(engine.expand_segment(node, project, _NS(backend="agent-sdk"), "m"))
    assert children == [], "fixture gaps must not enter a real run's tree"


def test_fixture_run_keeps_fixture_synthesized_gaps(monkeypatch):
    import asyncio as _aio
    from types import SimpleNamespace as _NS
    from app.autonomous import engine
    from app.autonomous.schemas import Node, NodeKind, Project

    async def _fake_synth(*_a, **_k):
        return ([_NS(title="Demo gap", tags=[])], "fixture", [])

    monkeypatch.setattr(engine, "synthesize", _fake_synth)
    monkeypatch.setattr(engine, "_fetch_all",
                        lambda *_a, **_k: _aio.sleep(0, result=({}, [])))
    monkeypatch.setattr(engine, "extract_signals", lambda *_a, **_k: None)
    monkeypatch.setattr(engine, "scope_area",
                        lambda *_a, **_k: _NS(keywords=["k"], sub_segments=[]))
    monkeypatch.setattr(engine, "steering_context_block", lambda _p: "")
    monkeypatch.setattr(engine, "get_client", lambda: _NS(backend="fixture"))
    monkeypatch.setattr(engine, "_evidence_context", lambda *_a, **_k: "")
    monkeypatch.setattr(engine, "node_priority", lambda *_a, **_k: 1.0)

    node = Node(id="n", project_id="p", kind=NodeKind.SEGMENT, title="anything")
    project = Project(id="p", domain="anything")
    children = _aio.run(engine.expand_segment(node, project, _NS(backend="fixture"), "m"))
    assert len(children) == 1, "a deliberate fixture run keeps demo gaps"
