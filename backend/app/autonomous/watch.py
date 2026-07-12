"""Space Watch (Phase 3 C2) — source-only sweeps over watched nodes.

The user flags a node ``watched`` (the ``watch_node`` control); the
:class:`WatchService` then re-fetches the source adapters for that node's
keywords — **source fetch only, NO LLM, ever** — and diffs the result against
the previous sweep's baseline snapshot (itself seeded from the first sweep,
which reads through the adapters' own ``ingest:*`` caches). A *material
shift* — ≥3 never-seen items, or any new regulatory/outcomes hit — emits a
``watch_alert`` event {node_id, summary, evidence[]} plus a human-readable
project log line, so the dashboard's movers block and the live activity feed
both see it.

Triggering:

* ``POST /api/watch/sweep`` — the manual trigger (and what the tests use).
* :func:`start_background_sweeper` — an **opt-in** periodic loop gated on
  ``Settings.watch_sweep_hours`` (env ``WATCH_SWEEP_HOURS``). The default
  ``None`` means NO background task ever starts: a server boot must never
  begin fetching on its own.

Degrade-don't-crash throughout: a broken source, cache, or node yields fewer
alerts, never an exception. Evidence is only ever the actual new source items
(null/absent over invented), with honest per-item live/mock provenance.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable, Optional

from ..cache import Cache, get_cache
from ..config import get_settings
from ..schemas import Evidence, RawItem, SourceStatus
from ..sources.base import Source
from ..sources.registry import get_sources
from .schemas import (
    EventType,
    ExplorerEvent,
    Node,
    WatchAlert,
    WatchedNodeStatus,
    WatchSweepResult,
)
from .store import TreeStore, get_store

logger = logging.getLogger("gapfinder.watch")

# Cache namespace for the per-node baseline snapshot ({item_key: weight}).
_NS = "watch"
# Never expire the baseline — a stale baseline would re-alert on old items.
_NO_TTL = -1

# The material-shift rule (contract C2).
_MATERIAL_NEW_ITEMS = 3
_REGULATORY_SOURCES = frozenset({"regulatory", "outcomes"})

# How many new items an alert quotes as evidence (strongest first).
_EVIDENCE_CAP = 5


def _item_key(item: RawItem) -> str:
    """Stable identity of a source item across sweeps."""
    return f"{item.source.value}:{item.id}"


class WatchService:
    """Sweeps watched nodes' sources and raises material-shift alerts.

    ``sources_factory`` is injectable for tests; the default is the demand mix
    plus the pressure-only adapters (regulatory/outcomes hits are exactly what
    a watcher cares about).
    """

    def __init__(
        self,
        store: TreeStore,
        cache: Optional[Cache] = None,
        sources_factory: Optional[Callable[[], list[Source]]] = None,
    ) -> None:
        self.store = store
        self._cache = cache
        self._sources_factory = sources_factory or (
            lambda: get_sources(include_pressure_only=True)
        )

    @property
    def cache(self) -> Cache:
        if self._cache is None:
            self._cache = get_cache()
        return self._cache

    # ------------------------------------------------------------------ #
    # The sweep                                                           #
    # ------------------------------------------------------------------ #
    def sweep(self) -> WatchSweepResult:
        """Re-fetch sources for every watched node and diff vs the baseline.

        The first sweep of a node only seeds its baseline (no alert — there is
        nothing honest to diff against yet). Never raises; a node whose sweep
        fails is skipped.
        """
        alerts: list[WatchAlert] = []
        watched = self.store.watched_nodes()
        for node, _domain in watched:
            try:
                alert = self._sweep_node(node)
            except Exception:  # noqa: BLE001 - one bad node must not sink the sweep.
                logger.exception("watch sweep failed for node %s", node.id)
                continue
            if alert is not None:
                alerts.append(alert)
        return WatchSweepResult(swept=len(watched), alerts=alerts)

    def _sweep_node(self, node: Node) -> Optional[WatchAlert]:
        """Fetch, diff, maybe alert, and roll the baseline forward for one node."""
        keywords = [k for k in node.keywords if k] or [node.title]
        items: dict[str, RawItem] = {}
        live_by_key: dict[str, bool] = {}
        for source in self._sources_factory():
            try:
                result = source.fetch(node.title, keywords, [])
            except Exception:  # noqa: BLE001 - adapters shouldn't raise; guard anyway.
                continue
            is_live = bool(
                result.report is not None
                and result.report.status is SourceStatus.LIVE
            )
            for item in result.items:
                key = _item_key(item)
                items[key] = item
                live_by_key[key] = is_live

        baseline = self.cache.get(_NS, node.id, ttl=_NO_TTL)
        # Roll the baseline forward (union: vanished items stay known so they
        # can never re-alert as "new" later).
        merged = dict(baseline) if isinstance(baseline, dict) else {}
        merged.update({k: item.weight for k, item in items.items()})
        self.cache.set(_NS, merged, node.id)

        if not isinstance(baseline, dict):
            return None  # first sweep: seed the snapshot, nothing to diff yet.

        new_keys = [k for k in items if k not in baseline]
        if not new_keys:
            return None
        regulatory_hit = any(
            items[k].source.value in _REGULATORY_SOURCES for k in new_keys
        )
        if len(new_keys) < _MATERIAL_NEW_ITEMS and not regulatory_hit:
            return None  # movement, but not a material shift.

        new_keys.sort(key=lambda k: items[k].weight, reverse=True)
        evidence = [
            Evidence(
                source=items[k].source,
                url=items[k].url,
                quote=items[k].title,
                date=str(items[k].created) if items[k].created else None,
                live=live_by_key.get(k, False),
            )
            for k in new_keys[:_EVIDENCE_CAP]
        ]
        source_names = sorted({items[k].source.value for k in new_keys})
        summary = (
            f"{len(new_keys)} new signal{'s' if len(new_keys) != 1 else ''} "
            f"for '{node.title}' ({', '.join(source_names)})"
        )
        if regulatory_hit:
            summary += " — includes a regulatory/outcomes hit"
        alert = WatchAlert(
            node_id=node.id,
            summary=summary,
            evidence=evidence,
            new_items=len(new_keys),
            weight_delta=sum(items[k].weight for k in new_keys),
            regulatory_hit=regulatory_hit,
        )
        self.store.append_event(
            ExplorerEvent(
                project_id=node.project_id,
                type=EventType.WATCH_ALERT,
                node=node,
                alert=alert,
                message=summary,
            )
        )
        self.store.append_event(
            ExplorerEvent(
                project_id=node.project_id,
                type=EventType.LOG,
                message=f"Space Watch: {summary}.",
            )
        )
        return alert

    # ------------------------------------------------------------------ #
    # GET /api/watch backing                                              #
    # ------------------------------------------------------------------ #
    def watch_status(self) -> list[WatchedNodeStatus]:
        """Every watched node with its most recent alert (dashboard movers)."""
        out: list[WatchedNodeStatus] = []
        for node, domain in self.store.watched_nodes():
            out.append(
                WatchedNodeStatus(
                    project_id=node.project_id,
                    project_domain=domain,
                    node=node,
                    last_alert=self.store.last_watch_alert(node.project_id, node.id),
                )
            )
        return out


# --------------------------------------------------------------------------- #
# Opt-in background loop (default OFF — NO unattended fetching)                #
# --------------------------------------------------------------------------- #
def start_background_sweeper(
    service: Optional[WatchService] = None,
    sweep_hours: Optional[float] = None,
) -> Optional[asyncio.Task]:
    """Start the periodic sweep task IF the user opted in; else return None.

    Gated on ``sweep_hours`` (defaults to ``Settings.watch_sweep_hours``,
    env ``WATCH_SWEEP_HOURS``): ``None``/``<= 0`` means no task ever starts —
    the hard "nothing scheduled runs by default" contract. Also degrades to
    None (rather than crashing the caller) when no event loop is running.
    """
    hours = sweep_hours if sweep_hours is not None else get_settings().watch_sweep_hours
    if hours is None or hours <= 0:
        return None
    svc = service or get_watch_service()

    async def _loop() -> None:
        while True:
            await asyncio.sleep(hours * 3600)
            try:
                await asyncio.to_thread(svc.sweep)
            except Exception:  # noqa: BLE001 - the sweeper must never die loudly.
                logger.exception("background watch sweep failed")

    try:
        return asyncio.get_running_loop().create_task(_loop())
    except RuntimeError:
        return None  # no running loop — nothing to schedule on.


# --------------------------------------------------------------------------- #
# Global singleton                                                            #
# --------------------------------------------------------------------------- #
_watch_service: Optional[WatchService] = None
_watch_lock = threading.Lock()


def get_watch_service() -> WatchService:
    """Return the process-wide :class:`WatchService` bound to the shared store."""
    global _watch_service
    if _watch_service is None:
        with _watch_lock:
            if _watch_service is None:
                _watch_service = WatchService(get_store())
    return _watch_service
