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
    tools, sink = corroboration_tools_for(segment, "battery sensors", project, "light")
    assert tools is None
    assert sink == {}

    # Ample headroom -> real corroboration tools, one per source, scoped to the
    # segment title + the gap/project sub-segments, sharing a url_sink so the
    # pressure test can stamp true per-source provenance on tool-fetched evidence.
    for rigor in ("standard", "deep"):
        tools, sink = corroboration_tools_for(segment, "battery sensors", project, rigor)
        assert tools is not None, f"{rigor} rigor must arm corroboration tools"
        assert sink == {}
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


@pytest.mark.asyncio
async def test_pressure_tool_fetched_evidence_carries_mock_provenance(monkeypatch):
    """Corroboration evidence fetched from a MOCK-mode source must be live=False.

    Regression for the benchmark finding: tool-fetched evidence was stamped
    live unconditionally, so fixture URLs (Reddit mock, arXiv degraded) showed
    up in lens evidence marked live. The fetch-time SourceStatus now threads
    through the shared url_sink into the parsed verdicts.
    """
    import json as _json

    from types import SimpleNamespace

    from app.autonomous.engine import make_node
    from app.autonomous.pressure import pressure_test
    from app.autonomous.schemas import NodeKind
    from app.autonomous.service import corroboration_tools_for
    from app.schemas import RawItem, SourceName, SourceReport, SourceStatus
    from app.sources import registry
    from app.sources.base import FetchResult

    mock_url = "https://www.reddit.com/r/x/comments/9/mock_hit/"
    live_url = "https://news.ycombinator.com/item?id=41000001"

    class FakeSource:
        def __init__(self, name, url, status):
            self._name, self._url, self._status = name, url, status

        def fetch(self, area, keywords, sub_segments):  # noqa: ANN001
            return FetchResult(
                items=[RawItem(source=self._name, id="t1", title="hit",
                               url=self._url, weight=1.0)],
                report=SourceReport(name=self._name, status=self._status),
            )

    fakes = {
        SourceName.REDDIT: FakeSource(SourceName.REDDIT, mock_url, SourceStatus.MOCK),
        SourceName.HACKERNEWS: FakeSource(
            SourceName.HACKERNEWS, live_url, SourceStatus.LIVE
        ),
    }
    monkeypatch.setattr(registry, "get_source", lambda name: fakes.get(name))

    segment = make_node("proj", None, NodeKind.SEGMENT, "tiny inference", keywords=["a"])
    project = SimpleNamespace(sub_segments=[])
    tools, sink = corroboration_tools_for(segment, "battery sensors", project, "deep")
    assert tools is not None

    verdicts = [
        {
            "lens": "demand_mirage",
            "verdict": "kills",
            "argument": "mock corroboration says the pain is canned",
            "evidence": [
                {"source": "reddit", "url": mock_url, "quote": "canned pain"}
            ],
        },
        {
            "lens": "why_now_fragility",
            "verdict": "survives",
            "argument": "the shift is real",
            "evidence": [
                {"source": "hackernews", "url": live_url, "quote": "real shift"},
                {"source": "newsletter", "url": "https://n.example/made-up",
                 "quote": "never fetched"},
            ],
        },
    ]

    class _FakeClient:
        async def complete(self, prompt, **kwargs):  # noqa: ANN001
            # Simulate the red team corroborating via its tools mid-call.
            by_name = {t.name: t for t in kwargs["tools"]}
            await by_name["search_reddit"].handler({"query": "pain"})
            await by_name["search_hackernews"].handler({"query": "shift"})
            return SimpleNamespace(text=_json.dumps(verdicts))

    test = await pressure_test(
        _tiny_gap(), "ctx", _FakeClient(), "claude-opus-4-8", "deep",
        tools=tools, url_sink=sink,
    )
    by_lens = {lv.lens: lv for lv in test.lenses}

    # Fetched from a MOCK-mode source -> the true provenance is live=False.
    assert by_lens["demand_mirage"].evidence[0].live is False
    # Fetched from a LIVE source -> live=True survives the round trip.
    assert by_lens["why_now_fragility"].evidence[0].live is True
    # A URL never seen in the gap or any tool fetch can't launder live credit.
    assert by_lens["why_now_fragility"].evidence[1].live is False


