"""Phase 3 C2 — Space Watch: the WatchService sweep, alerts, and endpoints.

Contract (docs/strategy/phase234-build.md C2): ``WatchService.sweep()``
re-fetches sources for each watched node's keywords (source fetch only, NO
LLM), diffs against the previous sweep's baseline snapshot (seeded from the
first sweep over the ingest-cache-backed fetch), and on a material shift
(≥3 new items OR any new regulatory/outcomes hit) emits a ``watch_alert``
event {node_id, summary, evidence[]} plus a project log line. The manual
trigger is ``POST /api/watch/sweep``; ``GET /api/watch`` lists watched nodes
with their last alert. A background periodic sweep is opt-in via
``Settings.watch_sweep_hours`` — the default ``None`` means NO task ever
starts.

Everything here runs against a private temp TreeStore + Cache with injected
fake sources — hermetic, no LLM, no network.
"""

from __future__ import annotations

import asyncio

import pytest

from app.schemas import RawItem, SourceName, SourceReport, SourceStatus
from app.sources.base import FetchResult, Source


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #
class FakeSource(Source):
    """A controllable source: yields exactly the items it's told to."""

    def __init__(self, name: SourceName, items: list[RawItem]):
        self.name = name
        self.items = items

    @property
    def live(self) -> bool:
        return True

    def fetch(self, area, keywords, sub_segments) -> FetchResult:
        return FetchResult(
            items=list(self.items),
            report=SourceReport(
                name=self.name, status=SourceStatus.LIVE, item_count=len(self.items)
            ),
        )


def _item(source: SourceName, i: int, weight: float = 1.0) -> RawItem:
    return RawItem(
        source=source,
        id=f"{source.value}-{i}",
        title=f"signal {i} from {source.value}",
        url=f"https://example.com/{source.value}/{i}",
        weight=weight,
    )


@pytest.fixture()
def world(tmp_path):
    """A hermetic WatchService + store + one project with a watched segment."""
    from app.autonomous.engine import make_node
    from app.autonomous.governor import UsageGovernor
    from app.autonomous.schemas import Budget, CreateProjectRequest, NodeKind
    from app.autonomous.service import ExplorerService
    from app.autonomous.store import TreeStore
    from app.autonomous.watch import WatchService
    from app.cache import Cache

    store = TreeStore(path=str(tmp_path / "watch.db"))
    service = ExplorerService(store, UsageGovernor())
    project = service.create(
        CreateProjectRequest(
            domain="bookkeeping tools for freelancers",
            budget=Budget(max_nodes=5),
            autostart=False,
        )
    )
    node = make_node(
        project.id, None, NodeKind.SEGMENT, "solo tax prep", keywords=["tax", "freelancer"]
    )
    store.upsert_node(node)
    service.watch_node(project.id, node.id, True)

    reddit = FakeSource(SourceName.REDDIT, [_item(SourceName.REDDIT, i) for i in range(3)])
    regulatory = FakeSource(SourceName.REGULATORY, [])
    cache = Cache(path=str(tmp_path / "cache.db"))
    watch = WatchService(
        store, cache=cache, sources_factory=lambda: [reddit, regulatory]
    )
    return watch, store, project, node, reddit, regulatory


def _alert_events(store, pid: str):
    from app.autonomous.schemas import EventType

    return [e for e in store.events_since(pid, 0) if e.type == EventType.WATCH_ALERT]


# --------------------------------------------------------------------------- #
# The sweep + material-shift rule                                              #
# --------------------------------------------------------------------------- #
def test_first_sweep_seeds_baseline_without_alerting(world):
    watch, store, project, node, reddit, _ = world
    result = watch.sweep()
    assert result.swept == 1
    assert result.alerts == []
    assert _alert_events(store, project.id) == []


def test_three_new_items_is_a_material_shift(world):
    watch, store, project, node, reddit, _ = world
    watch.sweep()  # seed the baseline

    reddit.items = reddit.items + [_item(SourceName.REDDIT, i) for i in range(10, 13)]
    result = watch.sweep()

    assert len(result.alerts) == 1
    alert = result.alerts[0]
    assert alert.node_id == node.id
    assert alert.new_items == 3
    assert alert.regulatory_hit is False
    assert alert.summary  # human-readable, non-empty
    assert alert.evidence and all(e.url for e in alert.evidence)

    events = _alert_events(store, project.id)
    assert len(events) == 1
    assert events[0].alert is not None
    assert events[0].alert.node_id == node.id
    # A human-readable log line rides along.
    from app.autonomous.schemas import EventType

    logs = [
        e.message
        for e in store.events_since(project.id, 0)
        if e.type == EventType.LOG
    ]
    assert any("watch" in m.lower() for m in logs)


def test_fewer_than_three_new_items_skips_the_alert(world):
    watch, store, project, node, reddit, _ = world
    watch.sweep()
    reddit.items = reddit.items + [
        _item(SourceName.REDDIT, 20),
        _item(SourceName.REDDIT, 21),
    ]
    result = watch.sweep()
    assert result.alerts == []
    assert _alert_events(store, project.id) == []


