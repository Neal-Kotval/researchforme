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


# --------------------------------------------------------------------------- #
# SPEC §5 corroboration seam — the pressure-test red team must be able to pull #
# fresh evidence from live sources at standard/deep rigor (and stay tool-free  #
# at light rigor). Regression for the audit's #1 HIGH finding: the tools were  #
# plumbed into pressure_test but never wired from the only caller.             #
# --------------------------------------------------------------------------- #
def _tiny_gap(sub_segment: str = "battery sensors"):
    from app.schemas import Gap, Scores

    return Gap(
        title="OTA model-diffing for MCU firmware",
        thesis="Ship model deltas to microcontrollers over the air.",
        scores=Scores(
            demand_strength=4,
            competitive_openness=4,
            trend_tailwind=4,
            feasibility=3,
            willingness_to_pay=3,
        ),
        wedge="Start with the loudest TinyML forum's #1 pain.",
        riskiest_assumption="Teams will trust OTA to safety-critical MCUs.",
        weakest_link="Bandwidth on constrained links.",
        sub_segment=sub_segment,
    )


def test_pressure_corroboration_tools_gated_by_rigor():
    """standard/deep rigor arms the five live search_* tools; light stays tool-free."""
    from types import SimpleNamespace

    from app.autonomous.engine import make_node
    from app.autonomous.schemas import NodeKind
    from app.autonomous.service import corroboration_tools_for

    segment = make_node(
        "proj", None, NodeKind.SEGMENT, "on-device inference runtimes", keywords=["a"]
    )
    project = SimpleNamespace(sub_segments=["TinyML"])

    # Governor curbing rigor -> no tools (cheap, tool-free per SPEC §5).
    assert corroboration_tools_for(segment, "battery sensors", project, "light") is None

    # Ample headroom -> real corroboration tools, one per source, scoped to the
    # segment title + the gap/project sub-segments.
    for rigor in ("standard", "deep"):
        tools = corroboration_tools_for(segment, "battery sensors", project, rigor)
        assert tools is not None, f"{rigor} rigor must arm corroboration tools"
        assert {t.name for t in tools} == {
            "search_reddit",
            "search_arxiv",
            "search_hackernews",
            "search_github",
            "search_newsletters",
        }


@pytest.mark.asyncio
async def test_pressure_test_forwards_tools_to_client():
    """pressure_test hands the corroboration tools straight to the LLM call."""
    from app.autonomous.pressure import pressure_test

    captured: dict = {}

    class _FakeResult:
        text = '[{"lens":"demand_mirage","verdict":"survives","argument":"real WTP"}]'

    class _FakeClient:
        async def complete(self, prompt, **kwargs):  # noqa: ANN001
            captured["tools"] = kwargs.get("tools")
            return _FakeResult()

    sentinel = ["<the five search_* tools>"]
    test = await pressure_test(
        _tiny_gap(), "ctx", _FakeClient(), "claude-opus-4-8", "light", tools=sentinel
    )
    assert captured["tools"] is sentinel, "tools must reach client.complete unchanged"
    assert test.test_rigor == "light"


# --------------------------------------------------------------------------- #
# SPEC §6.1 — rate-limit signalling. Regression for audit HIGH #2: the client  #
# now fires an observer hook on a 429-shaped error and the governor registers   #
# its note_rate_limit there, so the "authoritative live signal" is actually     #
# wired (was dead code with zero callers).                                      #
# --------------------------------------------------------------------------- #
def test_looks_rate_limited_detects_429_shapes():
    from app.llm.client import _looks_rate_limited

    class _Status429(Exception):
        status_code = 429

    assert _looks_rate_limited(_Status429("boom"))
    assert _looks_rate_limited(Exception("Error: rate limit exceeded"))
    assert _looks_rate_limited(Exception("overloaded_error from upstream"))
    assert _looks_rate_limited(Exception("HTTP 429 Too Many Requests"))
    assert not _looks_rate_limited(Exception("could not parse json array"))


def test_rate_limit_notification_drives_governor_backoff():
    from app.autonomous.governor import UsageGovernor
    from app.autonomous.schemas import Budget
    from app.llm import client as client_mod

    gov = UsageGovernor()
    client_mod.register_rate_limit_listener(gov.note_rate_limit)
    assert gov.headroom(Budget(), 0) == "ample", "fresh governor has ample headroom"

    class _Boom(Exception):
        status_code = 429

    client_mod._notify_rate_limit(_Boom("429 too many requests"))
    # A live 429 must push the governor into backoff -> no headroom this instant.
    assert gov.headroom(Budget(), 0) == "none"


