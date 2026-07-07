"""End-to-end test for Autonomous Exploration Mode with the fixture backend.

Runs a whole project's frontier loop (SPEC §4) to completion with
``LLM_BACKEND=fixture``, no source credentials, and a private temp store — so the
explorer degrades all the way down: mock sources, fixture synthesis, and a
neutral pressure-test fallback when the LLM output can't be parsed. Even with
*zero* real LLM or network, the loop must still build a scored tree.

It verifies the contract the rest of the stack relies on:

* a single DOMAIN root exists;
* the tree contains pressure-tested GAP nodes, each with an integer viability in
  ``0..100`` and a :class:`PressureTest`;
* the run stopped for an explicit, recorded reason (``stats.stop_reason``);
* the per-project event log is non-empty and its ``seq`` is strictly monotonic.

Plus a pure-code invariant from the frontier heuristic (SPEC §4.1): pinning a
node raises its priority and floats it to the front of the frontier.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture()
def fixture_env(tmp_path, monkeypatch):
    """Force the fixture LLM backend + a hermetic temp cache + mock sources.

    Mirrors ``test_pipeline``'s fixture: strip every credential so all sources
    fall back to their mock path, pin the fixture backend, and reset the
    foundation's memoized settings / lazy singletons so our env wins.
    """
    monkeypatch.setenv("LLM_BACKEND", "fixture")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "test_cache.db"))
    for var in (
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "GITHUB_TOKEN",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

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


@pytest.mark.asyncio
async def test_autonomous_run_builds_scored_tree(fixture_env, tmp_path):
    from app.autonomous.engine import make_node, node_priority
    from app.autonomous.governor import UsageGovernor
    from app.autonomous.schemas import (
        Budget,
        CreateProjectRequest,
        NodeKind,
        PressureTest,
        ProjectStatus,
    )
    from app.autonomous.service import ExplorerService
    from app.autonomous.store import TreeStore

    # A private store + governor keeps this fully hermetic (no shared singletons).
    store = TreeStore(path=str(tmp_path / "autonomous.db"))
    governor = UsageGovernor()
    service = ExplorerService(store, governor)

    # max_nodes is sized to reach the gap layer (domain → sub-areas → segments →
    # gaps) while staying tiny/fast. No token/time/milestone caps so the run
    # terminates on max_nodes or a genuine EXHAUSTED, never a pause.
    req = CreateProjectRequest(
        domain="bookkeeping tools for freelancers",
        sub_segments=[],
        budget=Budget(max_nodes=25, milestone_tokens=0),
        autostart=False,
    )
    project = service.create(req)
    pid = project.id

    # Drive the frontier loop directly. Flip to RUNNING first so the loop works
    # rather than idling on a paused status. A timeout turns any hang into a
    # fast, legible failure instead of a stuck suite.
    project.status = ProjectStatus.RUNNING
    store.save_project(project)
    await asyncio.wait_for(service._run(pid), timeout=60)

    nodes = store.get_nodes(pid)
    assert nodes, "the run produced no nodes"

    # --- exactly one DOMAIN root, and it is the actual root -------------------
    roots = [n for n in nodes if n.kind == NodeKind.DOMAIN]
    assert len(roots) == 1, f"expected one DOMAIN root, got {len(roots)}"
    assert roots[0].parent_id is None

    # --- pressure-tested, scored GAP nodes -----------------------------------
    scored = [n for n in nodes if n.viability is not None]
    assert scored, "expected at least one scored gap node"
    gaps = [n for n in nodes if n.kind == NodeKind.GAP]
    assert gaps, "expected candidates to be marked GAP after scoring"
    for n in scored:
        assert isinstance(n.viability, int) and not isinstance(n.viability, bool)
        assert 0 <= n.viability <= 100, f"viability {n.viability} out of 0..100"
        assert isinstance(n.pressure_test, PressureTest)
        assert n.gap is not None

    # --- the run stopped for an explicit, recorded reason --------------------
    final = store.get_project(pid)
    assert final is not None
    assert final.stats.stop_reason, "stats.stop_reason was never set"
    assert final.status in {
        ProjectStatus.EXHAUSTED,
        ProjectStatus.BUDGET_SPENT,
        ProjectStatus.TIME_LIMIT,
    }, f"unexpected terminal status {final.status}"

    # --- the event log is non-empty and strictly monotonic -------------------
    events = store.events_since(pid, 0)
    assert events, "the event log is empty"
    seqs = [e.seq for e in events]
    assert seqs[0] >= 1
    assert all(b > a for a, b in zip(seqs, seqs[1:])), f"seq not monotonic: {seqs}"
    assert all(e.project_id == pid for e in events)

    # --- pinning raises frontier priority (SPEC §4.1, pure heuristic) ---------
    node = make_node(pid, None, NodeKind.SEGMENT, "a segment", keywords=["a", "b"])
    base = node_priority(node, None)
    node.pinned = True
    boosted = node_priority(node, None)
    assert boosted > base, "pinning must raise a node's priority"


def test_pinned_node_pops_first_from_frontier(fixture_env):
    """A pinned node jumps an otherwise-identical unpinned peer in the frontier."""
    from app.autonomous.engine import Frontier, make_node, node_priority
    from app.autonomous.schemas import NodeKind

    plain = make_node("proj", None, NodeKind.SEGMENT, "alpha", keywords=["x"])
    pinned = make_node("proj", None, NodeKind.SEGMENT, "beta", keywords=["x"])
    plain.priority = node_priority(plain, None)
    pinned.pinned = True
    pinned.priority = node_priority(pinned, None)

    frontier = Frontier()
    frontier.push(plain)
    frontier.push(pinned)

    assert pinned.priority > plain.priority
    assert frontier.pop().id == pinned.id  # highest priority pops first
