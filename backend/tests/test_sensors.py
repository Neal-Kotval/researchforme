"""Phase 2 sensors — triage (S1), look-into stage (S2), and the watched flag (C2).

Contract (docs/strategy/phase234-build.md): the Node grows five user-sensor
fields — ``triage`` / ``triage_reason`` / ``stage`` / ``learnings`` / ``watched``
— each mutated ONLY through control actions (``set_triage`` / ``set_stage`` /
``watch_node`` / ``unwatch_node``) that persist the node via the store and emit
``node_updated`` through the event log. These tests exercise:

* control round-trips for every action (set → persisted → clear → persisted);
* invalid triage / stage values rejected at the schema boundary;
* unknown / cross-project node ids raise (the router turns that into a 404/500);
* every mutation lands in the event log as ``node_updated``.

Everything runs against a private temp :class:`TreeStore` — hermetic, no LLM,
no network.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


@pytest.fixture()
def svc(tmp_path):
    """A hermetic service + store + one project with a single segment node."""
    from app.autonomous.engine import make_node
    from app.autonomous.governor import UsageGovernor
    from app.autonomous.schemas import Budget, CreateProjectRequest, NodeKind
    from app.autonomous.service import ExplorerService
    from app.autonomous.store import TreeStore

    store = TreeStore(path=str(tmp_path / "sensors.db"))
    service = ExplorerService(store, UsageGovernor())
    project = service.create(
        CreateProjectRequest(
            domain="bookkeeping tools for freelancers",
            sub_segments=[],
            budget=Budget(max_nodes=5),
            autostart=False,
        )
    )
    node = make_node(project.id, None, NodeKind.SEGMENT, "solo tax prep", keywords=["tax"])
    store.upsert_node(node)
    return service, store, project, node


def _node_updated_count(store, pid: str) -> int:
    from app.autonomous.schemas import EventType

    return sum(
        1 for e in store.events_since(pid, 0) if e.type == EventType.NODE_UPDATED
    )


# --------------------------------------------------------------------------- #
# S1 — triage                                                                  #
# --------------------------------------------------------------------------- #
def test_set_triage_round_trips_and_clears(svc):
    service, store, project, node = svc

    before = _node_updated_count(store, project.id)
    service.set_triage(project.id, node.id, "passed", "too_crowded")
    saved = store.get_node(node.id)
    assert saved.triage == "passed"
    assert saved.triage_reason == "too_crowded"
    assert _node_updated_count(store, project.id) == before + 1

    # Clearing triage = null; the reason goes with it.
    service.set_triage(project.id, node.id, None, "")
    saved = store.get_node(node.id)
    assert saved.triage is None
    assert saved.triage_reason == ""
    assert _node_updated_count(store, project.id) == before + 2


def test_set_triage_accepts_free_text_reason(svc):
    service, store, project, node = svc
    service.set_triage(project.id, node.id, "interested", "my cousin runs one of these")
    assert store.get_node(node.id).triage_reason == "my cousin runs one of these"


def test_invalid_triage_value_rejected():
    from app.autonomous.schemas import ControlRequest

    with pytest.raises(ValidationError):
        ControlRequest(action="set_triage", node_id="n1", triage="meh")


# --------------------------------------------------------------------------- #
# S2 — look-into stage                                                         #
# --------------------------------------------------------------------------- #
def test_set_stage_round_trips_and_clears(svc):
    service, store, project, node = svc

    service.set_stage(project.id, node.id, "interviewing", "3 of 5 calls confirmed the pain")
    saved = store.get_node(node.id)
    assert saved.stage == "interviewing"
    assert saved.learnings == "3 of 5 calls confirmed the pain"

    service.set_stage(project.id, node.id, None, "")
    saved = store.get_node(node.id)
    assert saved.stage is None
    assert saved.learnings == ""


def test_invalid_stage_value_rejected():
    from app.autonomous.schemas import ControlRequest

    with pytest.raises(ValidationError):
        ControlRequest(action="set_stage", node_id="n1", stage="shipped")


# --------------------------------------------------------------------------- #
# C2 field — watched                                                           #
# --------------------------------------------------------------------------- #
def test_watch_and_unwatch_round_trip(svc):
    service, store, project, node = svc

    assert store.get_node(node.id).watched is False
    service.watch_node(project.id, node.id, True)
    assert store.get_node(node.id).watched is True
    service.watch_node(project.id, node.id, False)
    assert store.get_node(node.id).watched is False


def test_watch_emits_node_updated(svc):
    service, store, project, node = svc
    before = _node_updated_count(store, project.id)
    service.watch_node(project.id, node.id, True)
    assert _node_updated_count(store, project.id) == before + 1


# --------------------------------------------------------------------------- #
# Guard rails                                                                  #
# --------------------------------------------------------------------------- #
def test_sensor_actions_reject_unknown_or_foreign_nodes(svc):
    service, store, project, node = svc

    with pytest.raises(KeyError):
        service.set_triage(project.id, "nope", "interested", "")
    with pytest.raises(KeyError):
        service.set_stage("other-project", node.id, "found", "")
    with pytest.raises(KeyError):
        service.watch_node(project.id, "nope", True)


def test_node_sensor_fields_round_trip_through_json():
    """Schema defaults + JSON round-trip parity for the five new fields."""
    from app.autonomous.schemas import Node, NodeKind

    node = Node(id="n", project_id="p", kind=NodeKind.GAP, title="t")
    assert node.triage is None
    assert node.triage_reason == ""
    assert node.stage is None
    assert node.learnings == ""
    assert node.watched is False

    node.triage = "interested"
    node.triage_reason = "other"
    node.stage = "smoke_testing"
    node.learnings = "landing page at 4% conversion"
    node.watched = True
    back = Node.model_validate_json(node.model_dump_json())
    assert back.triage == "interested"
    assert back.stage == "smoke_testing"
    assert back.learnings == "landing page at 4% conversion"
    assert back.watched is True


def test_node_rejects_invalid_triage_and_stage():
    from app.autonomous.schemas import Node, NodeKind

    with pytest.raises(ValidationError):
        Node(id="n", project_id="p", kind=NodeKind.GAP, title="t", triage="maybe")
    with pytest.raises(ValidationError):
        Node(id="n", project_id="p", kind=NodeKind.GAP, title="t", stage="launched")


# --------------------------------------------------------------------------- #
# HTTP round-trip through the control endpoint                                 #
# --------------------------------------------------------------------------- #
def test_control_endpoint_dispatches_sensor_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "fixture")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "cache.db"))

    from app import config as config_mod

    config_mod.get_settings.cache_clear()

    import app.autonomous.service as service_mod
    import app.autonomous.store as store_mod
    from app.autonomous.engine import make_node
    from app.autonomous.governor import UsageGovernor
    from app.autonomous.schemas import NodeKind
    from app.autonomous.store import TreeStore

    store = TreeStore(path=str(tmp_path / "auto.db"))
    service = service_mod.ExplorerService(store, UsageGovernor())
    monkeypatch.setattr(service_mod, "_service", service, raising=False)
    monkeypatch.setattr(store_mod, "_store", store, raising=False)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        pid = client.post(
            "/api/projects",
            json={"domain": "demo domain", "sub_segments": [], "autostart": False},
        ).json()["id"]
        node = make_node(pid, None, NodeKind.SEGMENT, "seg", keywords=["k"])
        store.upsert_node(node)

        r = client.post(
            f"/api/projects/{pid}/control",
            json={
                "action": "set_triage",
                "node_id": node.id,
                "triage": "passed",
                "triage_reason": "too_small",
            },
        )
        assert r.status_code == 200
        assert store.get_node(node.id).triage == "passed"

        r = client.post(
            f"/api/projects/{pid}/control",
            json={"action": "set_stage", "node_id": node.id, "stage": "found"},
        )
        assert r.status_code == 200
        assert store.get_node(node.id).stage == "found"

        r = client.post(
            f"/api/projects/{pid}/control",
            json={"action": "watch_node", "node_id": node.id},
        )
        assert r.status_code == 200
        assert store.get_node(node.id).watched is True

        r = client.post(
            f"/api/projects/{pid}/control",
            json={"action": "unwatch_node", "node_id": node.id},
        )
        assert r.status_code == 200
        assert store.get_node(node.id).watched is False

        # Missing node_id → 400; invalid triage literal → 422 from pydantic.
        r = client.post(
            f"/api/projects/{pid}/control", json={"action": "set_triage"}
        )
        assert r.status_code == 400
        r = client.post(
            f"/api/projects/{pid}/control",
            json={"action": "set_triage", "node_id": node.id, "triage": "meh"},
        )
        assert r.status_code == 422

    config_mod.get_settings.cache_clear()