# --------------------------------------------------------------------------- #
# SPEC §6 — the shared daily cap is a ROLLING 24h window, not a process-        #
# lifetime total. Regression for audit HIGH #3.                                 #
# --------------------------------------------------------------------------- #
def test_daily_cap_uses_rolling_window_not_lifetime():
    import time

    from app.autonomous.governor import UsageGovernor
    from app.autonomous.schemas import Budget

    gov = UsageGovernor()
    budget = Budget(daily_cap_tokens=1000)  # default pace: balanced

    # Simulate 900 tokens spent >24h ago (a stale hourly bucket) + lifetime total.
    stale = time.time() - 90_000.0
    gov._daily.append([stale - (stale % 3600.0), 900.0])
    gov._spent_total = 900

    # Rolling daily spend prunes the stale bucket -> effectively zero today.
    assert gov._daily_spent(time.time()) == 0
    assert gov.headroom(budget, 0) == "ample", "yesterday's spend must not count today"

    # Spend 950 now: today's rolling total is 950/1000 -> headroom tightens.
    gov.record_usage(950)
    assert gov._daily_spent(time.time()) == 950
    assert gov.headroom(budget, 0) in {"tight", "none"}


def test_governor_snapshot_reports_measured_numbers():
    """The global usage snapshot exposes real spend/rate/mode for the usage bar."""
    from app.autonomous.governor import UsageGovernor

    gov = UsageGovernor()
    snap = gov.snapshot()
    assert snap["spent_total"] == 0 and snap["rate_per_min"] == 0
    assert snap["mode"] in {"sprinting", "curbing", "paused"}
    assert snap["max_concurrency"] >= 1
    assert snap["in_backoff"] is False

    gov.record_usage(1500)
    snap = gov.snapshot()
    assert snap["spent_total"] == 1500
    assert snap["daily_spent"] == 1500
    assert snap["rate_per_min"] == 1500  # all within the trailing 60s window


@pytest.mark.asyncio
async def test_self_critique_degrades_and_guards_format():
    """The adversarial self-critique (SPEC feature C) never raises and rejects non-prose."""
    from app.autonomous.pressure import adversarial_self_critique
    from app.autonomous.schemas import PressureTest

    gap = _tiny_gap()
    test = PressureTest(test_rigor="deep", summary="SURVIVED: 5/5 lenses survived.")

    class _Fail:
        async def complete(self, *a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("llm down")

    assert await adversarial_self_critique(gap, 80, test, _Fail(), "m") == ""

    class _JsonDump:
        async def complete(self, *a, **k):  # noqa: ANN002, ANN003
            class R:
                text = '{"reason":"model ignored the format"}'
            return R()

    assert await adversarial_self_critique(gap, 80, test, _JsonDump(), "m") == ""

    class _Good:
        async def complete(self, *a, **k):  # noqa: ANN002, ANN003
            class R:
                text = "The enabling shift predates 2026, so 80 overstates the why-now."
            return R()

    out = await adversarial_self_critique(gap, 80, test, _Good(), "m")
    assert "80 overstates" in out


def test_usage_policy_shapes_headroom_around_limit():
    """A dynamic percent usage limit auto-curbs then pauses as spend nears cap×pct."""
    from app.autonomous.governor import UsageGovernor
    from app.autonomous.schemas import Budget

    gov = UsageGovernor()
    # 100k/day cap, aim to stay under 90% of it -> effective ceiling 90k.
    gov.set_policy(daily_cap_tokens=100_000, limit_pct=0.90)
    b = Budget()  # no per-project caps; the global policy does the shaping

    assert gov.headroom(b, 0) == "ample"
    snap = gov.snapshot()
    assert snap["effective_cap"] == 90_000 and snap["limit_pct"] == 0.90

    gov.record_usage(80_000)  # 80k of 90k effective -> ~11% left -> curbing
    assert gov.headroom(b, 0) == "tight"
    lvl = gov.snapshot()["usage_level"]
    assert lvl in {"high", "heavy"}

    gov.record_usage(15_000)  # 95k > 90k effective -> no headroom -> pause
    assert gov.headroom(b, 0) == "none"
    assert gov.snapshot()["usage_level"] == "heavy"


def test_usage_level_bands_without_cap_use_rate():
    from app.autonomous.governor import _usage_level

    assert _usage_level(None, 0) == "low"
    assert _usage_level(None, 5_000) == "medium"
    assert _usage_level(None, 12_000) == "high"
    assert _usage_level(None, 50_000) == "heavy"
    assert _usage_level(0.5, 0) == "medium"  # ratio wins when a cap is set
