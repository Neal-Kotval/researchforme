"""S3 anti-portfolio graveyard — endpoint + decomposition-prompt injection.

Contract (docs/strategy/phase234-build.md S3 + the S4 consumer side):

* ``GET /api/graveyard?q=&limit=`` — cross-project list of rejected gaps:
  killed (any lens kill OR viability ≤ 40) or ``triage == "passed"``. Pure
  store-level SQL over ``ap_nodes``, no LLM. ``q=`` is a token filter.
  Post-mortem corpus entries are merged in flagged ``external: true``.
* ``graveyard_context_block(store, domain, cap=12)`` — the most-relevant
  rejected gaps rendered as a "SPACES ALREADY REJECTED" block, injected into
  the decomposition prompt beside ``steering_context_block``: present when
  rejected gaps exist, absent when the store is empty.

Everything runs against a private temp :class:`TreeStore` — hermetic, no LLM,
no network.
"""

from __future__ import annotations

import pytest


def _mk_gap_node(
    store,
    project_id: str,
    title: str,
    *,
    viability=None,
    triage=None,
    triage_reason: str = "",
    kill_lens: str | None = None,
    thesis: str = "",
):
    """Persist one GAP node with the given rejection shape."""
    from app.autonomous.engine import make_node
    from app.autonomous.schemas import (
        LensVerdict,
        NodeKind,
        NodeState,
        PressureTest,
    )
    from app.schemas import Gap, Scores

    node = make_node(project_id, None, NodeKind.GAP, title, keywords=["k"])
    node.state = NodeState.SCORED
    node.viability = viability
    node.triage = triage
    node.triage_reason = triage_reason
    if thesis:
        node.gap = Gap(
            title=title,
            thesis=thesis,
            scores=Scores(
                demand_strength=3, competitive_openness=3, trend_tailwind=3,
                feasibility=3, willingness_to_pay=3,
            ),
            wedge="w", riskiest_assumption="r", weakest_link="l",
        )
    if kill_lens:
        node.pressure_test = PressureTest(
            lenses=[
                LensVerdict(lens=kill_lens, verdict="kills", argument="dead"),
                LensVerdict(lens="crowded", verdict="survives", argument="fine"),
            ],
            killed=1,
            survived=1,
        )
    store.upsert_node(node)
    return node


@pytest.fixture()
def seeded(tmp_path):
    """Two projects with a spread of rejected + healthy gap nodes."""
    from app.autonomous.schemas import Project
    from app.autonomous.store import TreeStore

    store = TreeStore(path=str(tmp_path / "graveyard.db"))
    p1 = Project(id="p1", domain="bookkeeping tools for freelancers")
    p2 = Project(id="p2", domain="clinic scheduling software")
    store.create_project(p1)
    store.create_project(p2)

    killed = _mk_gap_node(
        store, "p1", "receipt OCR for sole traders",
        viability=62, kill_lens="empty_for_a_reason",
        thesis="Nobody serves paper-receipt-heavy sole traders.\nSecond line.",
    )
    low = _mk_gap_node(store, "p1", "invoice reminders", viability=30)
    passed = _mk_gap_node(
        store, "p2", "waitlist texting for dental clinics",
        viability=70, triage="passed", triage_reason="too_crowded",
    )
    healthy = _mk_gap_node(store, "p2", "no-show prediction", viability=80)
    return store, {"killed": killed, "low": low, "passed": passed, "healthy": healthy}


# --------------------------------------------------------------------------- #
# Store-level query                                                            #
# --------------------------------------------------------------------------- #
def test_rejected_gaps_criteria_and_cross_project(seeded):
    store, nodes = seeded
    rows = store.rejected_gaps()
    ids = {n.id for n, _domain in rows}
    assert nodes["killed"].id in ids
    assert nodes["low"].id in ids
    assert nodes["passed"].id in ids
    assert nodes["healthy"].id not in ids
    domains = {d for _n, d in rows}
    assert "bookkeeping tools for freelancers" in domains
    assert "clinic scheduling software" in domains


def test_graveyard_items_shape(seeded):
    store, nodes = seeded
    from app.autonomous.graveyard import graveyard_items

    items = graveyard_items(store)
    by_id = {i.node_id: i for i in items}

    killed = by_id[nodes["killed"].id]
    assert killed.project_id == "p1"
    assert killed.project_domain == "bookkeeping tools for freelancers"
    assert killed.kill_lenses == ["empty_for_a_reason"]
    assert killed.thesis_first_line == "Nobody serves paper-receipt-heavy sole traders."
    assert killed.viability == 62
    assert killed.external is False

    passed = by_id[nodes["passed"].id]
    assert passed.triage_reason == "too_crowded"
    assert passed.kill_lenses == []


def test_graveyard_merges_external_postmortems(seeded):
    store, _nodes = seeded
    from app.autonomous.graveyard import graveyard_items

    items = graveyard_items(store, limit=200)
    external = [i for i in items if i.external]
    assert external, "postmortem corpus entries must be merged in"
    assert all(i.project_id is None for i in external)
    assert all(i.kill_lenses for i in external)  # carry the recorded kill reason