# --------------------------------------------------------------------------- #
# Pressure-test integrity — a model that engages only some lenses must not     #
# earn free "survives" verdicts from the gap's own self-reported scores, nor   #
# keep the requested rigor's confidence credit. Regression for the audit's     #
# self-reference-loop finding.                                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_pressure_partial_fill_weakens_and_downgrades_rigor():
    """Skipped lenses fill as 'weakens', and recorded rigor reflects the
    actually-evaluated lens count (1 of 6 at deep -> light)."""
    from app.autonomous.pressure import pressure_test

    class _FakeResult:
        text = '[{"lens":"demand_mirage","verdict":"survives","argument":"real WTP"}]'

    class _FakeClient:
        async def complete(self, prompt, **kwargs):  # noqa: ANN001
            return _FakeResult()

    test = await pressure_test(
        _tiny_gap(), "ctx", _FakeClient(), "claude-opus-4-8", "deep"
    )
    assert len(test.lenses) == 6, "deep rigor still records all six lenses"
    assert test.survived == 1 and test.weakened == 5 and test.killed == 0
    filled = [lv for lv in test.lenses if lv.lens != "demand_mirage"]
    for lv in filled:
        assert lv.verdict == "weakens"
        assert "not evaluated" in lv.argument
    assert test.test_rigor == "light", "1 evaluated lens must not keep deep credit"


@pytest.mark.asyncio
async def test_pressure_partial_fill_keeps_earned_rigor():
    """Evaluating 4 of 6 deep lenses earns standard rigor, not deep or light."""
    import json as _json

    from app.autonomous.pressure import pressure_test

    verdicts = [
        {"lens": k, "verdict": "survives", "argument": "holds"}
        for k in ("demand_mirage", "just_a_feature", "empty_for_a_reason", "moat")
    ]

    class _FakeResult:
        text = _json.dumps(verdicts)

    class _FakeClient:
        async def complete(self, prompt, **kwargs):  # noqa: ANN001
            return _FakeResult()

    test = await pressure_test(
        _tiny_gap(), "ctx", _FakeClient(), "claude-opus-4-8", "deep"
    )
    assert test.survived == 4 and test.weakened == 2
    assert test.test_rigor == "standard"


def _grounded_gap():
    from app.schemas import Evidence, SourceName

    gap = _tiny_gap()
    gap.evidence = [
        Evidence(source=SourceName.REDDIT, url=f"https://r.example/{i}", quote="q")
        for i in range(3)
    ]
    return gap


def _lens(key: str, verdict: str, n_evidence: int = 0, live: bool = True):
    from app.autonomous.schemas import LensVerdict
    from app.schemas import Evidence, SourceName

    return LensVerdict(
        lens=key,
        verdict=verdict,  # type: ignore[arg-type]
        argument="case made",
        evidence=[
            Evidence(source=SourceName.HACKERNEWS, url=f"https://hn.example/{key}/{i}",
                     quote="q", live=live)
            for i in range(n_evidence)
        ],
    )


def _test_with(lenses, rigor="deep"):
    from app.autonomous.pressure import _assemble

    return _assemble(lenses, rigor)


