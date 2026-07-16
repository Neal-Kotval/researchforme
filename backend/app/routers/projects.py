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
    GraveyardItem,
    IntakeRequest,
    IntakeResponse,
    PortfolioItem,
    Preferences,
    PreferencesState,
    Project,
    ProjectDiff,
    RerunRequest,
    ResearchPackResponse,
    ScoutRequest,
    ScoutResponse,
    SortResearchRequest,
    SortedResearch,
    TreeSnapshot,
    UpdatePreferencesRequest,
    WatchSweepResult,
    WatchedNodeStatus,
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
    """The shared governor's global snapshot — spend, rate, mode, backoff.

    Drives the persistent usage bar so concurrent explorations show one
    cooperating meter. Token counts are ESTIMATED from output length (the
    subscription LLM path surfaces no real usage), so treat them as a consistent
    relative gauge and pacing signal, not billing-accurate figures.
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
# Anti-portfolio graveyard (Phase 2 S3 + the S4 corpus merge)                  #
# --------------------------------------------------------------------------- #
@router.get("/graveyard", response_model=list[GraveyardItem])
def get_graveyard(
    q: str = Query(default="", description="Every-token-must-match text filter."),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[GraveyardItem]:
    """Cross-project list of rejected gaps, merged with the post-mortem corpus.

    Killed (any pressure-lens kill OR viability ≤ 40) or user-passed nodes from
    every project — pure store-level SQL, no LLM — plus curated post-mortem
    corpus entries flagged ``external: true``.
    """
    from ..autonomous.graveyard import graveyard_items

    try:
        return graveyard_items(get_store(), q=q, limit=limit)
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("get_graveyard failed")
        raise HTTPException(
            status_code=500,
            detail=f"Could not read the graveyard: {type(exc).__name__}.",
        ) from exc


# --------------------------------------------------------------------------- #
# Space Watch (Phase 3 C2)                                                     #
# --------------------------------------------------------------------------- #
@router.get("/watch", response_model=list[WatchedNodeStatus])
def get_watch() -> list[WatchedNodeStatus]:
    """Every watched node across projects with its most recent alert.

    Backs the dashboard "recent signals / movers" block. Pure store reads —
    no fetching, no LLM.
    """
    from ..autonomous.watch import get_watch_service

    try:
        return get_watch_service().watch_status()
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("get_watch failed")
        raise HTTPException(
            status_code=500,
            detail=f"Could not read the watch list: {type(exc).__name__}.",
        ) from exc


@router.post("/watch/sweep", response_model=WatchSweepResult)
def sweep_watch() -> WatchSweepResult:
    """Manually sweep every watched node's sources (source fetch only, NO LLM).

    Diffs against each node's baseline snapshot; a material shift (≥3 new
    items or a new regulatory/outcomes hit) emits a ``watch_alert`` event.
    Sync handler on purpose — FastAPI runs it in the threadpool, keeping the
    (network-bound) source fetches off the event loop.
    """
    from ..autonomous.watch import get_watch_service

    try:
        return get_watch_service().sweep()
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("sweep_watch failed")
        raise HTTPException(
            status_code=500,
            detail=f"Watch sweep failed: {type(exc).__name__}.",
        ) from exc


# --------------------------------------------------------------------------- #
# Preference distillation (Phase 4 H3)                                        #
# --------------------------------------------------------------------------- #
@router.get("/preferences", response_model=PreferencesState)
def get_preferences() -> PreferencesState:
    """The single learned-preferences row (or null) + the triage-verdict count.

    Pure store reads — no LLM. ``triage_count`` backs the dashboard "distill
    what your passes say" card threshold.
    """
    store = get_store()
    try:
        return PreferencesState(
            preferences=store.get_preferences(),
            triage_count=len(store.triaged_nodes()),
        )
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("get_preferences failed")
        raise HTTPException(
            status_code=500,
            detail=f"Could not read preferences: {type(exc).__name__}.",
        ) from exc


@router.post("/preferences", response_model=Preferences)
def update_preferences(req: UpdatePreferencesRequest) -> Preferences:
    """Review/edit/confirm/dismiss the learned preferences (H3).

    Confirm = ``status: "active"`` (the ONLY status ever injected into
    prompts, possibly with user-edited text); reject = ``status: "dismissed"``.
    Activating empty text is a 400 — there is nothing to inject.
    """
    text = req.learned_preferences.strip()
    if req.status == "active" and not text:
        raise HTTPException(
            status_code=400,
            detail="Cannot activate empty preferences — supply the text to apply.",
        )
    try:
        prefs = Preferences(learned_preferences=text, status=req.status)
        get_store().save_preferences(prefs)
        return prefs
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("update_preferences failed")
        raise HTTPException(
            status_code=500,
            detail=f"Could not save preferences: {type(exc).__name__}.",
        ) from exc


@router.post("/preferences/distill", response_model=Preferences)
async def distill_preferences_route() -> Preferences:
    """ONE cheap-model pass over the accumulated triage verdicts → a PENDING
    proposal (H3). Nothing is applied until the user confirms it.

    Zero triage verdicts is a 400 (there is nothing to distill from). A
    backend that can't produce a real distillation (the fixture backend
    included) is an honest 503 — NEVER an invented preference.
    """
    from ..autonomous.preferences import PreferencesUnavailable, distill_preferences
    from ..llm.client import get_client

    store = get_store()
    triaged = store.triaged_nodes()
    if not triaged:
        raise HTTPException(
            status_code=400,
            detail="No triage verdicts yet — mark some gaps interested/passed first.",
        )
    try:
        text = await distill_preferences(triaged, get_client())
        prefs = Preferences(learned_preferences=text, status="pending")
        store.save_preferences(prefs)
        return prefs
    except PreferencesUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("distill_preferences failed")
        raise HTTPException(
            status_code=500,
            detail=f"Could not distill preferences: {type(exc).__name__}.",
        ) from exc


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
    questions = await generate_intake_questions(req.domain, get_client(), model, brief=req.brief)
    return IntakeResponse(questions=questions)


@router.post("/projects/scout", response_model=ScoutResponse)
async def scout_projects(req: ScoutRequest) -> ScoutResponse:
    """Propose candidate domains from what is trending right now (Scout mode).

    Domainless discovery: one cheap wide pass over all source adapters + one
    cheap-model (decompose-tier) LLM call. Never fails hard — degrades to
    deterministic token-cluster candidates flagged ``degraded`` — so the
    suggested-spaces panel always returns something useful (STRATEGY Phase 1 §5).
    """
    from ..autonomous.scout import DECOMPOSE_MODEL, scout_spaces
    from ..llm.client import get_client

    return await scout_spaces(req.brief, req.avoid, get_client(), DECOMPOSE_MODEL)


@router.post("/projects/sort-research", response_model=SortedResearch)
async def sort_research_route(req: SortResearchRequest) -> SortedResearch:
    """Sort a raw wall of the founder's own research into a launchable job.

    Infers a domain, concrete sub-segments, and a tight brief from the paste, and
    preserves the original text as steering ``research``. Never fails hard —
    degrades to preserving the paste with an empty domain (SPEC bulk-paste).
    """
    from ..autonomous.intake import sort_research
    from ..config import get_settings
    from ..llm.client import get_client

    model = get_settings().llm_model
    return await sort_research(req.text, get_client(), model)


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


@router.get("/portfolio", response_model=list[PortfolioItem])
def get_portfolio() -> list[PortfolioItem]:
    """Every scored gap across every project (H1) — the 2×2 scatter dataset.

    Pure store-level rollup, no LLM. ``fit`` is null for gaps scored without
    steering; the frontend renders those separately, never faked onto the plot.
    """
    from ..autonomous.portfolio import portfolio_items

    try:
        return portfolio_items(get_store())
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("get_portfolio failed")
        raise HTTPException(
            status_code=500,
            detail=f"Could not read the portfolio: {type(exc).__name__}.",
        ) from exc


@router.get("/starred", response_model=list[PortfolioItem])
def get_starred() -> list[PortfolioItem]:
    """Every idea the USER starred, across every project (W-1) — the shortlist.

    Reads ``user_star``, never the engine's ``star``: this is the founder's own
    taste, and it is the entry point for importing ideas into a project. Pure
    store-level rollup, no LLM.
    """
    from ..autonomous.portfolio import starred_items

    try:
        return starred_items(get_store())
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("get_starred failed")
        raise HTTPException(
            status_code=500,
            detail=f"Could not read the starred ideas: {type(exc).__name__}.",
        ) from exc


@router.post("/projects/{pid}/rerun", response_model=Project)
def rerun_project(pid: str, req: RerunRequest | None = None) -> Project:
    """Clone a project into a fresh linked run (C3).

    Same domain, sub-segments, intake, budget, and model policy;
    ``parent_project_id`` records the lineage for the diff view. Starts only
    when the request says ``autostart: true`` (default false — a re-run never
    spends tokens unbidden).

    Pass ``steering`` to amend the clone's steering — the supported way to
    correct a bad brief/constraint/avoid, since steering is write-once on a
    project. Omit it to clone the parent's steering verbatim.
    """
    try:
        return get_service().rerun(
            pid,
            autostart=bool(req and req.autostart),
            steering=(req.steering if req else None),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("rerun_project failed for pid=%r", pid)
        raise HTTPException(
            status_code=500,
            detail=f"Could not re-run project: {type(exc).__name__}.",
        ) from exc


@router.get("/projects/{pid}/diff", response_model=ProjectDiff)
def diff_project(
    pid: str,
    against: str = Query(min_length=1, description="Baseline project id to diff against."),
) -> ProjectDiff:
    """Node-level diff of two runs' scored gaps by normalized title (C3).

    ``new``/``gone``/``moved`` with viability + fit deltas — pure store
    computation, no LLM. 404 when either project is unknown.
    """
    from ..autonomous.diff import project_diff

    store = get_store()
    if store.get_project(pid) is None or store.get_project(against) is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    try:
        return project_diff(store, pid, against)
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("diff_project failed for pid=%r against=%r", pid, against)
        raise HTTPException(
            status_code=500,
            detail=f"Could not diff projects: {type(exc).__name__}.",
        ) from exc


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
# Research Pack (Phase 4 H2)                                                  #
# --------------------------------------------------------------------------- #
@router.post(
    "/projects/{pid}/nodes/{nid}/research-pack", response_model=ResearchPackResponse
)
async def research_pack(
    pid: str,
    nid: str,
    refresh: int = Query(default=0, description="1 = regenerate, bypassing the cache."),
) -> ResearchPackResponse:
    """The gap's markdown research pack — cached on the node, ONE strong-model call.

    Serves ``Node.research_pack`` when present (``?refresh=1`` regenerates).
    Honest degrade: when no LLM backend can produce a real pack (the fixture
    backend included) this returns 503 with the reason — NEVER canned content.
    """
    from ..autonomous.researchpack import ResearchPackUnavailable

    if get_store().get_project(pid) is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    try:
        node, cached = await get_service().research_pack(pid, nid, refresh=bool(refresh))
        return ResearchPackResponse(
            node_id=node.id, markdown=node.research_pack, cached=cached
        )
    except ResearchPackUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Node not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - clean 500, no stack trace.
        logger.exception("research_pack failed for pid=%r nid=%r", pid, nid)
        raise HTTPException(
            status_code=500,
            detail=f"Could not build the research pack: {type(exc).__name__}.",
        ) from exc


# --------------------------------------------------------------------------- #
# Control (pause / resume / budget / pace / pin / triage / stage / watch)     #
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
        if action is ControlAction.SET_TRIAGE:
            if not req.node_id:
                raise HTTPException(
                    status_code=400, detail="set_triage requires a 'node_id'."
                )
            return service.set_triage(pid, req.node_id, req.triage, req.triage_reason)
        if action is ControlAction.SET_STAGE:
            if not req.node_id:
                raise HTTPException(
                    status_code=400, detail="set_stage requires a 'node_id'."
                )
            return service.set_stage(pid, req.node_id, req.stage, req.learnings)
        if action in (ControlAction.STAR_NODE, ControlAction.UNSTAR_NODE):
            if not req.node_id:
                raise HTTPException(
                    status_code=400, detail=f"{action.value} requires a 'node_id'."
                )
            return service.star_node(
                pid, req.node_id, action is ControlAction.STAR_NODE
            )
        if action in (ControlAction.WATCH_NODE, ControlAction.UNWATCH_NODE):
            if not req.node_id:
                raise HTTPException(
                    status_code=400, detail=f"{action.value} requires a 'node_id'."
                )
            return service.watch_node(
                pid, req.node_id, action is ControlAction.WATCH_NODE
            )
        if action is ControlAction.CONTINUE_DEEPENING:
            # C4: valid ONLY when the contract conditions hold (opt-in +
            # exhausted + ample headroom + unexpanded starred branches);
            # otherwise a 409 carrying the honest reason.
            try:
                return service.continue_deepening(pid)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
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
