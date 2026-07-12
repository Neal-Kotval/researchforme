"""Phase 4 H4 (end-of-run digest) + H2 (research pack) — contract tests.

Digest: written on terminal transition by ONE cheap-model call; under the
fixture backend (whose canned output is a gaps array, not a digest object) the
deterministic fallback fires, flagged ``degraded: true``, shaped per contract,
and emitted via ``project_updated``. Nothing in the deterministic path is
invented — every string traces to data already on the nodes.

Research pack: ONE strong-model call, cached on ``Node.research_pack``,
``?refresh=1`` regenerates, and ONLY live evidence is quotable. Honest degrade:
a fixture backend that can't produce a real pack means 503 — NEVER canned
content served as a deliverable.

Everything runs against private temp stores — hermetic, no network.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def fixture_env(tmp_path, monkeypatch):
    """Fixture LLM backend + hermetic temp cache; reset memoized singletons."""
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


def _gap(title: str, riskiest: str = "people will pay", live_quotes: int = 1,
         mock_quotes: int = 0):
    from app.schemas import Evidence, Gap, Scores, SourceName

    evidence = [
        Evidence(source=SourceName.REDDIT, url=f"https://r.example/{i}",
                 quote=f"live pain {i}", live=True)
        for i in range(live_quotes)
    ] + [
        Evidence(source=SourceName.REDDIT, url=f"https://m.example/{i}",
                 quote=f"mock pain {i}", live=False)
        for i in range(mock_quotes)
    ]
    return Gap(
        title=title,
        thesis=f"{title}: underserved and growing",
        scores=Scores(demand_strength=4, competitive_openness=4, trend_tailwind=3,
                      feasibility=4, willingness_to_pay=3),
        evidence=evidence,
        wedge="a narrow wedge",
        riskiest_assumption=riskiest,
        weakest_link="willingness to pay",
    )


def _scored_gap_node(store, pid: str, title: str, viability: int, *,
                     kill_lens: str | None = None, **gap_kwargs):
    from app.autonomous.engine import make_node
    from app.autonomous.schemas import LensVerdict, NodeKind, NodeState, PressureTest

    node = make_node(pid, None, NodeKind.GAP_CANDIDATE, title, keywords=["k"])
    node.kind = NodeKind.GAP
    node.state = NodeState.SCORED
    node.gap = _gap(title, **gap_kwargs)
    node.viability = viability
    lenses = []
    if kill_lens:
        lenses.append(LensVerdict(lens=kill_lens, verdict="kills", argument="dead"))
    node.pressure_test = PressureTest(lenses=lenses, killed=len(lenses))
    store.upsert_node(node)
    return node


def _svc(tmp_path, name: str):
    from app.autonomous.governor import UsageGovernor
    from app.autonomous.schemas import Budget, CreateProjectRequest
    from app.autonomous.service import ExplorerService
    from app.autonomous.store import TreeStore

    store = TreeStore(path=str(tmp_path / f"{name}.db"))
    service = ExplorerService(store, UsageGovernor())
    project = service.create(
        CreateProjectRequest(
            domain="bookkeeping tools for freelancers",
            sub_segments=[],
            budget=Budget(max_nodes=10),
            autostart=False,
        )
    )
    return service, store, project


# --------------------------------------------------------------------------- #
# H4 — end-of-run digest                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_digest_written_on_terminal_transition(fixture_env, tmp_path):
    """Terminal transition under fixture → degraded deterministic digest,
    contract-shaped, persisted on the project, and emitted via project_updated."""
    from app.autonomous.schemas import EventType, ProjectStatus

    service, store, project = _svc(tmp_path, "digest")
    _scored_gap_node(store, project.id, "quarterly tax autopilot", 82,
                     kill_lens="crowded", riskiest="freelancers trust automation")
    _scored_gap_node(store, project.id, "invoice chasing bot", 55,
                     kill_lens="crowded")
    _scored_gap_node(store, project.id, "expense OCR for sole traders", 30,
                     kill_lens="empty_for_a_reason")

    await service._finish(project.id, ("exhausted", "Frontier exhausted."))

    final = store.get_project(project.id)
    assert final.status is ProjectStatus.EXHAUSTED
    digest = final.digest
    assert digest is not None
    # Fixture backend returns a gaps array, not a digest object → deterministic
    # fallback, honestly flagged.
    assert digest["degraded"] is True
    # Shape per contract: top_spaces (title+why), kill_pattern, next_questions.
    assert [s["title"] for s in digest["top_spaces"]] == [
        "quarterly tax autopilot", "invoice chasing bot", "expense OCR for sole traders"
    ]
    assert all(s["why"] for s in digest["top_spaces"])
    assert "crowded" in digest["kill_pattern"]  # most-common kill lens (2 kills)
    assert 2 <= len(digest["next_questions"]) <= 3
    # Deterministic questions trace to recorded riskiest assumptions — no invention.
    assert any("freelancers trust automation" in q for q in digest["next_questions"])

    # Emitted via project_updated: the last project event carries the digest.
    updates = [e for e in store.events_since(project.id, 0)
               if e.type is EventType.PROJECT_UPDATED and e.project is not None]
    assert updates and updates[-1].project.digest is not None


@pytest.mark.asyncio
async def test_digest_llm_call_is_steering_aware_and_grounded(fixture_env, tmp_path, monkeypatch):
    """The ONE cheap-model call carries the steering block; a parseable answer
    is accepted only when its titles come from the run's own gaps."""
    import json

    from app.autonomous.digest import build_digest
    from app.autonomous.schemas import SteeringContext

    service, store, project = _svc(tmp_path, "digest_llm")
    project.steering = SteeringContext(brief="I am a solo dev with tax-domain depth")
    store.save_project(project)
    project = store.get_project(project.id)
    _scored_gap_node(store, project.id, "quarterly tax autopilot", 82)

    prompts: list[str] = []

    class _FakeResult:
        text = json.dumps({
            "top_spaces": [
                {"title": "quarterly tax autopilot", "why": "fits the founder"},
                {"title": "a space the run never scored", "why": "hallucinated"},
            ],
            "kill_pattern": "crowding kept killing candidates",
            "next_questions": ["Will freelancers connect their bank?"],
        })
        backend = "test"

    class _FakeClient:
        async def complete(self, prompt, **kwargs):
            prompts.append(prompt)
            return _FakeResult()

    digest = await build_digest(project, store.get_nodes(project.id), _FakeClient())
    assert digest["degraded"] is False
    # Grounding: the invented title is filtered out, the real one kept.
    assert [s["title"] for s in digest["top_spaces"]] == ["quarterly tax autopilot"]
    assert digest["kill_pattern"] == "crowding kept killing candidates"
    # Steering-aware: the founder block rode in the prompt.
    assert "solo dev with tax-domain depth" in prompts[0]