def test_high_confidence_requires_the_full_bar():
    """'high' is earned, not near-free: standard/deep rigor actually earned,
    >=2 LIVE corroboration items, zero kills, and a majority of lenses that
    survive or weaken *with evidence*. Anything less is medium at most."""
    from app.autonomous.pressure import _confidence_for

    gap = _grounded_gap()

    # The full bar: deep rigor, 2 live corroborations, no kills, 4/6 survive.
    battle_tested = _test_with([
        _lens("demand_mirage", "survives", n_evidence=1),
        _lens("just_a_feature", "survives", n_evidence=1),
        _lens("empty_for_a_reason", "survives"),
        _lens("why_now_fragility", "survives"),
        _lens("incumbent_countermove", "weakens"),
        _lens("moat", "weakens"),
    ])
    corroboration = sum(len(lv.evidence) for lv in battle_tested.lenses)
    assert _confidence_for(gap, battle_tested, corroboration) == "high"

    # Only ONE live corroboration item -> medium, however well it survived.
    thin_corroboration = _test_with([
        _lens("demand_mirage", "survives", n_evidence=1),
        _lens("just_a_feature", "survives"),
        _lens("empty_for_a_reason", "survives"),
        _lens("why_now_fragility", "survives"),
        _lens("incumbent_countermove", "survives"),
        _lens("moat", "survives"),
    ])
    assert _confidence_for(gap, thin_corroboration, corroboration=1) == "medium"

    # Any kill -> not high, no matter the corroboration.
    with_kill = _test_with([
        _lens("demand_mirage", "kills", n_evidence=2),
        _lens("just_a_feature", "survives", n_evidence=1),
        _lens("empty_for_a_reason", "survives"),
        _lens("why_now_fragility", "survives"),
        _lens("incumbent_countermove", "survives"),
        _lens("moat", "survives"),
    ])
    assert _confidence_for(gap, with_kill, corroboration=3) != "high"

    # Majority evidence-free weakens (e.g. skipped-lens fills) -> not high.
    mostly_weakened = _test_with([
        _lens("demand_mirage", "survives", n_evidence=2),
        _lens("just_a_feature", "weakens"),
        _lens("empty_for_a_reason", "weakens"),
        _lens("why_now_fragility", "weakens"),
        _lens("incumbent_countermove", "weakens"),
        _lens("moat", "weakens"),
    ])
    assert _confidence_for(gap, mostly_weakened, corroboration=2) != "high"

    # Rigor downgraded to light (the model barely engaged) -> not high.
    light = _test_with(
        [_lens("demand_mirage", "survives", n_evidence=2)], rigor="light"
    )
    assert _confidence_for(gap, light, corroboration=2) != "high"

    # A weaken that comes WITH evidence counts toward the supported majority.
    weakens_with_evidence = _test_with([
        _lens("demand_mirage", "survives", n_evidence=1),
        _lens("just_a_feature", "weakens", n_evidence=1),
        _lens("empty_for_a_reason", "weakens", n_evidence=1),
        _lens("why_now_fragility", "survives"),
        _lens("incumbent_countermove", "weakens"),
        _lens("moat", "weakens"),
    ])
    assert _confidence_for(gap, weakens_with_evidence, corroboration=3) == "high"


def test_mock_only_corroboration_cannot_reach_high():
    """Fixture/mock-sourced corroboration (live=False after the provenance fix)
    keeps confidence at medium: only LIVE corroboration unlocks 'high'."""
    from app.autonomous.pressure import _confidence_for

    gap = _grounded_gap()
    mock_corroborated = _test_with([
        _lens("demand_mirage", "survives", n_evidence=2, live=False),
        _lens("just_a_feature", "survives", n_evidence=1, live=False),
        _lens("empty_for_a_reason", "survives"),
        _lens("why_now_fragility", "survives"),
        _lens("incumbent_countermove", "survives"),
        _lens("moat", "survives"),
    ])
    corroboration = sum(len(lv.evidence) for lv in mock_corroborated.lenses)
    assert corroboration == 3
    assert _confidence_for(gap, mock_corroborated, corroboration) == "medium"


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


