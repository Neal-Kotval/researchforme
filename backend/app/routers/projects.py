"""HTTP surface for Autonomous Exploration Mode (SPEC §7).

Every endpoint here is a thin, degrade-don't-crash wrapper around the
:class:`~app.autonomous.service.ExplorerService` (the frontier worker) and the
:class:`~app.autonomous.store.TreeStore` (the event-sourced persistence). The
router owns no logic of its own beyond request validation and turning the
service's state into JSON / Server-Sent Events.

Mounted under the same ``/api`` prefix as the analysis router, so the full set is:

* ``POST   /api/projects``                — create (and optionally autostart) a run.
* ``GET    /api/projects``                — list every project (newest first).
* ``GET    /api/projects/{pid}``          — one project's metadata + stats.
* ``GET    /api/projects/{pid}/tree``     — the full :class:`TreeSnapshot` to hydrate from.
* ``POST   /api/projects/{pid}/control``  — pause / resume / set-budget / set-pace / pin.
* ``DELETE /api/projects/{pid}``          — stop and forget a run.
* ``GET    /api/projects/{pid}/events``   — the live SSE event stream (§7).

The events endpoint is the interesting one: on connect it emits a ``snapshot``
event carrying the whole tree, optionally replays anything the client missed via
``?after=<seq>``, and then tails :meth:`TreeStore.subscribe` live. It is
disconnect-safe — the subscriber queue is always deregistered on client
disconnect. As everywhere else, unexpected failures return a clean 404/500 and
never leak a stack trace.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..autonomous.schemas import (
    ControlAction,
    ControlRequest,
    CreateProjectRequest,
    ExplorerEvent,
    IntakeRequest,
    IntakeResponse,
    Project,
    TreeSnapshot,
)
from ..autonomous.governor import get_governor
from ..autonomous.service import get_service
from ..autonomous.store import get_store

logger = logging.getLogger("gapfinder.api")

router = APIRouter(tags=["autonomous"])


# --------------------------------------------------------------------------- #
# Global usage (the always-on shared usage bar, SPEC §6/§10.3)                 #
# --------------------------------------------------------------------------- #
@router.get("/usage")
def global_usage() -> dict:
    """The shared governor's real global snapshot — spend, rate, mode, backoff.

    Drives the persistent global usage bar from measured numbers (not a guess),
    so several concurrent explorations show one honest, cooperating meter.
    """
    return get_governor().snapshot()


class UsagePolicyRequest(BaseModel):
    """Set the fleet-wide usage-shaping policy (SPEC §6)."""

    daily_cap_tokens: Optional[int] = None  # the "100%" reference; 0/None = unbounded
    limit_pct: Optional[float] = None       # target fraction of the cap, e.g. 0.95


@router.post("/usage/policy")
def set_usage_policy(req: UsagePolicyRequest) -> dict:
    """Set a dynamic usage limit; the governor auto-shapes spend around it."""
    gov = get_governor()
    gov.set_policy(daily_cap_tokens=req.daily_cap_tokens, limit_pct=req.limit_pct)
    return gov.snapshot()


# --------------------------------------------------------------------------- #
# CRUD                                                                        #
# --------------------------------------------------------------------------- #
@router.post("/projects/intake", response_model=IntakeResponse)
async def project_intake(req: IntakeRequest) -> IntakeResponse:
    """Generate a short set of preflight clarifying questions for a domain.

    Never fails hard — degrades to a solid static question set if the LLM can't
    help — so the intake step always returns something useful (SPEC feature A).
    """
    from ..autonomous.intake import generate_intake_questions
    from ..config import get_settings
    from ..llm.client import get_client

    model = get_settings().llm_model
    questions = await generate_intake_questions(req.domain, get_client(), model)
    return IntakeResponse(questions=questions)


@router.post("/projects", response_model=Project)
async def create_project(req: CreateProjectRequest) -> Project:
    """Create an autonomous exploration project and (by default) start it.

    Delegates to :meth:`ExplorerService.create`, which builds the ``Project`` +
    its DOMAIN root node, persists them, emits the initial events, and — when
    ``req.autostart`` is set — spawns the frontier worker task.
    """
    try:
        return get_service().create(req)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - never leak internals to the client.
        logger.exception("create_project failed for domain=%r", req.domain)
        raise HTTPException(
            status_code=500,
            detail=f"Could not create project: {type(exc).__name__}.",
        ) from exc


@router.get("/projects", response_model=list[Project])
def list_projects() -> list[Project]:
    """List every project (newest first), for the tab bar."""
    try:
        return get_store().list_projects()
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("list_projects failed")
        raise HTTPException(
            status_code=500,
            detail=f"Could not list projects: {type(exc).__name__}.",
        ) from exc


@router.get("/projects/{pid}", response_model=Project)
def get_project(pid: str) -> Project:
    """Fetch one project's metadata + live stats, or 404 if it's unknown."""
    project = get_store().get_project(pid)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


@router.get("/projects/{pid}/tree", response_model=TreeSnapshot)
def get_tree(pid: str) -> TreeSnapshot:
    """Return the full tree snapshot the UI hydrates from before subscribing."""
    snapshot = get_store().snapshot(pid)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return snapshot


@router.delete("/projects/{pid}")
async def delete_project(pid: str) -> dict:
    """Stop the worker (if running) and forget the project entirely."""
    if get_store().get_project(pid) is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    try:
        get_service().delete(pid)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("delete_project failed for pid=%r", pid)
        raise HTTPException(
            status_code=500,
            detail=f"Could not delete project: {type(exc).__name__}.",
        ) from exc
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Control (pause / resume / budget / pace / pin)                              #
# --------------------------------------------------------------------------- #
@router.post("/projects/{pid}/control", response_model=Project)
async def control_project(pid: str, req: ControlRequest) -> Project:
    """Apply a run-control action and return the updated project.

    Dispatches on ``req.action`` to the matching :class:`ExplorerService` method.
    Missing projects 404; requests that omit a field an action needs (e.g.
    ``set_budget`` without a ``budget``) 400. Pause/resume/continue are async on
    the service (they touch the worker task); the rest are synchronous mutations.
    """
    service = get_service()
    if get_store().get_project(pid) is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    try:
        action = req.action
        if action is ControlAction.PAUSE:
            return await service.pause(pid)
        if action is ControlAction.RESUME:
            return await service.resume(pid)
        if action is ControlAction.CONTINUE_MILESTONE:
            return await service.continue_milestone(pid)
        if action is ControlAction.SET_BUDGET:
            if req.budget is None:
                raise HTTPException(
                    status_code=400, detail="set_budget requires a 'budget'."
                )
            return service.set_budget(pid, req.budget)
        if action is ControlAction.SET_PACE:
            if req.pace is None:
                raise HTTPException(
                    status_code=400, detail="set_pace requires a 'pace'."
                )
            return service.set_pace(pid, req.pace)
        if action in (ControlAction.PIN_NODE, ControlAction.UNPIN_NODE):
            if not req.node_id:
                raise HTTPException(
                    status_code=400, detail=f"{action.value} requires a 'node_id'."
                )
            return service.pin_node(
                pid, req.node_id, action is ControlAction.PIN_NODE
            )
        # Enum coverage is exhaustive; guard anyway for forward-compat.
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}.")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("control_project failed for pid=%r action=%r", pid, req.action)
        raise HTTPException(
            status_code=500,
            detail=f"Control action failed: {type(exc).__name__}.",
        ) from exc


# --------------------------------------------------------------------------- #
# Live event stream (Server-Sent Events, §7)                                  #
# --------------------------------------------------------------------------- #
def _sse(data: str, event: str | None = None) -> str:
    """Frame a JSON payload as one SSE message (``data: …\\n\\n``)."""
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {data}\n\n"


async def _event_source(
    pid: str, after: Optional[int], request: Request
) -> AsyncIterator[str]:
    """Yield SSE frames: a ``snapshot`` event, replayed misses, then live events.

    Ordering matters for correctness. We register the live subscriber *first*
    (priming :meth:`TreeStore.subscribe` so its queue exists), then read the
    snapshot and replay ``events_since`` — so any event appended during setup is
    captured by the live queue rather than dropped. Live events are de-duplicated
    against the replayed range by ``seq``. The subscriber is always torn down in
    ``finally`` (``agen.aclose()`` runs the store's deregistration), making the
    stream safe against client disconnects.
    """
    store = get_store()

    # 1. Register the live subscriber before snapshotting (close the race window).
    agen = store.subscribe(pid).__aiter__()
    pending: asyncio.Task = asyncio.ensure_future(agen.__anext__())
    # Let the subscribe() body run up to its first `await queue.get()`, which is
    # where it registers the queue with the store.
    await asyncio.sleep(0)

    try:
        # 2. Snapshot the tree the client hydrates from.
        snapshot = store.snapshot(pid)
        if snapshot is None:
            # Deleted between the 404 guard and here; end the stream cleanly.
            return
        yield _sse(snapshot.model_dump_json(), event="snapshot")

        # 3. Replay anything the client missed. Baseline is the caller's ?after
        #    cursor when resuming, else the snapshot's own high-water mark.
        last_seq = after if after is not None else snapshot.last_seq
        for event in store.events_since(pid, last_seq):
            yield _sse(event.model_dump_json())
            last_seq = max(last_seq, event.seq)

        # 4. Tail the live stream, de-duping against the replayed range.
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(asyncio.shield(pending), timeout=15.0)
            except asyncio.TimeoutError:
                # Heartbeat so proxies keep the connection open and we can notice
                # a client disconnect on the next loop.
                yield ": keep-alive\n\n"
                continue
            pending = asyncio.ensure_future(agen.__anext__())
            if not isinstance(event, ExplorerEvent) or event.seq <= last_seq:
                continue
            last_seq = event.seq
            yield _sse(event.model_dump_json())
    finally:
        # Tear down the subscriber cleanly. The `pending` task is a live
        # `agen.__anext__()` in flight; we must let its cancellation *settle*
        # before `aclose()`, otherwise the generator is still "running" and
        # aclose() raises `RuntimeError: aclose(): asynchronous generator is
        # already running` (observed on client disconnect). Await the cancelled
        # task first, then close — guarding both so teardown never raises out.
        if not pending.done():
            pending.cancel()
        try:
            await pending
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001 - deregistration must never crash the stream
            pass


@router.get("/projects/{pid}/events")
async def stream_events(
    pid: str,
    request: Request,
    after: Optional[int] = Query(
        default=None, description="Replay events with seq greater than this cursor."
    ),
) -> StreamingResponse:
    """Live SSE stream of the project's exploration events (§7).

    Emits an initial ``snapshot`` event carrying the :class:`TreeSnapshot`, then
    (when ``?after=<seq>`` is supplied) replays missed events, then streams new
    ones live. 404 if the project doesn't exist.
    """
    if get_store().get_project(pid) is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    return StreamingResponse(
        _event_source(pid, after, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering for real-time SSE.
        },
    )