# --------------------------------------------------------------------------- #
# H2 — research pack                                                           #
# --------------------------------------------------------------------------- #
def test_research_pack_503_under_fixture_never_canned(fixture_env, tmp_path, monkeypatch):
    """A fixture backend cannot produce a real pack → honest 503, node untouched."""
    import app.autonomous.service as service_mod
    import app.autonomous.store as store_mod

    service, store, project = _svc(tmp_path, "pack503")
    node = _scored_gap_node(store, project.id, "quarterly tax autopilot", 82)
    monkeypatch.setattr(service_mod, "_service", service, raising=False)
    monkeypatch.setattr(store_mod, "_store", store, raising=False)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.post(f"/api/projects/{project.id}/nodes/{node.id}/research-pack")
        assert r.status_code == 503
        assert "no usable research pack" in r.json()["detail"]
        # NEVER canned: nothing was cached on the node.
        assert store.get_node(node.id).research_pack == ""

        # Unknown node → 404; non-gap node (the DOMAIN root) → 400.
        r = client.post(f"/api/projects/{project.id}/nodes/nope/research-pack")
        assert r.status_code == 404
        root = next(n for n in store.get_nodes(project.id) if n.parent_id is None)
        r = client.post(f"/api/projects/{project.id}/nodes/{root.id}/research-pack")
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_research_pack_caches_and_refreshes(fixture_env, tmp_path):
    """First call generates + persists; second serves the cache with no LLM
    call; ``refresh=True`` regenerates."""
    from app.autonomous.researchpack import SECTION_HEADINGS

    service, store, project = _svc(tmp_path, "packcache")
    node = _scored_gap_node(store, project.id, "quarterly tax autopilot", 82)

    calls = {"n": 0}
    pack_md = "# Research pack\n" + "\n".join(f"{h}\ncontent" for h in SECTION_HEADINGS)

    class _FakeResult:
        text = pack_md
        backend = "test"

    class _FakeClient:
        async def complete(self, prompt, **kwargs):
            calls["n"] += 1
            return _FakeResult()

    service.client = _FakeClient()

    got, cached = await service.research_pack(project.id, node.id)
    assert cached is False and calls["n"] == 1
    assert got.research_pack == pack_md
    assert store.get_node(node.id).research_pack == pack_md  # persisted

    got, cached = await service.research_pack(project.id, node.id)
    assert cached is True and calls["n"] == 1  # served from cache, no new call

    got, cached = await service.research_pack(project.id, node.id, refresh=True)
    assert cached is False and calls["n"] == 2  # refresh bypasses the cache

    # Metered: the strong-model spend landed on the project stats.
    assert store.get_project(project.id).stats.tokens_spent > 0


def test_live_evidence_only_filter_and_prompt(fixture_env, tmp_path):
    """ONLY live evidence is quotable: the filter drops mock items and the
    prompt never carries a mock quote."""
    from app.autonomous.researchpack import build_pack_prompt, live_evidence

    service, store, project = _svc(tmp_path, "packlive")
    node = _scored_gap_node(store, project.id, "quarterly tax autopilot", 82,
                            live_quotes=2, mock_quotes=3)

    live = live_evidence(node.gap)
    assert [e.quote for e in live] == ["live pain 0", "live pain 1"]
    assert all(e.live for e in live)

    prompt = build_pack_prompt(node, store.get_project(project.id))
    assert "live pain 0" in prompt and "live pain 1" in prompt
    assert "mock pain" not in prompt

    # Zero live evidence → the prompt forbids quoting instead of faking it.
    node2 = _scored_gap_node(store, project.id, "invoice chasing bot", 60,
                             live_quotes=0, mock_quotes=2)
    prompt2 = build_pack_prompt(node2, store.get_project(project.id))
    assert "mock pain" not in prompt2
    assert "NO live evidence" in prompt2


def test_looks_like_pack_rejects_fixture_json_and_thin_output():
    """Validation: canned gaps JSON, empty output, and heading-less prose all
    fail; a real 4-section pack passes."""
    from app.autonomous.researchpack import SECTION_HEADINGS, looks_like_pack
    from app.llm.fixture_synthesis import FIXTURE_GAPS_JSON

    assert looks_like_pack(FIXTURE_GAPS_JSON) is False  # the fixture backend's output
    assert looks_like_pack("") is False
    assert looks_like_pack("Here are some thoughts about the market.") is False
    good = "\n".join(f"{h}\nbody" for h in SECTION_HEADINGS)
    assert looks_like_pack(good) is True