@pytest.mark.asyncio
async def test_intake_degrades_to_static_questions():
    """Intake never raises: a failing LLM falls back to a solid static question set."""
    from app.autonomous.intake import generate_intake_questions, intake_context_block

    class _Fail:
        async def complete(self, *a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("no llm")

    qs = await generate_intake_questions("bookkeeping for freelancers", _Fail(), "m")
    assert 3 <= len(qs) <= 5
    assert all(q.question and q.suggestions for q in qs)

    # Context block renders answers, and is empty when there are none.
    block = intake_context_block({"Who for?": "SMB", "Win?": ""})
    assert "Who for? → SMB" in block and "Win?" not in block
    assert intake_context_block({}) == ""


# --------------------------------------------------------------------------- #
# Founder fit (Phase 1) — an orthogonal 0..100 "is this space for YOU" score   #
# from the steering context. Null means "no steering or scoring unavailable"   #
# — a fit is NEVER fabricated.                                                 #
# --------------------------------------------------------------------------- #
def _steered_project():
    from types import SimpleNamespace

    from app.autonomous.schemas import SteeringContext

    return SimpleNamespace(
        steering=SteeringContext(
            brief="Ex-firmware engineer, 10y embedded, small audience in TinyML.",
            advantages=["deep MCU/firmware expertise"],
            constraints=["solo founder", "no capital for hardware"],
        ),
        intake={},
    )


@pytest.mark.asyncio
async def test_founder_fit_scored_when_steering_present():
    """A parseable fit object lands as (int 0..100, reason)."""
    from app.autonomous.fit import score_founder_fit

    captured: dict = {}

    class _Client:
        async def complete(self, prompt, **kwargs):  # noqa: ANN001
            captured["prompt"] = prompt
            captured["system"] = kwargs.get("system")

            class R:
                text = '{"fit": 72, "fit_reason": "Squarely on the firmware expertise advantage."}'
            return R()

    fit, reason = await score_founder_fit(
        _tiny_gap(), 80, _steered_project(), _Client(), "claude-haiku-4-5-20251001"
    )
    assert fit == 72
    assert "firmware expertise" in reason
    # The call is grounded in the steering block and instructs founder-not-market.
    assert "FOUNDER STEERING" in captured["prompt"]
    assert "deep MCU/firmware expertise" in captured["prompt"]
    assert "not the market" in captured["system"]


@pytest.mark.asyncio
async def test_founder_fit_none_when_steering_empty():
    """Empty steering → (None, "") without spending an LLM call."""
    from types import SimpleNamespace

    from app.autonomous.fit import score_founder_fit
    from app.autonomous.schemas import SteeringContext

    class _MustNotBeCalled:
        async def complete(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("no LLM call may be made without steering")

    bare = SimpleNamespace(steering=SteeringContext(), intake={})
    assert await score_founder_fit(_tiny_gap(), 80, bare, _MustNotBeCalled(), "m") == (None, "")


@pytest.mark.asyncio
async def test_founder_fit_degrades_to_none_never_fabricates():
    """LLM failure or unparseable output → (None, ""), never a made-up score."""
    from app.autonomous.fit import score_founder_fit

    class _Fail:
        async def complete(self, *a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("llm down")

    class _Garbage:
        async def complete(self, *a, **k):  # noqa: ANN002, ANN003
            class R:
                text = "I think it fits pretty well, maybe a 70?"
            return R()

    project = _steered_project()
    assert await score_founder_fit(_tiny_gap(), 80, project, _Fail(), "m") == (None, "")
    assert await score_founder_fit(_tiny_gap(), 80, project, _Garbage(), "m") == (None, "")


def test_founder_fit_parse_clamps_and_strips_fences():
    from app.autonomous.fit import _parse_fit

    assert _parse_fit('```json\n{"fit": 150, "fit_reason": "over"}\n```') == (100, "over")
    assert _parse_fit('noise {"fit": -3, "fit_reason": "under"} noise') == (0, "under")
    assert _parse_fit('{"fit": true, "fit_reason": "bool is not a score"}') == (None, "")
    assert _parse_fit('{"fit_reason": "no score at all"}') == (None, "")
    assert _parse_fit("") == (None, "")


def test_node_fit_round_trips_through_json():
    """The Node schema carries fit/fit_reason through a serialize→parse cycle."""
    from app.autonomous.schemas import Node, NodeKind

    node = Node(
        id="n1", project_id="p1", kind=NodeKind.GAP, title="g",
        viability=80, fit=55, fit_reason="Right skills, wrong capital profile.",
    )
    again = Node.model_validate_json(node.model_dump_json())
    assert again.fit == 55
    assert again.fit_reason == "Right skills, wrong capital profile."
    # And the default is honest: no steering → no fit.
    bare = Node(id="n2", project_id="p1", kind=NodeKind.GAP, title="g2")
    assert bare.fit is None and bare.fit_reason == ""


@pytest.mark.asyncio
async def test_worker_attaches_fit_to_scored_gaps(fixture_env, tmp_path, monkeypatch):
    """A steered fixture run stamps fit on every scored GAP node (wiring test)."""
    from app.autonomous import service as service_mod
    from app.autonomous.governor import UsageGovernor
    from app.autonomous.schemas import (
        Budget,
        CreateProjectRequest,
        NodeKind,
        ProjectStatus,
        SteeringContext,
    )
    from app.autonomous.service import ExplorerService
    from app.autonomous.store import TreeStore

    async def _fake_fit(gap, viability, project, client, model):  # noqa: ANN001
        return 66, "Leans on the founder's stated advantage."

    monkeypatch.setattr(service_mod, "score_founder_fit", _fake_fit)

    store = TreeStore(path=str(tmp_path / "fit.db"))
    service = ExplorerService(store, UsageGovernor())
    project = service.create(
        CreateProjectRequest(
            domain="bookkeeping tools for freelancers",
            budget=Budget(max_nodes=25, milestone_tokens=0),
            steering=SteeringContext(brief="Ex-accountant turned engineer."),
            autostart=False,
        )
    )
    project.status = ProjectStatus.RUNNING
    store.save_project(project)
    await asyncio.wait_for(service._run(project.id), timeout=60)

    gaps = [n for n in store.get_nodes(project.id) if n.kind == NodeKind.GAP]
    assert gaps, "expected scored GAP nodes"
    for n in gaps:
        assert n.fit == 66
        assert "advantage" in n.fit_reason


# --------------------------------------------------------------------------- #
# Boot reconciliation — a project persisted as RUNNING has no worker after a   #
# server restart; it must not present as live forever. Regression for the      #
# 2026-07-12 audit's "stale Sprinting status" finding.                         #
# --------------------------------------------------------------------------- #
def test_reconcile_on_boot_parks_orphaned_running_projects(fixture_env, tmp_path):
    from app.autonomous.governor import UsageGovernor
    from app.autonomous.schemas import (
        CreateProjectRequest,
        EventType,
        ExplorerMode,
        ProjectStatus,
    )
    from app.autonomous.service import ExplorerService
    from app.autonomous.store import TreeStore

    store = TreeStore(path=str(tmp_path / "reconcile.db"))
    service = ExplorerService(store, UsageGovernor())

    # A parked project (no worker) persisted mid-run as RUNNING/sprinting —
    # exactly what a crashed or restarted server leaves behind.
    project = service.create(CreateProjectRequest(domain="stale test", autostart=False))
    project.status = ProjectStatus.RUNNING
    project.stats.mode = ExplorerMode.SPRINTING
    store.save_project(project)

    # A second service (fresh process) reconciles on boot.
    reborn = ExplorerService(store, UsageGovernor())
    parked = reborn.reconcile_on_boot()

    assert [p.id for p in parked] == [project.id]
    fresh = store.get_project(project.id)
    assert fresh.status is ProjectStatus.PAUSED
    assert fresh.stats.mode is ExplorerMode.PAUSED
    events = store.events_since(project.id, 0)
    assert any(e.type is EventType.LOG and "restart" in e.message for e in events)

    # Idempotent: a healthy store reconciles to nothing.
    assert reborn.reconcile_on_boot() == []
