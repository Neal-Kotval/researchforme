"""The :class:`ExplorerService` — the per-project frontier loop (SPEC §4, §7).

This is the *conductor*. Everything sharp already lives in the modules it
composes — ``engine`` (the frontier + expansion), ``pressure`` (the adversarial
gauntlet + viability score), ``governor`` (usage-aware throttling), and ``store``
(event-sourced persistence). The service wires them into one long-running
``asyncio`` task per project that:

1. **Gates** every iteration through the shared :class:`UsageGovernor` so N
   projects cooperate on the subscription instead of stampeding a rate limit.
2. **Pops** the highest-value node off that project's :class:`Frontier`
   (best-first search — "come back to the good stuff", SPEC §4.1).
3. **Expands** it inside the governor's global concurrency slot — a cheap-model
   decomposition for structural nodes, or the full ``scope → fetch → extract →
   synthesize`` pipeline for a Segment (SPEC §4.2).
4. **Pressure-tests + scores** each ``GapCandidate`` the expansion yields
   (SPEC §5): a strong model drags it through the kill-lenses, the score turns
   into a viability + confidence, and it earns a ⭐ when it clears the project's
   threshold with real confidence. The candidate then *becomes* a scored ``Gap``.
5. **Meters** the (estimated) tokens spent back into the governor, updates the
   project stats, persists everything, and emits the events that drive the live
   SSE tree.
6. **Honours every stop condition** — token budget, node cap, time limit, a
   drained frontier, and the optional milestone check-ins — plus user
   pause/resume and the governor's usage pauses.

Same degrade-don't-crash contract as the rest of the codebase: the worker task
NEVER raises out. Any unexpected error parks the project in
:attr:`ProjectStatus.ERRORED` with a human-readable reason and the loop ends
cleanly. Because the whole pipeline degrades (no creds → mock sources, no LLM →
fixtures, unparseable LLM → deterministic fallbacks), the loop runs end-to-end
under ``LLM_BACKEND=fixture`` with zero credentials and still produces a tree of
scored, occasionally-starred gap nodes.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from ..analysis.synthesize import build_corroboration_tools
from ..llm.client import ClaudeClient, ToolSpec, get_client
from .engine import (
    Frontier,
    expand_segment,
    expand_structural,
    is_duplicate_title,
    kind_is_structural,
    make_node,
    node_priority,
    root_node,
)
from .evolve import (
    crossover_gaps,
    mutate_gap,
    select_crossover_pairs,
    select_mutation_targets,
)
from .fit import score_founder_fit
from .governor import UsageGovernor, get_governor
from .intake import steering_context_block
from .pressure import adversarial_self_critique, pressure_test, score_viability
from .semdedup import semantic_duplicate_of
from .schemas import (
    Budget,
    CreateProjectRequest,
    EventType,
    ExplorerEvent,
    ExplorerMode,
    Node,
    NodeKind,
    NodeState,
    Pace,
    Project,
    ProjectStats,
    ProjectStatus,
    Stage,
    SteeringContext,
    Triage,
)
from .store import TreeStore, get_store

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Rigor levels at which the pressure-test red team is allowed to spend real
# fetches corroborating a kill/rescue against live sources. ``light`` (the
# governor's curbing rigor) stays deliberately tool-free and cheap (SPEC §5).
_CORROBORATION_RIGORS = frozenset({"standard", "deep"})


def corroboration_tools_for(
    segment: Node, gap_sub_segment: str, project: Project, rigor: str
) -> tuple[Optional[list[ToolSpec]], dict[str, bool]]:
    """Live ``search_*`` tools for a pressure test, plus their shared url_sink.

    Wires the SPEC §5 corroboration seam: at ``standard``/``deep`` rigor the red
    team gets fresh-evidence tools scoped to the segment being explored (so a
    lens can actually verify "is the demand real?" against live Reddit/HN/etc).
    At ``light`` rigor — the governor's curbing mode — the tools are ``None`` so
    the test stays cheap and tool-free. The returned ``url_sink`` collects every
    tool-fetched URL -> whether its source ran LIVE, so ``pressure_test`` can
    stamp true per-source provenance on the evidence a lens cites (fixture data
    must never masquerade as live corroboration). Pure/deterministic, so it's
    unit-testable without an event loop.
    """
    url_sink: dict[str, bool] = {}
    if (rigor or "").strip().lower() not in _CORROBORATION_RIGORS:
        return None, url_sink
    # Scope: the specific segment title is the sharpest "area"; carry the gap's
    # sub-segment (and the project's) so fetches stay on-target.
    subs = [s for s in (gap_sub_segment, *project.sub_segments) if s]
    # include_pressure_only: the red team gets `search_outcomes` (who already
    # tried this and what happened to them) and `search_postmortems` on top of
    # the default mix. These adapters exist FOR this path — omitting the flag
    # left them reachable from nowhere, so the lenses that most need prior-art
    # were running without the two tools built for them.
    return (
        build_corroboration_tools(
            segment.title, subs, url_sink=url_sink, include_pressure_only=True
        ),
        url_sink,
    )


# --------------------------------------------------------------------------- #
# Tunables                                                                     #
# --------------------------------------------------------------------------- #
# Bounded idle while a project is user-/milestone-paused (seconds). The loop
# stays alive so a resume takes effect promptly, but spends nothing.
_IDLE_SLEEP = 0.5

# Rough per-call *base* token costs (prompt side) added on top of the estimated
# output size. The LLM client doesn't surface real usage counts, so this is an
# honest approximation, deliberately proportional to how heavy each stage is:
# structural decomposition is a small prompt, segment synthesis hands the model a
# dense signals payload, and each pressure gauntlet ships the gap + lens prompts.
_BASE_STRUCTURAL = 700
_BASE_SEGMENT = 2500
_BASE_PRESSURE = 1500
_BASE_FIT = 900  # founder-fit judge: steering block + gap payload, tiny output
_BASE_DIGEST = 900  # end-of-run digest: gap summaries + steering, small output
_BASE_PACK = 3500  # research pack: strong model over the full gap payload
_BASE_EVOLVE = 1800  # crossover/mutation operator: two-parent payload, one gap out

# Evolutionary recombination: how many generations to run once the top-down
# frontier drains, and how many offspring to attempt per generation. Bounded so a
# tree "spins" a while chasing non-obvious hybrids without running forever — the
# hard budget/node/time caps still apply on every loop iteration.
_MAX_GENERATIONS = 5
_OFFSPRING_PER_GEN = 6
# Viability at or above which a gap earns the expensive validation enrichment
# (value model + novelty scan), even if it didn't clear the ⭐ star bar (75). The
# honest score distribution clusters the strongest gaps in the high 60s/low 70s —
# just under the star — so a star-only gate would almost never run the very
# analysis the founder asked for. This floor targets the top ~2-3 gaps per tree.
_ENRICH_FLOOR = 62

# States a project rests in permanently until a user action restarts it.
_TERMINAL: frozenset[ProjectStatus] = frozenset(
    {
        ProjectStatus.EXHAUSTED,
        ProjectStatus.BUDGET_SPENT,
        ProjectStatus.TIME_LIMIT,
        ProjectStatus.ERRORED,
    }
)

# States where the loop idles and waits for the user (not the governor).
_USER_PAUSED: frozenset[ProjectStatus] = frozenset(
    {ProjectStatus.PAUSED, ProjectStatus.MILESTONE_PAUSED}
)

# Which terminal status each stop-reason maps onto. ``max_nodes`` is a budget
# ceiling too, so it lands on BUDGET_SPENT (its stop_reason string disambiguates).
_STOP_STATUS: dict[str, ProjectStatus] = {
    "budget_spent": ProjectStatus.BUDGET_SPENT,
    "max_nodes": ProjectStatus.BUDGET_SPENT,
    "time_limit": ProjectStatus.TIME_LIMIT,
    "exhausted": ProjectStatus.EXHAUSTED,
}


# --------------------------------------------------------------------------- #
# Per-project runtime (in-memory; the durable state lives in the TreeStore)    #
# --------------------------------------------------------------------------- #
@dataclass
class _Runtime:
    """The volatile, per-project state a running worker needs.

    Everything durable (project, nodes, events) is in the :class:`TreeStore`; the
    runtime just holds the live frontier, the worker task handle, the control
    ``stop`` flag (set on delete), the next milestone threshold, and the last
    governor mode we broadcast (so we only emit a project update when it changes).
    """

    frontier: Frontier = field(default_factory=Frontier)
    task: Optional[asyncio.Task] = None
    stop: bool = False
    next_milestone: int = 0
    last_mode: Optional[ExplorerMode] = None
    pending_start: bool = False  # start() was called with no running loop; retry later
    generations: int = 0  # evolutionary recombination rounds run after the frontier drained
    recombined: set[str] = field(default_factory=set)  # offspring node ids already minted


# --------------------------------------------------------------------------- #
# The service                                                                  #
# --------------------------------------------------------------------------- #
class ExplorerService:
    """Owns the worker task per project and the frontier loop that drives it."""

    def __init__(self, store: TreeStore, governor: UsageGovernor) -> None:
        self.store = store
        self.governor = governor
        self.client: ClaudeClient = get_client()
        self._runtimes: dict[str, _Runtime] = {}
        self._lock = threading.RLock()
        # Give the governor durable memory. This is the only place that holds
        # both, and without it the governor's 24h ledger dies with the process —
        # so the shared daily cap silently resets to zero on every restart and
        # can never bind.
        self.governor.attach_store(store)

    # ================================================================== #
    # Lifecycle: create / start                                          #
    # ================================================================== #
    def create(
        self, req: CreateProjectRequest, *, parent_project_id: Optional[str] = None
    ) -> Project:
        """Build a project + its DOMAIN root, persist both, and (maybe) start.

        Emits ``project_created`` and the root's ``node_added`` so a client that
        subscribes immediately sees a live, one-node tree. When
        ``req.autostart`` is set (the default) the worker task is spawned right
        away; otherwise the project rests in :attr:`ProjectStatus.PAUSED` until a
        ``resume`` control lands. ``parent_project_id`` records re-run lineage
        (C3) so the new run can later be diffed against its parent.
        """
        project_id = uuid.uuid4().hex
        budget = req.budget or Budget()
        project = Project(
            id=project_id,
            domain=req.domain,
            sub_segments=list(req.sub_segments or []),
            budget=budget,
            intake=dict(req.intake or {}),
            steering=req.steering or SteeringContext(),
            status=ProjectStatus.RUNNING if req.autostart else ProjectStatus.PAUSED,
            stats=ProjectStats(nodes=1, frontier_size=1, mode=ExplorerMode.PAUSED),
            parent_project_id=parent_project_id,
        )
        # Per-stage model policy (SPEC §13.4): cheap decompose, strong synth/test.
        if req.decompose_model:
            project.decompose_model = req.decompose_model
        if req.synth_model:
            project.synth_model = req.synth_model
        if req.pressure_model:
            project.pressure_model = req.pressure_model

        root = root_node(project)
        root.state = NodeState.QUEUED

        self.store.create_project(project)
        self.store.upsert_node(root)
        self._emit(project_id, EventType.PROJECT_CREATED, project=project)
        self._emit(project_id, EventType.NODE_ADDED, node=root)

        if req.autostart:
            self.start(project_id)
        return project

    def rerun(
        self,
        project_id: str,
        autostart: bool = False,
        steering: Optional[SteeringContext] = None,
    ) -> Project:
        """Clone a project into a fresh run linked back to its parent (C3).

        The new project copies the parent's domain, sub-segments, intake,
        budget, and per-stage model policy, records ``parent_project_id``, and
        starts only when ``autostart`` asks it to (default False — a re-run
        never spends tokens unbidden). Raises ``KeyError`` for an unknown
        parent.

        ``steering`` replaces the parent's steering on the clone; None clones it
        verbatim. This is the only way to correct a bad fence — a project's
        steering cannot be edited in place, so a wrong constraint otherwise
        follows every re-run forever.
        """
        parent = self._get_or_raise(project_id)
        amended = steering is not None
        req = CreateProjectRequest(
            domain=parent.domain,
            sub_segments=list(parent.sub_segments),
            budget=parent.budget.model_copy(deep=True),
            decompose_model=parent.decompose_model,
            synth_model=parent.synth_model,
            pressure_model=parent.pressure_model,
            intake=dict(parent.intake),
            steering=(
                steering.model_copy(deep=True)
                if amended
                else parent.steering.model_copy(deep=True)
            ),
            autostart=autostart,
        )
        project = self.create(req, parent_project_id=parent.id)
        note = " with amended steering" if amended else ""
        self._log(
            project.id, f"Re-run of project {parent.id} ('{parent.domain}'){note}."
        )
        return project

    def start(self, project_id: str) -> None:
        """Spawn the worker task for a project. Idempotent.

        A no-op if a task is already running; otherwise it schedules :meth:`_run`
        on the event loop. Resilient to being called without a running loop (e.g.
        from a sync/threadpool endpoint or a resume-on-boot hook): rather than
        crash the server with ``RuntimeError: no running event loop``, it marks
        the runtime pending so a later loop-bound caller can pick it up.

        CALLERS MUST BE ASYNC. ``pending_start`` is a tombstone, not a queue —
        nothing sweeps it, so a deferred start never happens on its own; it waits
        for the user to hit resume. This was silent for a long time: the sync
        ``rerun_project`` endpoint made ``autostart: true`` a no-op that still
        returned status "running", and three re-runs sat at 1 node / 0 tokens for
        18 minutes looking perfectly healthy. It now logs loudly, because a
        worker that never starts is indistinguishable from one that is merely
        slow — the worst kind of failure this codebase has.
        """
        rt = self._runtime(project_id)
        if rt.task is not None and not rt.task.done():
            return
        rt.stop = False
        rt.last_mode = None
        try:
            rt.task = asyncio.create_task(self._run(project_id))
            rt.pending_start = False
        except RuntimeError:
            # No running event loop in this context — defer instead of crashing.
            rt.task = None
            rt.pending_start = True
            logger.error(
                "start(%s) called with no running event loop — the worker did NOT "
                "start and nothing will retry it. The caller is a sync context "
                "(FastAPI runs `def` endpoints in a threadpool); make it `async "
                "def`. The project will sit at 0 tokens until resumed.",
                project_id,
            )
            self._log(
                project_id,
                "Worker could not start (no event loop) — resume to run it.",
            )

    def reconcile_on_boot(self) -> list[Project]:
        """Park projects persisted as RUNNING whose worker died with the process.

        Workers are in-process asyncio tasks, so a restart orphans any project
        stored mid-run: it would present as live ("sprinting") forever while
        nothing ticks. Auto-resuming instead is deliberately NOT done — a server
        boot must never start spending tokens on its own. The frontier is
        persisted, so a user Resume picks up exactly where the run stopped.
        Returns the projects it parked (empty on a healthy store).
        """
        parked: list[Project] = []
        for project in self.store.list_projects():
            if project.status is not ProjectStatus.RUNNING:
                continue
            rt = self._runtimes.get(project.id)
            if rt is not None and rt.task is not None and not rt.task.done():
                continue  # a live worker owns this project
            updated = self._mutate(
                project.id,
                lambda p: self._apply_status(p, ProjectStatus.PAUSED, mode=ExplorerMode.PAUSED),
            )
            if updated is not None:
                self._log(project.id, "Interrupted by a server restart — resume to continue.")
                parked.append(updated)
        return parked

    async def shutdown(self) -> None:
        """Stop every worker and close every SSE stream for a clean process exit.

        Without this a restart hangs: uvicorn waits on open SSE connections whose
        generators block forever on an empty queue, and worker tasks can be killed
        mid-write. We flag-stop each worker, cancel its task, briefly await the
        unwind, then sentinel-close the streams. Never raises."""
        tasks = []
        for rt in list(self._runtimes.values()):
            rt.stop = True
            if rt.task is not None and not rt.task.done():
                rt.task.cancel()
                tasks.append(rt.task)
        if tasks:
            try:
                await asyncio.wait(tasks, timeout=3)
            except Exception:  # noqa: BLE001 - best-effort unwind.
                pass
        self.store.close_all_subscribers()

    # ================================================================== #
    # Controls (SPEC §6.3)                                               #
    # ================================================================== #
    async def pause(self, project_id: str) -> Project:
        """User pause: park the project until a ``resume``. Runs are resumable, so
        pausing is free (the frontier is persisted)."""
        project = self._mutate(
            project_id,
            lambda p: self._apply_status(p, ProjectStatus.PAUSED, mode=ExplorerMode.PAUSED),
        )
        self._require(project, project_id)
        self._log(project_id, "Paused by user.")
        return project

    async def resume(self, project_id: str) -> Project:
        """Resume a paused/usage-paused/terminal project and ensure its worker runs."""
        def fn(p: Project) -> None:
            p.status = ProjectStatus.RUNNING
            p.stats.stop_reason = None
            p.stats.next_resume_at = None

        project = self._mutate(project_id, fn)
        self._require(project, project_id)
        self.start(project_id)
        self._log(project_id, "Resumed.")
        return project

    async def continue_milestone(self, project_id: str) -> Project:
        """Acknowledge a milestone check-in: advance the next threshold and run on."""
        rt = self._runtime(project_id)

        def fn(p: Project) -> None:
            p.status = ProjectStatus.RUNNING
            p.stats.stop_reason = None
            p.stats.next_resume_at = None
            m = p.budget.milestone_tokens
            if m > 0:
                # Advance to the next multiple strictly beyond current spend.
                rt.next_milestone = ((p.stats.tokens_spent // m) + 1) * m

        project = self._mutate(project_id, fn)
        self._require(project, project_id)
        self.start(project_id)
        self._log(project_id, "Milestone acknowledged — continuing.")
        return project

    def pin_node(self, project_id: str, node_id: str, pinned: bool) -> Project:
        """Pin/unpin a node — a user boost that jumps the frontier (SPEC §4.1).

        Recomputes the node's priority (``pinned`` adds a large boost in
        :func:`node_priority`) and persists it, so the node is prioritized on any
        future push and reads as pinned in the tree. The live in-memory frontier
        keeps its current ordering; the boost takes full effect on the next run
        that rebuilds the frontier.
        """
        node = self.store.get_node(node_id)
        if node is None or node.project_id != project_id:
            raise KeyError(f"unknown node {node_id!r} in project {project_id!r}")
        node.pinned = pinned
        parent = self.store.get_node(node.parent_id) if node.parent_id else None
        node.priority = node_priority(node, parent)
        node.updated_at = _now()
        self.store.upsert_node(node)
        self._emit(project_id, EventType.NODE_UPDATED, node=node)
        return self._get_or_raise(project_id)

    # ------------------------------------------------------------------ #
    # User sensors (Phase 2 S1/S2 + the C2 watched flag)                  #
    # ------------------------------------------------------------------ #
    def _owned_node(self, project_id: str, node_id: str) -> Node:
        """Fetch a node, insisting it belongs to this project (else KeyError)."""
        node = self.store.get_node(node_id)
        if node is None or node.project_id != project_id:
            raise KeyError(f"unknown node {node_id!r} in project {project_id!r}")
        return node

    def _touch_node(self, project_id: str, node: Node) -> Project:
        """Persist a sensor mutation and emit ``node_updated``."""
        node.updated_at = _now()
        self.store.upsert_node(node)
        self._emit(project_id, EventType.NODE_UPDATED, node=node)
        return self._get_or_raise(project_id)

    def set_triage(
        self,
        project_id: str,
        node_id: str,
        triage: Optional[Triage],
        triage_reason: str = "",
    ) -> Project:
        """Record the user's interested/passed verdict on a node (S1).

        ``triage=None`` clears the verdict; the reason is cleared with it — a
        reason without a verdict is meaningless downstream (graveyard,
        preference distillation).
        """
        node = self._owned_node(project_id, node_id)
        node.triage = triage
        node.triage_reason = triage_reason if triage is not None else ""
        return self._touch_node(project_id, node)

    def star_node(self, project_id: str, node_id: str, on: bool) -> Project:
        """Add/remove a node from the user's own starred shortlist.

        Writes ``Node.user_star`` — deliberately NOT ``Node.star``, which is the
        engine's viability-threshold verdict. The two are kept apart so the
        founder's taste and the engine's score stay independently readable.
        """
        node = self._owned_node(project_id, node_id)
        node.user_star = on
        return self._touch_node(project_id, node)

    def set_stage(
        self,
        project_id: str,
        node_id: str,
        stage: Optional[Stage],
        learnings: str = "",
    ) -> Project:
        """Move a gap through the look-into checklist (S2).

        ``stage=None`` clears the checklist position; ``learnings`` is always
        taken from the request so the user can keep notes even while clearing.
        """
        node = self._owned_node(project_id, node_id)
        node.stage = stage
        node.learnings = learnings
        return self._touch_node(project_id, node)

    def watch_node(self, project_id: str, node_id: str, watched: bool) -> Project:
        """Flag/unflag a node for Space Watch sweeps (C2 field only —
        the sweep itself lives in ``autonomous/watch.py``)."""
        node = self._owned_node(project_id, node_id)
        node.watched = watched
        return self._touch_node(project_id, node)

    # ------------------------------------------------------------------ #
    # Idle-headroom scavenger (Phase 3 C4) — manual, opt-in only          #
    # ------------------------------------------------------------------ #
    def continue_deepening(self, project_id: str) -> Project:
        """Deepen an exhausted run beneath its unexpanded starred branches (C4).

        Valid ONLY when every contract condition holds — the project opted in
        (``Budget.allow_idle_deepening``), it is terminal-exhausted, the
        governor reports ample headroom, and :func:`scavenger_candidates`
        found unexpanded starred gaps. Otherwise raises ``ValueError`` with
        the honest reason (the router's 409). When valid it queues one
        deepening SEGMENT under each candidate, resumes the run, and starts
        the worker — a manual button, never an automatic trigger.
        """
        from .scavenger import deepening_ineligible_reason, scavenger_candidates

        project = self._get_or_raise(project_id)
        candidates = scavenger_candidates(self.store, project_id)
        headroom = self.governor.headroom(project.budget, project.stats.tokens_spent)
        reason = deepening_ineligible_reason(project, headroom, candidates)
        if reason is not None:
            raise ValueError(reason)

        from .engine import make_node, node_priority

        queued = 0
        for gap in candidates:
            child = make_node(
                project_id,
                gap,
                NodeKind.SEGMENT,
                gap.title,
                rationale=f"Idle-headroom deepening beneath starred gap '{gap.title}'.",
                keywords=list(gap.keywords),
                depth=gap.depth + 1,
            )
            if self.store.get_node(child.id) is not None:
                continue  # already deepened (content-hash ids dedup for free)
            child.priority = node_priority(child, gap)
            self._save_node(child, EventType.NODE_ADDED)
            gap.child_ids = [*gap.child_ids, child.id]
            self._save_node(gap, EventType.NODE_UPDATED)
            queued += 1

        def fn(p: Project) -> None:
            p.status = ProjectStatus.RUNNING
            p.stats.stop_reason = None
            p.stats.next_resume_at = None
            p.stats.nodes += queued
            p.stats.frontier_size += queued

        updated = self._mutate(project_id, fn)
        self._require(updated, project_id)
        self.start(project_id)
        self._log(
            project_id,
            f"Idle deepening: queued {queued} segment(s) beneath starred branches.",
        )
        return updated

    # ------------------------------------------------------------------ #
    # Research Pack (Phase 4 H2)                                          #
    # ------------------------------------------------------------------ #
    async def research_pack(
        self, project_id: str, node_id: str, refresh: bool = False
    ) -> tuple[Node, bool]:
        """The gap's markdown research pack: ``(node, served_from_cache)``.

        Serves ``Node.research_pack`` when it exists (unless ``refresh``);
        otherwise makes ONE strong-model call, caches the pack on the node
        (persist + ``node_updated``), and meters the spend into the governor
        and the project stats. Raises ``KeyError`` for unknown/foreign nodes,
        ``ValueError`` for a node without a scored gap payload, and
        :class:`~app.autonomous.researchpack.ResearchPackUnavailable` when no
        backend can produce a real pack (the router's honest 503 — NEVER
        canned content).
        """
        from .researchpack import generate_research_pack

        node = self._owned_node(project_id, node_id)
        if node.gap is None:
            raise ValueError("Research packs are only available for scored gap nodes.")
        if node.research_pack and not refresh:
            return node, True

        project = self._get_or_raise(project_id)
        markdown = await generate_research_pack(node, project, self.client)

        est = self._est(_BASE_PACK, len(markdown))
        node.research_pack = markdown
        node.tokens_spent += est
        self._touch_node(project_id, node)
        self.governor.record_usage(est)

        def fn(p: Project) -> None:
            p.stats.tokens_spent += est

        self._mutate(project_id, fn)
        self._log(project_id, f"Research pack generated for '{node.title}'.")
        return node, False

    def set_budget(self, project_id: str, budget: Budget) -> Project:
        """Replace the project's budget (ceilings, pace, star threshold, milestones)."""
        project = self._mutate(project_id, lambda p: setattr(p, "budget", budget))
        self._require(project, project_id)
        self._log(project_id, "Budget updated.")
        return project

    def set_pace(self, project_id: str, pace: Pace) -> Project:
        """Spin the pace dial (``eco`` / ``balanced`` / ``sprint``)."""
        def fn(p: Project) -> None:
            p.budget.pace = pace

        project = self._mutate(project_id, fn)
        self._require(project, project_id)
        self._log(project_id, f"Pace set to {pace}.")
        return project

    def delete(self, project_id: str) -> None:
        """Cancel the worker and purge the project + its tree + event log."""
        with self._lock:
            rt = self._runtimes.pop(project_id, None)
        if rt is not None:
            rt.stop = True
            if rt.task is not None and not rt.task.done():
                rt.task.cancel()
        self.store.delete_project(project_id)

    # ================================================================== #
    # The frontier loop (SPEC §4) — the heart                            #
    # ================================================================== #
    async def _run(self, project_id: str) -> None:
        """Best-first expansion loop for one project. NEVER raises out.

        Reads the *fresh* project each iteration so user controls (pause, budget,
        pace) and the loop's own stat updates never clobber each other, gates on
        the shared governor, expands the top frontier node inside a concurrency
        slot, pressure-tests + scores any gap candidates it yields, meters the
        spend, and stops on the first condition that fires. On an unexpected
        error the project is parked ERRORED and the task ends cleanly.
        """
        rt = self._runtime(project_id)
        try:
            project = self.store.get_project(project_id)
            if project is None:
                return

            self._rebuild_frontier(rt, project_id)
            run_start = time.monotonic()

            # Initialise the next milestone threshold from current spend so a
            # resumed run doesn't immediately re-fire an already-passed milestone.
            m = project.budget.milestone_tokens
            spent0 = project.stats.tokens_spent
            rt.next_milestone = (((spent0 // m) + 1) * m) if m > 0 else 0

            while True:
                if rt.stop:
                    return
                project = self.store.get_project(project_id)
                if project is None:  # deleted out from under us.
                    return

                status = project.status
                if status in _TERMINAL:
                    return
                if status in _USER_PAUSED:
                    await asyncio.sleep(_IDLE_SLEEP)
                    continue

                budget = project.budget
                spent = project.stats.tokens_spent

                # Explicit stop conditions (never imply "done" when a cap fired).
                reason = self._stop_reason(project, run_start)
                if reason is not None:
                    await self._finish(project_id, reason)
                    return

                # Usage-aware gate: sleeps/backs off internally, returns the mode.
                mode = await self.governor.gate(budget, spent)
                if mode is ExplorerMode.PAUSED:
                    self._usage_pause(rt, project_id)
                    continue
                self._usage_active(rt, project_id, mode)

                first = rt.frontier.pop()
                if first is None:
                    extra = self._completeness_check(project)
                    if extra:
                        rt.frontier.push_all(extra)
                        continue
                    # The tree's top-down search has drained — every space worth
                    # branching has been branched. Before declaring the run
                    # exhausted, run an evolutionary GENERATION: cross and mutate
                    # the gap pool to reach ideas decomposition structurally
                    # cannot (non-obvious hybrids, contrarian twists). Offspring
                    # are scored through the same gauntlet; genuinely-new ones
                    # keep the run alive (loop-until-dry, capped).
                    produced = await self._recombine_generation(
                        rt, project, mode, spent
                    )
                    if produced > 0:
                        continue
                    await self._finish(
                        project_id,
                        ("exhausted", "Frontier exhausted — no more nodes to explore."),
                    )
                    return

                # "Maximize parallel agent activity": fan out only on the expensive
                # gap-yielding work — SEGMENT nodes each run a full synthesis +
                # adversarial pressure test. Batch consecutive top-priority segments
                # and expand them concurrently, each in its own shared governor slot
                # (the global semaphore still caps total concurrency across every
                # project). Cheap structural decomposition (domain/subarea) stays
                # serial so best-first priority can interleave segments between
                # branches (and tiny node budgets still reach the gap layer).
                batch: list[Node] = [first]
                if first.kind is NodeKind.SEGMENT:
                    width = max(1, self.governor.concurrency_for(budget))
                    while len(batch) < width:
                        nxt = rt.frontier.pop()
                        if nxt is None:
                            break
                        if nxt.kind is not NodeKind.SEGMENT:
                            rt.frontier.push(nxt)  # keep non-segments serial
                            break
                        batch.append(nxt)

                async def _one(target: Node) -> None:
                    async with self.governor.slot():
                        await self._expand_and_process(rt, project, target, mode, spent)

                # return_exceptions: one bad expansion can't sink the whole batch
                # (each _expand_and_process is already contractually non-raising).
                await asyncio.gather(*(_one(n) for n in batch), return_exceptions=True)

                self._maybe_milestone(rt, project_id)
        except Exception as exc:  # noqa: BLE001 - the worker must never raise out.
            self._errored(project_id, exc)

    # ------------------------------------------------------------------ #
    # Expansion of one node + inline scoring of its gap candidates        #
    # ------------------------------------------------------------------ #
    async def _expand_and_process(
        self,
        rt: _Runtime,
        project: Project,
        node: Node,
        mode: ExplorerMode,
        spent: int,
    ) -> None:
        """Expand ``node``, score any gap candidates, push structural children.

        Structural nodes (Domain/SubArea) decompose via the cheap model; a Segment
        runs the full synthesis pipeline. Each ``GapCandidate`` is pressure-tested
        (strong model, rigor from the governor), scored into a viability +
        confidence, ⭐-ed when it clears the threshold, and promoted to a ``Gap``
        leaf — never re-queued. Structural children are prioritized onto the
        frontier. All spend is estimated and metered. Never raises.
        """
        budget = project.budget

        node.state = NodeState.EXPANDING
        self._save_node(node, EventType.NODE_UPDATED)

        try:
            if node.kind is NodeKind.SEGMENT:
                # Anti-mode-collapse: hand this synthesis the gaps already proposed
                # anywhere in the tree so it won't photocopy the same "eval layer
                # for X" idea a 23rd time (see synthesize.avoid_titles).
                avoid_titles = self._proposed_gap_titles(project.id)
                children = await expand_segment(
                    node, project, self.client, project.synth_model,
                    avoid_titles=avoid_titles,
                )
                # Re-check against the store AFTER the await. Sibling segments
                # are expanded concurrently via asyncio.gather, and every one of
                # them read `avoid_titles` at the same instant above — before any
                # sibling had written a NODE_ADDED. So the pre-await snapshot
                # cannot see the batch's own output, and two branches reliably
                # mint the same idea ("The Verifiable-Environment Factory" and
                # "Verifier & Environment Foundry", both scored 91, both starred).
                # By now the siblings that finished first ARE in the store; drop
                # anything they already claimed. Cheap: one store read, versus an
                # Opus pressure test per duplicate.
                claimed = set(self._proposed_gap_titles(project.id)) - set(avoid_titles)
                if children and claimed:
                    children = [
                        c
                        for c in children
                        if c.kind is not NodeKind.GAP_CANDIDATE
                        or not is_duplicate_title(c.title, claimed)
                    ]
                # Then the synonym case, which no lexical rule can reach:
                # "Moody's for RL Environments" / "The Underwriters Lab for
                # Reward Models" / "Consumer Reports for RL Environments" are
                # one idea with ~zero token overlap. One cheap-model call per
                # candidate, here — before the Opus pressure test it would
                # otherwise pay for five times over. Fails open.
                children = await self._drop_semantic_duplicates(project, children)
            else:  # DOMAIN / SUBAREA — structural decomposition.
                # Thread the store handle so the S3 graveyard block (rejected
                # spaces across every project) reaches the decompose prompt.
                children = await expand_structural(
                    node, project, self.client, project.decompose_model,
                    store=self.store,
                )
        except Exception:  # noqa: BLE001 - expansion is contractually non-raising,
            children = []   # but guard anyway so one bad node can't stop the run.

        step_tokens = 0
        new_candidates = new_gaps = new_stars = 0
        max_viab = 0

        for child in children:
            if child.kind is NodeKind.GAP_CANDIDATE and child.gap is not None:
                new_candidates += 1
                cand_tokens, viability = await self._score_gap_candidate(
                    rt, project, node, child, budget, spent
                )
                step_tokens += cand_tokens
                new_gaps += 1
                if child.star:
                    new_stars += 1
                max_viab = max(max_viab, viability)
            else:
                # A structural child (SubArea/Segment) — queue it for expansion.
                child.state = NodeState.QUEUED
                self._save_node(child, EventType.NODE_ADDED)
                rt.frontier.push(child)

        # Estimate the expansion call's own spend from what it produced.
        if node.kind is NodeKind.SEGMENT:
            produced = sum(
                len(c.gap.model_dump_json()) for c in children if c.gap is not None
            )
            step_tokens += self._est(_BASE_SEGMENT, produced)
        else:
            produced = sum(
                len(c.title) + len(c.rationale) + sum(len(k) for k in c.keywords)
                for c in children
            )
            step_tokens += self._est(_BASE_STRUCTURAL, produced)

        node.state = NodeState.CHILDREN_READY
        node.child_ids = [c.id for c in children]
        node.tokens_spent = step_tokens
        self._save_node(node, EventType.NODE_UPDATED)

        # Meter the spend into the shared governor and roll up the project stats.
        self.governor.record_usage(step_tokens)
        new_nodes = len(children)
        frontier_size = len(rt.frontier)

        def _stats(p: Project) -> None:
            s = p.stats
            s.tokens_spent += step_tokens
            s.nodes += new_nodes
            s.candidates += new_candidates
            s.gaps += new_gaps
            s.stars += new_stars
            if max_viab > s.max_viability:
                s.max_viability = max_viab
            s.frontier_size = frontier_size
            s.mode = mode

        self._mutate(project_id=project.id, fn=_stats)
        self._log(
            project.id,
            f"Expanded {node.kind.value} '{node.title}' → {new_nodes} children "
            f"({new_gaps} scored gaps, {new_stars}★).",
        )

    async def _score_gap_candidate(
        self,
        rt: _Runtime,
        project: Project,
        parent_node: Node,
        child: Node,
        budget: Budget,
        spent: int,
    ) -> tuple[int, int]:
        """Drag ONE gap candidate through the full gauntlet and promote it.

        Pressure-test (rigor from the governor, live-corroboration tools at real
        rigor) → viability + confidence → adversarial self-critique → founder fit
        → ⭐ → promote ``GAP_CANDIDATE`` to ``GAP``. Mutates ``child`` in place and
        persists it. Returns ``(estimated_tokens, viability)``.

        Extracted so BOTH the segment-synthesis loop and the evolutionary
        recombination pass score offspring through the identical path — a bad
        crossover dies in the same gauntlet as any synthesized gap. Never raises
        (the callers already run under a guard).
        """
        # Show the candidate mid-gauntlet so the live tree can pulse it.
        child.state = NodeState.PRESSURE_TESTING
        self._save_node(child, EventType.NODE_ADDED)
        step_tokens = 0

        rigor = self.governor.rigor_for(budget, spent)
        # Wire the live-corroboration seam (SPEC §5): at standard/deep rigor the
        # red team can pull fresh evidence from real sources to verify a kill or
        # rescue; light rigor stays tool-free and cheap.
        tools, url_sink = corroboration_tools_for(
            parent_node, child.gap.sub_segment, project, rigor
        )
        test = await pressure_test(
            child.gap,
            child.rationale,
            self.client,
            project.pressure_model,
            rigor,
            tools=tools,
            url_sink=url_sink,
        )
        viability, confidence = score_viability(child.gap, test)

        # Adversarial self-critique (SPEC feature C): after scoring, a cheap
        # meta-pass records the single strongest reason the score is wrong. Gated
        # to real rigor so light/curbing stays cheap.
        if rigor in _CORROBORATION_RIGORS:
            test.self_critique = await adversarial_self_critique(
                child.gap, viability, test, self.client, project.pressure_model
            )

        # Founder fit (orthogonal to viability): one cheap-model call grading "is
        # this space for YOU" against the steering context. Skipped entirely (fit
        # stays None) when no steering was given; degrades to None on any
        # LLM/parse failure — never fabricated.
        if steering_context_block(project):
            fit, fit_reason = await score_founder_fit(
                child.gap, viability, project, self.client,
                project.decompose_model,
            )
            child.fit = fit
            child.fit_reason = fit_reason
            step_tokens += self._est(_BASE_FIT, len(fit_reason))

        child.pressure_test = test
        child.viability = viability
        child.confidence = confidence
        child.star = viability >= budget.star_threshold and confidence != "low"
        child.kind = NodeKind.GAP
        child.state = NodeState.SCORED
        self._save_node(child, EventType.NODE_UPDATED)

        step_tokens += self._est(_BASE_PRESSURE, len(test.model_dump_json()))

        # Winner-only validation enrichment (active-learning: expensive analysis
        # runs on the ideas that cleared the bar, not every candidate). Models the
        # status-quo cost + ROI delta and places the gap against funded incumbents.
        # Best-effort: never raises, never alters the calibrated viability.
        if (child.star or viability >= _ENRICH_FLOOR) and rigor in _CORROBORATION_RIGORS:
            step_tokens += await self._enrich_winner(project, child)

        return step_tokens, viability

    # ------------------------------------------------------------------ #
    # Stop conditions, milestones, completeness                           #
    # ------------------------------------------------------------------ #
    def _stop_reason(
        self, project: Project, run_start: float
    ) -> Optional[tuple[str, str]]:
        """Return ``(reason_key, message)`` for the first hard cap hit, else None."""
        b = project.budget
        s = project.stats
        if b.max_tokens and s.tokens_spent >= b.max_tokens:
            return ("budget_spent", f"Budget spent: {s.tokens_spent}/{b.max_tokens} tokens.")
        if b.max_nodes and s.nodes >= b.max_nodes:
            # The last expansion can overshoot the cap by a batch of children, so
            # never render the confusing "54/40" fraction — just name the cap.
            return ("max_nodes", f"Node cap reached ({b.max_nodes} nodes).")
        if b.time_limit_minutes and (time.monotonic() - run_start) >= b.time_limit_minutes * 60:
            return ("time_limit", f"Time limit reached: {b.time_limit_minutes} min.")
        return None

    def _maybe_milestone(self, rt: _Runtime, project_id: str) -> None:
        """If spend crossed the next milestone multiple, park for a keep-going tap."""
        project = self.store.get_project(project_id)
        if project is None:
            return
        m = project.budget.milestone_tokens
        if m <= 0 or rt.next_milestone <= 0:
            return
        if project.stats.tokens_spent < rt.next_milestone:
            return

        def fn(p: Project) -> None:
            if p.status is ProjectStatus.RUNNING:
                p.status = ProjectStatus.MILESTONE_PAUSED
            p.stats.mode = ExplorerMode.PAUSED
            p.stats.stop_reason = (
                f"Milestone check-in at {p.stats.tokens_spent} tokens — tap continue."
            )

        self._mutate(project_id, fn)
        self._log(project_id, "Milestone reached — awaiting a keep-going tap.")

    def _completeness_check(self, project: Project) -> list[Node]:
        """SPEC §4.3 hook — decide what's missing when the frontier drains.

        The frontier already dedups and holds every structural node worth
        expanding, so the honest answer here is "nothing" and the run is declared
        genuinely EXHAUSTED. Left as a seam for a richer LLM completeness critic
        (SPEC P5) that could re-inject skipped modalities.
        """
        return []

    async def _enrich_winner(self, project: Project, child: Node) -> int:
        """Attach value-model + novelty-scan to a starred gap. Returns est. tokens.

        Both degrade to None on any failure and leave the node's score untouched —
        this is decision-support the founder asked for (current-state cost → ROI
        delta) plus a reality check against funded incumbents, not a re-score.
        """
        from .novelty import novelty_scan
        from .valuemodel import model_value

        est = 0
        try:
            value, novelty = await asyncio.gather(
                model_value(child.gap, project, self.client, project.pressure_model),
                novelty_scan(child.gap, self.client, project.pressure_model),
                return_exceptions=True,
            )
        except Exception:  # noqa: BLE001 - enrichment must never break scoring.
            return 0
        if value is not None and not isinstance(value, BaseException):
            child.value_model = value.model_dump()
            est += self._est(_BASE_FIT, len(child.value_model.get("annual_value", "")))
        if novelty is not None and not isinstance(novelty, BaseException):
            child.novelty_scan = novelty.model_dump()
            est += self._est(_BASE_PRESSURE, len(novelty.model_dump().get("rationale", "")))
            # Novelty GATES the star (funnel): a gap can score high on viability and
            # still sit on top of a funded incumbent — the exact trap that made a
            # "92/open" idea die in the deep critique. If the (now incumbent-aware)
            # scan confirms the space is OCCUPIED, the engine's ⭐ is wrong, so pull
            # it. Conservative bar — only a confirmed "occupied" (the scan is still
            # a touch generous vs the 7-lens critique), so open/adjacent ideas keep
            # their star. Demote only; never promote on novelty.
            if child.star and (
                novelty.verdict == "occupied" or novelty.novelty_0_100 < 25
            ):
                child.star = False  # reason is self-evident from the attached scan.
                self._log(
                    project.id,
                    f"Unstarred '{child.gap.title[:48]}' — novelty scan found it "
                    f"occupied ({novelty.novelty_0_100}/100).",
                )
        if child.value_model or child.novelty_scan:
            self._save_node(child, EventType.NODE_UPDATED)
        return est

    # ------------------------------------------------------------------ #
    # Evolutionary recombination — crossover + mutation over the gap pool #
    # ------------------------------------------------------------------ #
    async def _recombine_generation(
        self, rt: _Runtime, project: Project, mode: ExplorerMode, spent: int
    ) -> int:
        """Run ONE evolutionary generation over the scored gap pool.

        Top-down decomposition only ever reaches ideas that enumeration finds —
        the obvious, already-occupied ones. This is the move it structurally
        cannot make: take the gaps already found and CROSS them (fuse two into a
        non-obvious hybrid) and MUTATE them (twist one along a creative axis —
        invert the buyer, flip the model, go contrarian). Parents are picked for
        viability AND novelty AND distance, so the pool diversifies instead of
        converging (quality-diversity).

        Offspring become ``GAP_CANDIDATE`` nodes scored through the exact same
        gauntlet as any synthesized gap (:meth:`_score_gap_candidate`), so a weak
        recombination dies like anything else. Returns the number of NEW scored
        gaps minted — a positive count keeps the run alive for another generation
        (loop-until-dry), zero lets it exhaust. Never raises.
        """
        # Bounded number of generations — the tree "spins" a while, not forever.
        if rt.generations >= _MAX_GENERATIONS:
            return 0
        # A fixture backend can't fuse or twist anything — it returns canned text.
        if getattr(self.client, "backend", "") == "fixture":
            return 0

        try:
            nodes = self.store.get_nodes(project.id)
        except Exception:  # noqa: BLE001 - a read failure must not stall the loop.
            return 0
        gap_nodes = [
            n for n in nodes
            if n.kind is NodeKind.GAP and n.gap is not None and n.viability is not None
        ]
        if len(gap_nodes) < 2:  # need a pool to recombine.
            return 0

        rt.generations += 1
        by_title = {n.gap.title.lower(): n for n in gap_nodes}
        scored = [(n.gap, n.viability) for n in gap_nodes]
        steering = steering_context_block(project) or ""
        model = project.synth_model

        # Split this generation's offspring budget between fusion and twisting.
        n_cross = max(1, _OFFSPRING_PER_GEN // 2)
        n_mut = _OFFSPRING_PER_GEN - n_cross
        pairs = select_crossover_pairs(scored, n_cross)
        muts = select_mutation_targets(scored, n_mut)

        self._log(
            project.id,
            f"Recombination gen {rt.generations}: crossing {len(pairs)} pair(s), "
            f"mutating {len(muts)} idea(s) from a pool of {len(gap_nodes)}.",
        )

        # Run every operator concurrently, each in a shared governor slot. Each
        # outcome is (origin, primary_parent, child, second_parent|None) — the
        # second parent lets a crossover offspring record both lineages.
        async def _cross(a: Gap, b: Gap):
            async with self.governor.slot():
                child = await crossover_gaps(a, b, self.client, model, steering)
            return ("crossover", a, child, b)

        async def _mut(g: Gap, strat: str):
            async with self.governor.slot():
                child = await mutate_gap(g, strat, self.client, model, steering)
            return (f"mut:{strat}", g, child, None)

        ops = [_cross(a, b) for a, b in pairs] + [_mut(g, s) for g, s in muts]
        outcomes = await asyncio.gather(*ops, return_exceptions=True)

        # Turn valid offspring into GAP_CANDIDATE nodes, deduped against the pool.
        proposed = {t.lower() for t in self._proposed_gap_titles(project.id)}
        candidates: list[Node] = []
        parent_of: dict[str, Node] = {}  # offspring node id -> its parent gap node
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                continue
            origin, parent_gap, child_gap, second_parent = outcome
            if child_gap is None:
                continue
            title_key = child_gap.title.strip().lower()
            if not title_key or title_key in proposed:
                continue  # duplicate name of something already in the tree.
            parent_node = by_title.get(parent_gap.title.lower())
            if parent_node is None:
                continue
            child = make_node(
                project.id, parent_node, NodeKind.GAP_CANDIDATE,
                child_gap.title,
                rationale=f"Evolutionary offspring ({origin}) of "
                          f"'{parent_gap.title}'. {child_gap.thesis}",
                keywords=list(child_gap.tags or []),
                depth=parent_node.depth + 1,
            )
            if child.id in rt.recombined:  # already minted in a prior generation.
                continue
            child.gap = child_gap
            # Record the second parent so the force graph can draw both lineages
            # of a crossover converging into the offspring.
            if second_parent is not None:
                sp = by_title.get(second_parent.title.lower())
                if sp is not None and sp.id != parent_node.id:
                    child.cross_parent_id = sp.id
            rt.recombined.add(child.id)
            proposed.add(title_key)
            parent_of[child.id] = parent_node
            candidates.append(child)

        if not candidates:
            return 0

        # Synonym dedup (cheap model) before paying Opus to red-team offspring.
        candidates = await self._drop_semantic_duplicates(project, candidates)
        if not candidates:
            return 0

        # Score each offspring through the identical gauntlet.
        budget = project.budget
        step_tokens = 0
        new_gaps = new_stars = 0
        max_viab = 0
        for child in candidates:
            parent_node = parent_of.get(child.id) or child
            try:
                cand_tokens, viability = await self._score_gap_candidate(
                    rt, project, parent_node, child, budget, spent
                )
            except Exception:  # noqa: BLE001 - one bad offspring can't sink the gen.
                continue
            step_tokens += cand_tokens + self._est(_BASE_EVOLVE, 0)
            new_gaps += 1
            if child.star:
                new_stars += 1
            max_viab = max(max_viab, viability)

        self.governor.record_usage(step_tokens)

        def _stats(p: Project) -> None:
            s = p.stats
            s.tokens_spent += step_tokens
            s.nodes += len(candidates)
            s.candidates += len(candidates)
            s.gaps += new_gaps
            s.stars += new_stars
            if max_viab > s.max_viability:
                s.max_viability = max_viab
            s.mode = mode

        self._mutate(project_id=project.id, fn=_stats)
        self._log(
            project.id,
            f"Recombination gen {rt.generations} → {new_gaps} offspring scored, "
            f"{new_stars}★ (best {max_viab}).",
        )
        return new_gaps

    # ------------------------------------------------------------------ #
    # Status transitions (all guarded, all through the store)             #
    # ------------------------------------------------------------------ #
    async def _finish(self, project_id: str, reason: tuple[str, str]) -> None:
        """Park a project in the terminal status for a fired stop condition,
        then write the end-of-run digest (H4)."""
        key, message = reason
        status = _STOP_STATUS.get(key, ProjectStatus.EXHAUSTED)

        def fn(p: Project) -> None:
            p.status = status
            p.stats.stop_reason = message
            p.stats.mode = ExplorerMode.PAUSED
            p.stats.next_resume_at = None
            p.stats.frontier_size = 0

        self._mutate(project_id, fn)
        self._log(project_id, f"Stopped: {message}")
        await self._write_digest(project_id)

    async def _write_digest(self, project_id: str) -> None:
        """End-of-run digest (H4): ONE cheap-model call, deterministic fallback.

        Steering-aware (the founder block rides in the prompt), metered into
        the governor, persisted on ``Project.digest``, and emitted via the
        ``project_updated`` that :meth:`_mutate` fires. NEVER raises — a
        terminal project must stay terminal, digest or not.
        """
        try:
            from .digest import build_digest

            project = self.store.get_project(project_id)
            if project is None:
                return
            digest = await build_digest(
                project, self.store.get_nodes(project_id), self.client
            )
            est = self._est(_BASE_DIGEST, len(str(digest)))
            self.governor.record_usage(est)

            def fn(p: Project) -> None:
                p.digest = digest
                p.stats.tokens_spent += est

            self._mutate(project_id, fn)
            self._log(
                project_id,
                "Run digest ready"
                + (" (deterministic fallback)." if digest.get("degraded") else "."),
            )
        except Exception:  # noqa: BLE001 - the digest must never un-finish a run.
            pass

    def _errored(self, project_id: str, exc: Exception) -> None:
        """Last-resort guard: surface an unexpected failure as ERRORED."""
        reason = f"{type(exc).__name__}: {str(exc)[:200]}"

        def fn(p: Project) -> None:
            p.status = ProjectStatus.ERRORED
            p.stats.stop_reason = reason
            p.stats.mode = ExplorerMode.PAUSED
            p.stats.next_resume_at = None

        try:
            self._mutate(project_id, fn)
            self._log(project_id, f"Errored: {reason}")
        except Exception:  # noqa: BLE001 - truly nothing else we can do.
            pass

    def _usage_pause(self, rt: _Runtime, project_id: str) -> None:
        """Governor says ``none`` headroom — reflect a usage pause (once per change)."""
        if rt.last_mode is ExplorerMode.PAUSED:
            return
        rt.last_mode = ExplorerMode.PAUSED

        def fn(p: Project) -> None:
            if p.status is ProjectStatus.RUNNING:
                p.status = ProjectStatus.USAGE_PAUSED
            p.stats.mode = ExplorerMode.PAUSED

        self._mutate(project_id, fn)
        self._log(project_id, "Usage-paused: near a limit — backing off.")

    def _usage_active(self, rt: _Runtime, project_id: str, mode: ExplorerMode) -> None:
        """Governor allows work — clear any usage pause and broadcast the mode.

        Only writes when the mode actually changed, so the SSE stream isn't
        flooded with a project update every single iteration.
        """
        if rt.last_mode is mode:
            return
        rt.last_mode = mode

        def fn(p: Project) -> None:
            if p.status is ProjectStatus.USAGE_PAUSED:
                p.status = ProjectStatus.RUNNING
                p.stats.stop_reason = None
            p.stats.mode = mode

        self._mutate(project_id, fn)

    @staticmethod
    def _apply_status(
        project: Project,
        status: ProjectStatus,
        *,
        mode: Optional[ExplorerMode] = None,
    ) -> None:
        project.status = status
        if mode is not None:
            project.stats.mode = mode

    # ------------------------------------------------------------------ #
    # Frontier reconstruction (resume-safe)                               #
    # ------------------------------------------------------------------ #
    def _rebuild_frontier(self, rt: _Runtime, project_id: str) -> None:
        """(Re)build the live frontier from persisted, still-QUEUED structural nodes.

        On a fresh run this picks up just the DOMAIN root; on a resume (or after a
        server restart) it re-seeds every structural node that hadn't been
        expanded yet, so nothing already completed is recomputed (SPEC §8).
        """
        frontier = Frontier()
        for node in self.store.get_nodes(project_id):
            if not kind_is_structural(node.kind):
                continue
            if node.state is not NodeState.QUEUED:
                continue
            if not node.priority:
                parent = (
                    self.store.get_node(node.parent_id) if node.parent_id else None
                )
                node.priority = node_priority(node, parent)
            frontier.push(node)
        rt.frontier = frontier

    # ================================================================== #
    # Read helpers / plumbing                                            #
    # ================================================================== #
    def _runtime(self, project_id: str) -> _Runtime:
        with self._lock:
            rt = self._runtimes.get(project_id)
            if rt is None:
                rt = _Runtime()
                self._runtimes[project_id] = rt
            return rt

    def _get_or_raise(self, project_id: str) -> Project:
        project = self.store.get_project(project_id)
        if project is None:
            raise KeyError(f"unknown project {project_id!r}")
        return project

    @staticmethod
    def _require(project: Optional[Project], project_id: str) -> Project:
        if project is None:
            raise KeyError(f"unknown project {project_id!r}")
        return project

    @staticmethod
    def _est(base: int, produced_len: int) -> int:
        """Estimate a call's token spend: a fixed prompt-side base + output size."""
        return max(1, int(base + produced_len / 4))

    # ------------------------------------------------------------------ #
    # Persistence + event emission                                        #
    # ------------------------------------------------------------------ #
    def _mutate(
        self, project_id: str, fn: Callable[[Project], None]
    ) -> Optional[Project]:
        """Read the *fresh* project, apply ``fn``, persist, and emit a project update.

        Re-reading under the lock (rather than mutating a long-lived object) is
        what keeps the loop's stat rollups from clobbering a concurrent user
        control — and vice-versa — across the ``await`` windows in the loop.
        Returns the updated project, or ``None`` if it has been deleted.
        """
        with self._lock:
            project = self.store.get_project(project_id)
            if project is None:
                return None
            fn(project)
            project.updated_at = _now()
            self.store.save_project(project)
        self._emit(project_id, EventType.PROJECT_UPDATED, project=project)
        return project

    def _proposed_gap_titles(self, project_id: str) -> list[str]:
        """Titles of every gap already proposed in this project's tree.

        Fed to synthesis as the do-not-repropose list (anti-mode-collapse). Cheap
        store read; the gap title is the human-facing thesis, which is exactly what
        a near-duplicate would echo. Newest first so the most recent proposals
        (most likely to be echoed) survive the 40-item cap in the prompt.
        """
        try:
            nodes = self.store.get_nodes(project_id)
        except Exception:  # noqa: BLE001 - a read failure must not stall expansion.
            return []
        gaps = [n for n in nodes
                if n.kind in (NodeKind.GAP, NodeKind.GAP_CANDIDATE)]
        gaps.sort(key=lambda n: n.created_at, reverse=True)
        seen: set[str] = set()
        titles: list[str] = []
        for n in gaps:
            t = (n.gap.title if n.gap else n.title or "").strip()
            key = t.lower()
            if t and key not in seen:
                seen.add(key)
                titles.append(t)
        return titles

    def _proposed_gap_ideas(self, project_id: str) -> list[tuple[str, str]]:
        """(title, thesis) for every gap already proposed — the semantic-dedup pool.

        Titles alone are not enough to judge sameness: "Moody's for X" and "The
        Underwriters Lab for X" share no words, and only their theses reveal
        they are one company. Newest first, matching _proposed_gap_titles.
        """
        try:
            nodes = self.store.get_nodes(project_id)
        except Exception:  # noqa: BLE001 - a read failure must not stall expansion.
            return []
        gaps = [n for n in nodes if n.kind in (NodeKind.GAP, NodeKind.GAP_CANDIDATE)]
        gaps.sort(key=lambda n: n.created_at, reverse=True)
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for n in gaps:
            title = (n.gap.title if n.gap else n.title or "").strip()
            key = title.lower()
            if not title or key in seen:
                continue
            seen.add(key)
            out.append((title, (n.gap.thesis if n.gap else "") or ""))
        return out

    async def _drop_semantic_duplicates(
        self, project: Project, children: list[Node]
    ) -> list[Node]:
        """Drop candidates that restate an idea already in the tree.

        Runs BEFORE the pressure test, on the cheap decompose model — the whole
        point is to not pay Opus five times to red-team five names for one
        company. Fails open on every error: keeping a duplicate costs a scroll,
        wrongly merging destroys an idea the founder never learns existed.
        """
        candidates = [c for c in children if c.kind is NodeKind.GAP_CANDIDATE]
        if not candidates:
            return children
        # A fixture backend cannot judge sameness — it returns the same canned
        # array for any prompt — so the call is pure cost. Checked ONCE here
        # rather than per candidate: this runs on every segment expansion, and a
        # per-candidate probe turned a 41s test suite into 165s and timed the
        # worker tests out.
        if getattr(self.client, "backend", "") == "fixture":
            return children

        pool = self._proposed_gap_ideas(project.id)
        dropped: set[str] = set()
        for child in candidates:
            gap = child.gap
            title = (gap.title if gap else child.title) or ""
            thesis = (gap.thesis if gap else "") or ""
            if not pool:
                pool.append((title, thesis))
                continue
            try:
                idx = await semantic_duplicate_of(
                    title, thesis, pool, self.client, project.decompose_model
                )
            except Exception:  # noqa: BLE001 - never break an expansion on dedup
                idx = None
            if idx is None:
                # Not a duplicate — it joins the pool so this batch's own
                # siblings are compared against it too.
                pool.append((title, thesis))
                continue
            dropped.add(child.id)
            self._log(
                project.id,
                f"Semantic duplicate dropped: '{title}' restates '{pool[idx][0]}'.",
            )
        if not dropped:
            return children
        return [c for c in children if c.id not in dropped]

    def _save_node(self, node: Node, event_type: EventType) -> None:
        """Persist a node and emit the matching ``node_added`` / ``node_updated``."""
        node.updated_at = _now()
        self.store.upsert_node(node)
        self._emit(node.project_id, event_type, node=node)

    def _emit(
        self,
        project_id: str,
        event_type: EventType,
        *,
        project: Optional[Project] = None,
        node: Optional[Node] = None,
        message: str = "",
    ) -> None:
        """Append one event to the project's log (which fans it out to SSE clients)."""
        self.store.append_event(
            ExplorerEvent(
                project_id=project_id,
                type=event_type,
                project=project,
                node=node,
                message=message,
            )
        )

    def _log(self, project_id: str, message: str) -> None:
        """Emit a human-readable progress line for the digest / activity feed."""
        self._emit(project_id, EventType.LOG, message=message)


# --------------------------------------------------------------------------- #
# Global singleton                                                            #
# --------------------------------------------------------------------------- #
_service: Optional[ExplorerService] = None
_service_lock = threading.Lock()


def get_service() -> ExplorerService:
    """Return the process-wide :class:`ExplorerService` bound to the shared
    :class:`TreeStore` and :class:`UsageGovernor` singletons."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = ExplorerService(get_store(), get_governor())
    return _service
