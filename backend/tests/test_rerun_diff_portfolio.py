"""Phase 3 C3 + C4 and Phase 4 H1 — re-run, diff, scavenger seam, portfolio.

Contract (docs/strategy/phase234-build.md):

* C3: ``POST /api/projects/{pid}/rerun`` clones domain/steering/budget into a
  fresh project linked via ``parent_project_id``; ``GET /api/projects/{pid}/
  diff?against={other}`` is a pure-store node-level diff by normalized gap
  title — new / gone / moved with viability + fit deltas.
* C4: ``Budget.allow_idle_deepening`` defaults False; the ``continue_deepening``
  control is valid ONLY when the contract conditions hold (opt-in + exhausted +
  ample headroom + unexpanded starred branches) and 409s otherwise; the
  ``scavenger_candidates`` helper names the deepening candidates.
* H1: ``GET /api/portfolio`` rolls up every scored gap across projects.

Everything runs against a private temp TreeStore — hermetic, no LLM, no network.
"""

from __future__ import annotations

import pytest

from app.autonomous.engine import make_node
from app.autonomous.governor import UsageGovernor
from app.autonomous.schemas import (
    Budget,
    CreateProjectRequest,
    NodeKind,
    NodeState,
    ProjectStatus,
    SteeringContext,
)
from app.autonomous.service import ExplorerService
from app.autonomous.store import TreeStore


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _gap(
    store: TreeStore,
    project_id: str,
    title: str,
    viability: int | None = None,
    fit: int | None = None,
    star: bool = False,
    confidence: str | None = None,
):
    """Mint + persist a scored gap node (fixture tree building block)."""
    node = make_node(project_id, None, NodeKind.GAP, title, keywords=["k"])
    node.state = NodeState.SCORED
    node.viability = viability
    node.fit = fit
    node.star = star
    node.confidence = confidence
    store.upsert_node(node)
    return node


@pytest.fixture()
def world(tmp_path):
    """A hermetic service + store pair (no singletons, no LLM, no network)."""
    store = TreeStore(path=str(tmp_path / "b5.db"))
    service = ExplorerService(store, UsageGovernor())
    return service, store


@pytest.fixture()
def client(world, tmp_path, monkeypatch):
    """A TestClient whose app is wired to the hermetic store/service pair."""
    service, store = world
    monkeypatch.setenv("LLM_BACKEND", "fixture")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "cache.db"))

    from app import config as config_mod

    config_mod.get_settings.cache_clear()

    import app.autonomous.service as service_mod
    import app.autonomous.store as store_mod

    monkeypatch.setattr(service_mod, "_service", service, raising=False)
    monkeypatch.setattr(store_mod, "_store", store, raising=False)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
    config_mod.get_settings.cache_clear()


def _make_project(service, domain="bookkeeping tools for freelancers", **kwargs):
    return service.create(
        CreateProjectRequest(
            domain=domain,
            sub_segments=kwargs.pop("sub_segments", ["solo tax prep"]),
            budget=kwargs.pop("budget", Budget(max_nodes=7)),
            steering=kwargs.pop(
                "steering", SteeringContext(brief="ex-accountant, distribution via CPAs")
            ),
            intake=kwargs.pop("intake", {"who": "freelancers"}),
            autostart=False,
            **kwargs,
        )
    )


# --------------------------------------------------------------------------- #
# C3 — re-run                                                                  #
# --------------------------------------------------------------------------- #
def test_budget_allow_idle_deepening_defaults_false():
    assert Budget().allow_idle_deepening is False


def test_rerun_clones_domain_steering_and_links_parent(world, client):
    service, store = world
    parent = _make_project(service)

    r = client.post(f"/api/projects/{parent.id}/rerun", json={"autostart": False})
    assert r.status_code == 200
    body = r.json()

    assert body["id"] != parent.id
    assert body["parent_project_id"] == parent.id
    assert body["domain"] == parent.domain
    assert body["sub_segments"] == parent.sub_segments
    assert body["steering"]["brief"] == parent.steering.brief
    assert body["budget"]["max_nodes"] == parent.budget.max_nodes
    assert body["intake"] == parent.intake
    # autostart=false → the clone rests PAUSED, spending nothing.
    assert body["status"] == "paused"

    # The clone is persisted with its own DOMAIN root and no shared nodes.
    clone = store.get_project(body["id"])
    assert clone is not None and clone.parent_project_id == parent.id
    roots = store.get_nodes(body["id"])
    assert len(roots) == 1 and roots[0].kind is NodeKind.DOMAIN


