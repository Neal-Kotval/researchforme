"""Event-sourced persistence for Autonomous Exploration Mode.

The :class:`TreeStore` is the single source of truth for projects, their
exploration trees, and the per-project event log that drives the live SSE
stream. It is deliberately small: four SQLite tables (``ap_projects``,
``ap_nodes``, ``ap_events``, and the single-row ``ap_preferences``) that store
each pydantic model as JSON via
``model_dump(mode="json")`` and rebuild it with ``model_validate``.

Design notes:

* **Event sourcing.** Every mutation the service cares about is also appended to
  ``ap_events`` with a monotonic, per-project ``seq``. A late-joining SSE client
  hydrates from :meth:`snapshot` and then replays anything it missed via
  :meth:`events_since`, so the tree is fully resumable.
* **Live fan-out.** :meth:`subscribe` hands out an ``asyncio.Queue`` per
  subscriber; :meth:`append_event` pushes each freshly-persisted event onto every
  queue. ``append_event`` may be called from a worker thread (sources fetch runs
  in ``asyncio.to_thread``), so fan-out is done via ``call_soon_threadsafe``
  against each subscriber's own loop.
* **Thread-safe.** A single re-entrant lock guards both the in-memory subscriber
  registry and the (short) SQLite critical sections. SQLite runs in WAL mode and
  opens a fresh connection per call, matching ``app.cache``.

Reuses ``settings.cache_path`` so autonomous state lives alongside the ingest
cache with no extra configuration.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from typing import AsyncIterator, Optional

from ..config import get_settings
from .schemas import ExplorerEvent, Node, Preferences, Project, TreeSnapshot


class TreeStore:
    """SQLite-backed store for projects, nodes, and the event log."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or get_settings().cache_path
        self._lock = threading.RLock()
        # project_id -> list of (queue, loop) for live subscribers.
        self._subscribers: dict[str, list[tuple[asyncio.Queue, asyncio.AbstractEventLoop]]] = {}
        self._init()

    # ------------------------------------------------------------------ #
    # Connection / schema                                                #
    # ------------------------------------------------------------------ #
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS ap_projects (
                       id    TEXT PRIMARY KEY,
                       value TEXT NOT NULL
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS ap_nodes (
                       id         TEXT PRIMARY KEY,
                       project_id TEXT NOT NULL,
                       value      TEXT NOT NULL
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ap_nodes_project ON ap_nodes (project_id)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS ap_preferences (
                       id    INTEGER PRIMARY KEY CHECK (id = 1),
                       value TEXT NOT NULL
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS ap_events (
                       project_id TEXT NOT NULL,
                       seq        INTEGER NOT NULL,
                       value      TEXT NOT NULL,
                       PRIMARY KEY (project_id, seq)
                   )"""
            )

    # ------------------------------------------------------------------ #
    # Projects                                                           #
    # ------------------------------------------------------------------ #
    def create_project(self, project: Project) -> None:
        """Insert a new project row (idempotent via INSERT OR REPLACE)."""
        self.save_project(project)

    def save_project(self, project: Project) -> None:
        payload = json.dumps(project.model_dump(mode="json"))
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ap_projects (id, value) VALUES (?, ?)",
                (project.id, payload),
            )

    def get_project(self, project_id: str) -> Optional[Project]:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM ap_projects WHERE id=?", (project_id,)
            ).fetchone()
        if not row:
            return None
        return Project.model_validate(json.loads(row[0]))

    def list_projects(self) -> list[Project]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT value FROM ap_projects").fetchall()
        projects = [Project.model_validate(json.loads(r[0])) for r in rows]
        projects.sort(key=lambda p: p.created_at, reverse=True)
        return projects

    # ------------------------------------------------------------------ #
    # Nodes                                                              #
    # ------------------------------------------------------------------ #
    def upsert_node(self, node: Node) -> None:
        payload = json.dumps(node.model_dump(mode="json"))
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ap_nodes (id, project_id, value) VALUES (?, ?, ?)",
                (node.id, node.project_id, payload),
            )

    def get_node(self, node_id: str) -> Optional[Node]:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM ap_nodes WHERE id=?", (node_id,)
            ).fetchone()
        if not row:
            return None
        return Node.model_validate(json.loads(row[0]))

    def get_nodes(self, project_id: str) -> list[Node]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT value FROM ap_nodes WHERE project_id=?", (project_id,)
            ).fetchall()
        nodes = [Node.model_validate(json.loads(r[0])) for r in rows]
        nodes.sort(key=lambda n: n.created_at)
        return nodes

    def rejected_gaps(self) -> list[tuple[Node, Optional[str]]]:
        """Cross-project killed-or-passed nodes for the graveyard (S3).

        Pure store-level SQL over ``ap_nodes`` (no LLM): a node is rejected when
        the user triaged it ``passed``, its viability scored ≤ 40, or any
        pressure lens killed it (``pressure_test.killed > 0``). Returns each
        node with its project's domain (None if the project row is gone).
        Unparseable rows are skipped — degrade, don't crash.
        """
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT n.value, json_extract(p.value, '$.domain')
                   FROM ap_nodes n
                   LEFT JOIN ap_projects p ON p.id = n.project_id
                   WHERE json_extract(n.value, '$.triage') = 'passed'
                      OR (json_extract(n.value, '$.viability') IS NOT NULL
                          AND json_extract(n.value, '$.viability') <= 40)
                      OR COALESCE(json_extract(n.value, '$.pressure_test.killed'), 0) > 0"""
            ).fetchall()
        out: list[tuple[Node, Optional[str]]] = []
        for value, domain in rows:
            try:
                out.append((Node.model_validate(json.loads(value)), domain))
            except Exception:  # noqa: BLE001 - one bad row must not sink the list.
                continue
        return out

    def watched_nodes(self) -> list[tuple[Node, Optional[str]]]:
        """Cross-project nodes flagged for Space Watch sweeps (C2).

        Pure store-level SQL over ``ap_nodes`` (no LLM): every node whose
        ``watched`` flag is set, each paired with its project's domain (None
        if the project row is gone). Unparseable rows are skipped — degrade,
        don't crash.
        """
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT n.value, json_extract(p.value, '$.domain')
                   FROM ap_nodes n
                   LEFT JOIN ap_projects p ON p.id = n.project_id
                   WHERE json_extract(n.value, '$.watched')"""
            ).fetchall()
        out: list[tuple[Node, Optional[str]]] = []
        for value, domain in rows:
            try:
                out.append((Node.model_validate(json.loads(value)), domain))
            except Exception:  # noqa: BLE001 - one bad row must not sink the list.
                continue
        out.sort(key=lambda pair: pair[0].created_at)
        return out

    def scored_gaps(self) -> list[tuple[Node, Optional[str]]]:
        """Cross-project scored gap nodes — the H1 portfolio dataset.

        Pure store-level SQL over ``ap_nodes`` (no LLM): every node that has
        been promoted to ``kind == "gap"`` (pressure-tested + scored), each
        paired with its project's domain (None if the project row is gone).
        Unparseable rows are skipped — degrade, don't crash.
        """
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT n.value, json_extract(p.value, '$.domain')
                   FROM ap_nodes n
                   LEFT JOIN ap_projects p ON p.id = n.project_id
                   WHERE json_extract(n.value, '$.kind') = 'gap'"""
            ).fetchall()
        out: list[tuple[Node, Optional[str]]] = []
        for value, domain in rows:
            try:
                out.append((Node.model_validate(json.loads(value)), domain))
            except Exception:  # noqa: BLE001 - one bad row must not sink the list.
                continue
        out.sort(key=lambda pair: pair[0].created_at)
        return out

    def starred_nodes(self) -> list[tuple[Node, Optional[str]]]:
        """Cross-project nodes the USER starred — the shortlist (W-1).

        Pure store-level SQL over ``ap_nodes`` (no LLM). Reads ``user_star``,
        never ``star``: the engine's own threshold verdict is a measurement, not
        the founder's shortlist. Any node kind may be starred (a whole segment is
        a legitimate thing to keep), so this does not filter on ``kind``.
        Unparseable rows are skipped — degrade, don't crash.
        """
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT n.value, json_extract(p.value, '$.domain')
                   FROM ap_nodes n
                   LEFT JOIN ap_projects p ON p.id = n.project_id
                   WHERE json_extract(n.value, '$.user_star') = 1"""
            ).fetchall()
        out: list[tuple[Node, Optional[str]]] = []
        for value, domain in rows:
            try:
                out.append((Node.model_validate(json.loads(value)), domain))
            except Exception:  # noqa: BLE001 - one bad row must not sink the list.
                continue
        out.sort(key=lambda pair: pair[0].updated_at, reverse=True)
        return out

    def triaged_nodes(self) -> list[tuple[Node, Optional[str]]]:
        """Cross-project nodes carrying a triage verdict — the H3 distill corpus.

        Pure store-level SQL over ``ap_nodes`` (no LLM): every node the user
        marked interested or passed, each paired with its project's domain
        (None if the project row is gone). Unparseable rows are skipped —
        degrade, don't crash.
        """
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT n.value, json_extract(p.value, '$.domain')
                   FROM ap_nodes n
                   LEFT JOIN ap_projects p ON p.id = n.project_id
                   WHERE json_extract(n.value, '$.triage') IS NOT NULL"""
            ).fetchall()
        out: list[tuple[Node, Optional[str]]] = []
        for value, domain in rows:
            try:
                out.append((Node.model_validate(json.loads(value)), domain))
            except Exception:  # noqa: BLE001 - one bad row must not sink the list.
                continue
        out.sort(key=lambda pair: pair[0].created_at)
        return out

    # ------------------------------------------------------------------ #
    # Preferences (H3 — single ap_preferences row)                        #
    # ------------------------------------------------------------------ #
    def get_preferences(self) -> Optional[Preferences]:
        """The single learned-preferences row, or None (unset / unparseable)."""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM ap_preferences WHERE id=1"
            ).fetchone()
        if not row:
            return None
        try:
            return Preferences.model_validate(json.loads(row[0]))
        except Exception:  # noqa: BLE001 - degrade, don't crash.
            return None

    def save_preferences(self, prefs: Preferences) -> None:
        """Upsert the single learned-preferences row (id is always 1)."""
        payload = json.dumps(prefs.model_dump(mode="json"))
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ap_preferences (id, value) VALUES (1, ?)",
                (payload,),
            )

    # ------------------------------------------------------------------ #
    # Events (append-only log + live fan-out)                            #
    # ------------------------------------------------------------------ #
    def append_event(self, event: ExplorerEvent) -> int:
        """Assign a per-project monotonic ``seq``, persist, and fan out.

        Returns the assigned sequence number. Safe to call from any thread;
        live subscribers are notified on their own event loop.
        """
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT MAX(seq) FROM ap_events WHERE project_id=?",
                    (event.project_id,),
                ).fetchone()
                seq = (row[0] or 0) + 1
                event.seq = seq
                conn.execute(
                    "INSERT INTO ap_events (project_id, seq, value) VALUES (?, ?, ?)",
                    (event.project_id, seq, json.dumps(event.model_dump(mode="json"))),
                )
            subscribers = list(self._subscribers.get(event.project_id, ()))

        for queue, loop in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                # Loop already closed; the subscriber is gone. Ignore.
                pass
        return seq

    def events_since(self, project_id: str, after_seq: int) -> list[ExplorerEvent]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT value FROM ap_events WHERE project_id=? AND seq>? ORDER BY seq",
                (project_id, after_seq),
            ).fetchall()
        return [ExplorerEvent.model_validate(json.loads(r[0])) for r in rows]

    def last_watch_alert(self, project_id: str, node_id: str):
        """The newest ``watch_alert`` payload for one node, or None (C2).

        Store-level SQL over the event log; an unparseable row degrades to
        None rather than raising.
        """
        with self._lock, self._conn() as conn:
            row = conn.execute(
                """SELECT value FROM ap_events
                   WHERE project_id=?
                     AND json_extract(value, '$.type') = 'watch_alert'
                     AND json_extract(value, '$.alert.node_id') = ?
                   ORDER BY seq DESC LIMIT 1""",
                (project_id, node_id),
            ).fetchone()
        if not row:
            return None
        try:
            return ExplorerEvent.model_validate(json.loads(row[0])).alert
        except Exception:  # noqa: BLE001 - degrade, don't crash.
            return None

    def _last_seq(self, project_id: str) -> int:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(seq) FROM ap_events WHERE project_id=?", (project_id,)
            ).fetchone()
        return row[0] or 0

    # ------------------------------------------------------------------ #
    # Snapshot                                                           #
    # ------------------------------------------------------------------ #
    def snapshot(self, project_id: str) -> Optional[TreeSnapshot]:
        project = self.get_project(project_id)
        if project is None:
            return None
        return TreeSnapshot(
            project=project,
            nodes=self.get_nodes(project_id),
            last_seq=self._last_seq(project_id),
        )

    # ------------------------------------------------------------------ #
    # Live subscription                                                  #
    # ------------------------------------------------------------------ #
    async def subscribe(self, project_id: str) -> AsyncIterator[ExplorerEvent]:
        """Async-generate freshly-appended events for a project.

        Registers an ``asyncio.Queue`` bound to the caller's running loop and
        yields events as :meth:`append_event` pushes them. The queue is
        deregistered on generator close (client disconnect).
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        entry = (queue, loop)
        with self._lock:
            self._subscribers.setdefault(project_id, []).append(entry)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            with self._lock:
                subs = self._subscribers.get(project_id)
                if subs and entry in subs:
                    subs.remove(entry)
                    if not subs:
                        self._subscribers.pop(project_id, None)

    # ------------------------------------------------------------------ #
    # Deletion                                                           #
    # ------------------------------------------------------------------ #
    def delete_project(self, project_id: str) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute("DELETE FROM ap_projects WHERE id=?", (project_id,))
                conn.execute("DELETE FROM ap_nodes WHERE project_id=?", (project_id,))
                conn.execute("DELETE FROM ap_events WHERE project_id=?", (project_id,))
            self._subscribers.pop(project_id, None)


_store: Optional[TreeStore] = None
_store_lock = threading.Lock()


def get_store() -> TreeStore:
    """Return the process-wide :class:`TreeStore` singleton."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = TreeStore()
    return _store