def test_graveyard_q_token_filter(seeded):
    store, nodes = seeded
    from app.autonomous.graveyard import graveyard_items

    items = graveyard_items(store, q="dental waitlist")
    internal = [i for i in items if not i.external]
    assert [i.node_id for i in internal] == [nodes["passed"].id]

    # q also filters the external corpus (token overlap, not substring luck).
    items = graveyard_items(store, q="zzzznothingmatches")
    assert items == []


def test_graveyard_limit(seeded):
    store, _nodes = seeded
    from app.autonomous.graveyard import graveyard_items

    assert len(graveyard_items(store, limit=2)) == 2


def test_graveyard_empty_store(tmp_path):
    from app.autonomous.graveyard import graveyard_items
    from app.autonomous.store import TreeStore

    store = TreeStore(path=str(tmp_path / "empty.db"))
    # No internal rejections; the external corpus still surfaces (flagged).
    items = graveyard_items(store)
    assert all(i.external for i in items)


# --------------------------------------------------------------------------- #
# Context block + decomposition-prompt injection                               #
# --------------------------------------------------------------------------- #
def test_context_block_empty_store_is_empty(tmp_path):
    from app.autonomous.graveyard import graveyard_context_block
    from app.autonomous.store import TreeStore

    store = TreeStore(path=str(tmp_path / "empty.db"))
    assert graveyard_context_block(store, "bookkeeping tools") == ""


def test_context_block_renders_rejections(seeded):
    store, nodes = seeded
    from app.autonomous.graveyard import graveyard_context_block

    block = graveyard_context_block(store, "bookkeeping tools for freelancers")
    assert "SPACES ALREADY REJECTED" in block
    assert "do not re-propose" in block
    assert "kill reason has expired" in block
    assert nodes["killed"].title in block
    assert "empty_for_a_reason" in block
    # Ranked by domain-token overlap but capped, never LLM.
    assert nodes["passed"].title in block  # cross-project entries still surface


def test_context_block_caps_entries(seeded):
    store, _nodes = seeded
    from app.autonomous.graveyard import graveyard_context_block

    block = graveyard_context_block(store, "bookkeeping", cap=1)
    assert block.count("\n- ") == 1


class _CaptureClient:
    """Fake LLM client that records the decompose prompt and returns no JSON."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, prompt, system=None, model=None, max_turns=1, timeout=0):
        self.prompts.append(prompt)

        class _R:
            text = "[]"

        return _R()


@pytest.mark.asyncio
async def test_decompose_prompt_injects_graveyard_when_rejections_exist(seeded):
    store, nodes = seeded
    from app.autonomous.engine import expand_structural, root_node
    from app.autonomous.schemas import Project

    project = Project(id="p1", domain="bookkeeping tools for freelancers")
    client = _CaptureClient()
    await expand_structural(root_node(project), project, client, "m", store=store)
    assert client.prompts, "the decompose call must have been made"
    prompt = client.prompts[0]
    assert "SPACES ALREADY REJECTED" in prompt
    assert nodes["killed"].title in prompt


@pytest.mark.asyncio
async def test_decompose_prompt_omits_graveyard_when_store_empty(tmp_path):
    from app.autonomous.engine import expand_structural, root_node
    from app.autonomous.schemas import Project
    from app.autonomous.store import TreeStore

    store = TreeStore(path=str(tmp_path / "empty.db"))
    project = Project(id="px", domain="bookkeeping tools for freelancers")
    client = _CaptureClient()
    await expand_structural(root_node(project), project, client, "m", store=store)
    assert "SPACES ALREADY REJECTED" not in client.prompts[0]


@pytest.mark.asyncio
async def test_decompose_prompt_without_store_stays_clean(seeded):
    """No store handle (e.g. legacy callers) → no injection, no crash."""
    _store, _nodes = seeded
    from app.autonomous.engine import expand_structural, root_node
    from app.autonomous.schemas import Project

    project = Project(id="p1", domain="bookkeeping tools for freelancers")
    client = _CaptureClient()
    await expand_structural(root_node(project), project, client, "m")
    assert "SPACES ALREADY REJECTED" not in client.prompts[0]


# --------------------------------------------------------------------------- #
# HTTP endpoint                                                                #
# --------------------------------------------------------------------------- #
def test_graveyard_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "fixture")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "cache.db"))

    from app import config as config_mod

    config_mod.get_settings.cache_clear()

    import app.autonomous.store as store_mod
    from app.autonomous.schemas import Project
    from app.autonomous.store import TreeStore

    store = TreeStore(path=str(tmp_path / "auto.db"))
    monkeypatch.setattr(store_mod, "_store", store, raising=False)

    store.create_project(Project(id="p1", domain="bookkeeping tools"))
    node = _mk_gap_node(
        store, "p1", "receipt OCR for sole traders",
        viability=30, thesis="Paper receipts are unserved.",
    )

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/graveyard")
        assert r.status_code == 200
        items = r.json()
        internal = [i for i in items if not i["external"]]
        assert [i["node_id"] for i in internal] == [node.id]
        assert internal[0]["project_domain"] == "bookkeeping tools"

        r = client.get("/api/graveyard", params={"q": "receipt", "limit": 5})
        assert r.status_code == 200
        assert len(r.json()) <= 5
        assert any(i["node_id"] == node.id for i in r.json())

        r = client.get("/api/graveyard", params={"q": "zzzznothingmatches"})
        assert r.json() == []

    config_mod.get_settings.cache_clear()