def test_rerun_unknown_project_404s(client):
    assert client.post("/api/projects/nope/rerun", json={}).status_code == 404


# --------------------------------------------------------------------------- #
# C3 — diff on two fixture trees                                               #
# --------------------------------------------------------------------------- #
def test_diff_new_gone_moved_by_normalized_title(world, client):
    service, store = world
    old = _make_project(service)
    new = _make_project(service)

    # Baseline (old) run: one gap that will move, one that will disappear.
    _gap(store, old.id, "Solo Tax Prep", viability=60, fit=40)
    _gap(store, old.id, "Receipt OCR for markets", viability=55, fit=None)
    # Current (new) run: the mover restyled ("solo  tax-prep!" ≡ "Solo Tax Prep"),
    # plus a brand-new gap. A non-gap node must never enter the diff.
    _gap(store, new.id, "solo  tax-prep!", viability=72, fit=48)
    fresh = _gap(store, new.id, "Quarterly VAT nudges", viability=66, fit=None)
    seg = make_node(new.id, None, NodeKind.SEGMENT, "some segment")
    store.upsert_node(seg)

    r = client.get(f"/api/projects/{new.id}/diff", params={"against": old.id})
    assert r.status_code == 200
    diff = r.json()
    assert diff["project_id"] == new.id and diff["against"] == old.id

    assert [e["title"] for e in diff["new"]] == ["Quarterly VAT nudges"]
    assert diff["new"][0]["node_id"] == fresh.id
    assert diff["new"][0]["fit"] is None  # null over invented

    assert [e["title"] for e in diff["gone"]] == ["Receipt OCR for markets"]

    assert len(diff["moved"]) == 1
    moved = diff["moved"][0]
    assert moved["viability_from"] == 60 and moved["viability_to"] == 72
    assert moved["fit_from"] == 40 and moved["fit_to"] == 48


def test_diff_identical_scores_are_not_moved(world, client):
    service, store = world
    a = _make_project(service)
    b = _make_project(service)
    _gap(store, a.id, "Same Gap", viability=50, fit=None)
    _gap(store, b.id, "Same Gap", viability=50, fit=None)

    diff = client.get(f"/api/projects/{b.id}/diff", params={"against": a.id}).json()
    assert diff["new"] == [] and diff["gone"] == [] and diff["moved"] == []


def test_diff_unknown_side_404s(world, client):
    service, _ = world
    p = _make_project(service)
    assert client.get(f"/api/projects/{p.id}/diff", params={"against": "nope"}).status_code == 404
    assert client.get("/api/projects/nope/diff", params={"against": p.id}).status_code == 404


# --------------------------------------------------------------------------- #
# C4 — scavenger seam: opt-in only, contract conditions enforced               #
# --------------------------------------------------------------------------- #
def test_continue_deepening_rejected_when_not_opted_in(world, client):
    service, store = world
    project = _make_project(service)  # allow_idle_deepening defaults False

    r = client.post(
        f"/api/projects/{project.id}/control", json={"action": "continue_deepening"}
    )
    assert r.status_code == 409
    assert "opted in" in r.json()["detail"]


def test_continue_deepening_rejected_when_not_exhausted(world, client):
    service, store = world
    project = _make_project(
        service, budget=Budget(max_nodes=7, allow_idle_deepening=True)
    )
    _gap(store, project.id, "Starred branch", viability=80, star=True)
    # Opted in + candidates exist, but the run is PAUSED, not exhausted.
    r = client.post(
        f"/api/projects/{project.id}/control", json={"action": "continue_deepening"}
    )
    assert r.status_code == 409
    assert "exhausted" in r.json()["detail"]