def test_one_new_regulatory_item_is_material(world):
    watch, store, project, node, reddit, regulatory = world
    watch.sweep()
    regulatory.items = [_item(SourceName.REGULATORY, 1, weight=2.0)]
    result = watch.sweep()
    assert len(result.alerts) == 1
    assert result.alerts[0].regulatory_hit is True
    assert result.alerts[0].new_items == 1


def test_already_alerted_items_do_not_re_alert(world):
    watch, store, project, node, reddit, _ = world
    watch.sweep()
    reddit.items = reddit.items + [_item(SourceName.REDDIT, i) for i in range(30, 33)]
    assert len(watch.sweep().alerts) == 1
    # Same items again → nothing new → no second alert.
    assert watch.sweep().alerts == []
    assert len(_alert_events(store, project.id)) == 1


def test_unwatched_nodes_are_ignored(world):
    watch, store, project, node, reddit, _ = world
    svc_store_node = store.get_node(node.id)
    svc_store_node.watched = False
    store.upsert_node(svc_store_node)
    result = watch.sweep()
    assert result.swept == 0
    assert result.alerts == []


# --------------------------------------------------------------------------- #
# watch_status (GET /api/watch backing)                                        #
# --------------------------------------------------------------------------- #
def test_watch_status_lists_nodes_and_last_alert(world):
    watch, store, project, node, reddit, _ = world
    statuses = watch.watch_status()
    assert len(statuses) == 1
    assert statuses[0].node.id == node.id
    assert statuses[0].project_id == project.id
    assert statuses[0].project_domain == project.domain
    assert statuses[0].last_alert is None

    watch.sweep()
    reddit.items = reddit.items + [_item(SourceName.REDDIT, i) for i in range(40, 44)]
    watch.sweep()
    statuses = watch.watch_status()
    assert statuses[0].last_alert is not None
    assert statuses[0].last_alert.node_id == node.id


# --------------------------------------------------------------------------- #
# Opt-in background loop: default OFF, forever                                 #
# --------------------------------------------------------------------------- #
def test_default_settings_never_start_a_background_task(monkeypatch):
    monkeypatch.delenv("WATCH_SWEEP_HOURS", raising=False)

    from app import config as config_mod
    from app.autonomous.watch import start_background_sweeper

    config_mod.get_settings.cache_clear()
    try:
        assert config_mod.get_settings().watch_sweep_hours is None
        assert config_mod.Settings().watch_sweep_hours is None

        async def _inside_a_loop():
            # Even with a running event loop available, None → no task, ever.
            return start_background_sweeper()

        assert asyncio.run(_inside_a_loop()) is None
        # And without a loop it degrades to None rather than crashing.
        assert start_background_sweeper() is None
    finally:
        config_mod.get_settings.cache_clear()


def test_sweeper_starts_only_when_opted_in(world):
    watch, *_ = world
    from app.autonomous.watch import start_background_sweeper

    async def _run():
        task = start_background_sweeper(service=watch, sweep_hours=4.0)
        assert task is not None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
# HTTP round-trip                                                              #
# --------------------------------------------------------------------------- #
def test_watch_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "fixture")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.delenv("WATCH_SWEEP_HOURS", raising=False)

    from app import config as config_mod

    config_mod.get_settings.cache_clear()

    import app.autonomous.service as service_mod
    import app.autonomous.store as store_mod
    import app.autonomous.watch as watch_mod
    from app.autonomous.engine import make_node
    from app.autonomous.governor import UsageGovernor
    from app.autonomous.schemas import NodeKind
    from app.autonomous.store import TreeStore
    from app.autonomous.watch import WatchService
    from app.cache import Cache

    store = TreeStore(path=str(tmp_path / "auto.db"))
    service = service_mod.ExplorerService(store, UsageGovernor())
    reddit = FakeSource(SourceName.REDDIT, [_item(SourceName.REDDIT, i) for i in range(2)])
    watch = WatchService(
        store,
        cache=Cache(path=str(tmp_path / "wcache.db")),
        sources_factory=lambda: [reddit],
    )
    monkeypatch.setattr(service_mod, "_service", service, raising=False)
    monkeypatch.setattr(store_mod, "_store", store, raising=False)
    monkeypatch.setattr(watch_mod, "_watch_service", watch, raising=False)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        pid = client.post(
            "/api/projects",
            json={"domain": "demo domain", "autostart": False},
        ).json()["id"]
        node = make_node(pid, None, NodeKind.SEGMENT, "seg", keywords=["k"])
        store.upsert_node(node)
        client.post(
            f"/api/projects/{pid}/control",
            json={"action": "watch_node", "node_id": node.id},
        )

        r = client.get("/api/watch")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["node"]["id"] == node.id
        assert body[0]["last_alert"] is None

        # First sweep seeds; a grown source then alerts on the second.
        r = client.post("/api/watch/sweep")
        assert r.status_code == 200
        assert r.json()["alerts"] == []
        reddit.items = reddit.items + [_item(SourceName.REDDIT, i) for i in range(5, 8)]
        r = client.post("/api/watch/sweep")
        assert r.status_code == 200
        assert len(r.json()["alerts"]) == 1
        assert r.json()["alerts"][0]["node_id"] == node.id

        r = client.get("/api/watch")
        assert r.json()[0]["last_alert"]["node_id"] == node.id

    config_mod.get_settings.cache_clear()