def test_continue_deepening_rejected_without_starred_candidates(world, client):
    service, store = world
    project = _make_project(
        service, budget=Budget(max_nodes=7, allow_idle_deepening=True)
    )
    _gap(store, project.id, "Unstarred gap", viability=80, star=False)
    p = store.get_project(project.id)
    p.status = ProjectStatus.EXHAUSTED
    store.save_project(p)

    r = client.post(
        f"/api/projects/{project.id}/control", json={"action": "continue_deepening"}
    )
    assert r.status_code == 409
    assert "starred" in r.json()["detail"]


def test_scavenger_candidates_are_unexpanded_starred_gaps(world):
    service, store = world
    from app.autonomous.scavenger import scavenger_candidates

    project = _make_project(service)
    lo = _gap(store, project.id, "Starred low", viability=76, star=True)
    hi = _gap(store, project.id, "Starred high", viability=90, star=True)
    _gap(store, project.id, "Unstarred", viability=88, star=False)
    expanded = _gap(store, project.id, "Starred but expanded", viability=85, star=True)
    expanded.child_ids = ["child"]
    store.upsert_node(expanded)

    got = scavenger_candidates(store, project.id)
    # Only unexpanded starred gaps, highest viability first.
    assert [n.id for n in got] == [hi.id, lo.id]


def test_continue_deepening_queues_segments_when_eligible(world):
    service, store = world
    project = _make_project(
        service, budget=Budget(max_nodes=7, allow_idle_deepening=True)
    )
    starred = _gap(
        store, project.id, "Starred branch", viability=90, fit=70, star=True
    )
    p = store.get_project(project.id)
    p.status = ProjectStatus.EXHAUSTED
    store.save_project(p)

    updated = service.continue_deepening(project.id)  # fresh governor → ample
    assert updated.status is ProjectStatus.RUNNING

    children = [
        n for n in store.get_nodes(project.id) if n.parent_id == starred.id
    ]
    assert len(children) == 1
    assert children[0].kind is NodeKind.SEGMENT
    assert children[0].state is NodeState.QUEUED
    assert store.get_node(starred.id).child_ids == [children[0].id]

    # Idempotent: nothing eligible remains, so a second call is rejected.
    p = store.get_project(project.id)
    p.status = ProjectStatus.EXHAUSTED
    store.save_project(p)
    with pytest.raises(ValueError):
        service.continue_deepening(project.id)


# --------------------------------------------------------------------------- #
# H1 — portfolio                                                               #
# --------------------------------------------------------------------------- #
def test_portfolio_shape_across_two_projects(world, client):
    service, store = world
    p1 = _make_project(service, domain="bookkeeping tools for freelancers")
    p2 = _make_project(service, domain="fleet telematics for small haulers")

    g1 = _gap(store, p1.id, "Solo Tax Prep", viability=72, fit=48, star=True,
              confidence="high")
    g1.triage = "interested"
    g1.stage = "interviewing"
    store.upsert_node(g1)
    g2 = _gap(store, p2.id, "Cold-chain compliance logs", viability=58, fit=None)
    # Structural nodes never enter the portfolio.
    store.upsert_node(make_node(p1.id, None, NodeKind.SEGMENT, "a segment"))

    r = client.get("/api/portfolio")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2

    by_node = {i["node_id"]: i for i in items}
    one = by_node[g1.id]
    assert one["project_id"] == p1.id
    assert one["domain"] == "bookkeeping tools for freelancers"
    assert one["title"] == "Solo Tax Prep"
    assert one["viability"] == 72 and one["fit"] == 48
    assert one["confidence"] == "high" and one["star"] is True
    assert one["triage"] == "interested" and one["stage"] == "interviewing"
    assert one["updated_at"]

    two = by_node[g2.id]
    assert two["project_id"] == p2.id
    assert two["domain"] == "fleet telematics for small haulers"
    assert two["fit"] is None          # no steering score → null, never faked
    assert two["confidence"] is None
    assert two["star"] is False and two["triage"] is None and two["stage"] is None


def test_portfolio_empty_store_is_an_empty_list(world, client):
    assert client.get("/api/portfolio").json() == []
