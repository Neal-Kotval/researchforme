"""Data contracts for Autonomous Exploration Mode.

This is the single source of truth for the exploration tree, projects, budgets,
pressure tests, and the event stream. It reuses the existing `Gap` object from
``app.schemas`` verbatim at the leaf nodes — autonomous mode is a *driver* around
the existing synthesis pipeline, not a rewrite.

See SPEC-AUTONOMOUS.md for the design rationale. Keep frontend
``src/autonomous/types.ts`` in sync with these models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..schemas import Evidence, Gap, SourceReport


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Node taxonomy                                                               #
# --------------------------------------------------------------------------- #
class NodeKind(str, Enum):
    DOMAIN = "domain"          # the root of a project
    SUBAREA = "subarea"        # a specialization of the domain
    SEGMENT = "segment"        # a concrete segment we can run the pipeline on
    GAP_CANDIDATE = "gap_candidate"  # a hypothesized gap, pre-pressure-test
    GAP = "gap"                # a pressure-tested, scored gap


class NodeState(str, Enum):
    QUEUED = "queued"
    EXPANDING = "expanding"
    CHILDREN_READY = "children_ready"
    SYNTHESIZING = "synthesizing"
    PRESSURE_TESTING = "pressure_testing"
    SCORED = "scored"
    PRUNED = "pruned"
    ERRORED = "errored"


Confidence = Literal["low", "medium", "high"]
TestRigor = Literal["light", "standard", "deep"]


# --------------------------------------------------------------------------- #
# Pressure testing                                                            #
# --------------------------------------------------------------------------- #
class LensVerdict(BaseModel):
    """One adversarial lens's attempt to kill a gap."""

    lens: str                                  # e.g. "empty_for_a_reason"
    verdict: Literal["survives", "weakens", "kills"]
    argument: str                              # why it survives / weakens / dies
    evidence: list[Evidence] = Field(default_factory=list)


class PressureTest(BaseModel):
    lenses: list[LensVerdict] = Field(default_factory=list)
    survived: int = 0
    weakened: int = 0
    killed: int = 0
    test_rigor: TestRigor = "standard"
    summary: str = ""                          # one-line verdict of the gauntlet
    self_critique: str = ""                    # strongest reason the score is wrong


# --------------------------------------------------------------------------- #
# The tree node                                                               #
# --------------------------------------------------------------------------- #
# User-sensor vocabularies (Phase 2, docs/strategy/phase234-build.md S1/S2).
# Triage is the cheap interested/pass verdict; Stage tracks a gap the user is
# actively looking into. Both mirror types.ts.
Triage = Literal["interested", "passed"]
Stage = Literal[
    "found", "interviewing", "smoke_testing", "verdict_build", "verdict_pass"
]


class Node(BaseModel):
    id: str
    project_id: str
    parent_id: Optional[str] = None
    kind: NodeKind
    state: NodeState = NodeState.QUEUED

    title: str
    rationale: str = ""                        # why this branch might matter
    keywords: list[str] = Field(default_factory=list)
    depth: int = 0
    priority: float = 0.0                      # frontier ordering (higher first)

    # Leaf payload (gap nodes only).
    gap: Optional[Gap] = None
    viability: Optional[int] = None            # 0..100, post pressure-test
    confidence: Optional[Confidence] = None
    pressure_test: Optional[PressureTest] = None
    # Founder fit (orthogonal to viability): 0..100 "is this space for YOU",
    # scored from the project's steering context. None = no steering provided
    # or scoring unavailable — never fabricated. Mirrors types.ts.
    fit: Optional[int] = None
    fit_reason: str = ""
    star: bool = False
    pinned: bool = False                       # user-pinned (boosts priority)

    # User sensors (S1/S2/C2) — set only via control actions, never by the LLM.
    triage: Optional[Triage] = None            # interested/passed; None = untriaged
    triage_reason: str = ""                    # taxonomy slug or free text
    stage: Optional[Stage] = None              # look-into checklist position
    learnings: str = ""                        # what the user found out so far
    watched: bool = False                      # Space Watch sweeps re-check this node

    # Bookkeeping.
    child_ids: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    tokens_spent: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# --------------------------------------------------------------------------- #
# Budget & project                                                            #
# --------------------------------------------------------------------------- #
Pace = Literal["eco", "balanced", "sprint"]


class Budget(BaseModel):
    max_tokens: Optional[int] = None           # hard ceiling for the run
    daily_cap_tokens: Optional[int] = None      # shared across projects
    max_nodes: Optional[int] = 400
    time_limit_minutes: Optional[int] = None
    pace: Pace = "balanced"
    star_threshold: int = Field(default=75, ge=0, le=100)
    # Milestone check-ins: pause every N tokens for a one-tap continue (0 = off).
    milestone_tokens: int = 0


class ProjectStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"                          # by user
    USAGE_PAUSED = "usage_paused"              # by the governor (rate/limit)
    MILESTONE_PAUSED = "milestone_paused"      # awaiting a keep-going tap
    EXHAUSTED = "exhausted"                    # frontier genuinely empty
    BUDGET_SPENT = "budget_spent"
    TIME_LIMIT = "time_limit"
    ERRORED = "errored"


class ExplorerMode(str, Enum):
    SPRINTING = "sprinting"
    CURBING = "curbing"
    PAUSED = "paused"


class ProjectStats(BaseModel):
    nodes: int = 0
    gaps: int = 0
    candidates: int = 0
    stars: int = 0
    tokens_spent: int = 0
    max_viability: int = 0
    frontier_size: int = 0
    mode: ExplorerMode = ExplorerMode.PAUSED
    next_resume_at: Optional[datetime] = None
    stop_reason: Optional[str] = None


class SteeringContext(BaseModel):
    """Rich founder context that steers every LLM step of an exploration.

    All fields optional: an empty ``SteeringContext`` renders to nothing and the
    run behaves exactly as before. ``brief`` is the big free-paste box; the lists
    are the structured fields; ``research`` holds pasted prior research that a
    run can be seeded from (bulk-paste intake).
    """

    brief: str = Field(default="", max_length=8000)
    advantages: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    time_horizon: str = ""
    research: str = Field(default="", max_length=20000)


class Project(BaseModel):
    id: str
    domain: str
    sub_segments: list[str] = Field(default_factory=list)
    steering: SteeringContext = Field(default_factory=SteeringContext)
    # Mixed-model policy: cheap model for decomposition, strong for pressure-test.
    decompose_model: str = "claude-haiku-4-5-20251001"
    synth_model: str = "claude-opus-4-8"
    pressure_model: str = "claude-opus-4-8"
    status: ProjectStatus = ProjectStatus.PAUSED
    budget: Budget = Field(default_factory=Budget)
    stats: ProjectStats = Field(default_factory=ProjectStats)
    # Preflight intake answers (question → answer) that steer decomposition/scope.
    intake: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# --------------------------------------------------------------------------- #
# Event stream (event-sourced tree → live SSE + resumability)                 #
# --------------------------------------------------------------------------- #
class EventType(str, Enum):
    PROJECT_CREATED = "project_created"
    PROJECT_UPDATED = "project_updated"        # status / stats change
    NODE_ADDED = "node_added"
    NODE_UPDATED = "node_updated"              # state / score / star change
    NODE_PRUNED = "node_pruned"
    LOG = "log"                                # human-readable progress line


class ExplorerEvent(BaseModel):
    seq: int = 0                               # monotonic per project
    project_id: str
    type: EventType
    at: datetime = Field(default_factory=_now)
    node: Optional[Node] = None                # for node_* events
    project: Optional[Project] = None          # for project_* events
    message: str = ""                          # for log events


class TreeSnapshot(BaseModel):
    """Full state the UI hydrates from before subscribing to the event stream."""

    project: Project
    nodes: list[Node] = Field(default_factory=list)
    last_seq: int = 0


# --------------------------------------------------------------------------- #
# API request models                                                          #
# --------------------------------------------------------------------------- #
class CreateProjectRequest(BaseModel):
    domain: str = Field(min_length=2, max_length=200)
    sub_segments: list[str] = Field(default_factory=list)
    budget: Optional[Budget] = None
    decompose_model: Optional[str] = None
    synth_model: Optional[str] = None
    pressure_model: Optional[str] = None
    intake: dict[str, str] = Field(default_factory=dict)  # preflight answers
    steering: Optional[SteeringContext] = None            # rich founder context
    autostart: bool = True


class IntakeQuestion(BaseModel):
    """One preflight clarifying question with a few suggested answers."""

    question: str
    suggestions: list[str] = Field(default_factory=list)


class IntakeRequest(BaseModel):
    domain: str = Field(min_length=2, max_length=200)
    brief: str = Field(default="", max_length=8000)  # optional context to sharpen Qs


class IntakeResponse(BaseModel):
    questions: list[IntakeQuestion] = Field(default_factory=list)


class SortResearchRequest(BaseModel):
    """A raw wall of the founder's own research to be sorted into a job."""

    text: str = Field(min_length=1, max_length=20000)


class SortedResearch(BaseModel):
    """A research paste sorted into a ready-to-launch exploration job."""

    domain: str = ""
    sub_segments: list[str] = Field(default_factory=list)
    brief: str = ""
    research: str = ""  # the original paste, preserved verbatim for steering


class ScoutRequest(BaseModel):
    """Ask the engine to propose ownable spaces from what is hot right now."""

    brief: str = Field(default="", max_length=8000)   # optional founder context
    avoid: list[str] = Field(default_factory=list)    # spaces to exclude


class ScoutSignal(BaseModel):
    """One trending item that triggered a scout candidate — always from the
    supplied input set (grounding discipline: never LLM-invented)."""

    source: str
    title: str
    url: str


class ScoutCandidate(BaseModel):
    """A candidate DOMAIN shaped like an ownable space, with its trigger signals."""

    domain: str
    rationale: str
    signals: list[ScoutSignal] = Field(default_factory=list)
    suggested_sub_segments: list[str] = Field(default_factory=list)
    degraded: bool = False  # True when produced by the deterministic fallback


class ScoutResponse(BaseModel):
    """Stateless scout result: candidates + per-source telemetry. Not persisted."""

    candidates: list[ScoutCandidate] = Field(default_factory=list)
    sources: list[SourceReport] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_now)


class ControlAction(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    CONTINUE_MILESTONE = "continue_milestone"  # acknowledge a milestone check-in
    SET_BUDGET = "set_budget"
    SET_PACE = "set_pace"
    PIN_NODE = "pin_node"
    UNPIN_NODE = "unpin_node"
    SET_TRIAGE = "set_triage"                  # S1: interested/passed (+reason)
    SET_STAGE = "set_stage"                    # S2: look-into checklist (+learnings)
    WATCH_NODE = "watch_node"                  # C2: flag for Space Watch sweeps
    UNWATCH_NODE = "unwatch_node"


class ControlRequest(BaseModel):
    action: ControlAction
    budget: Optional[Budget] = None
    pace: Optional[Pace] = None
    node_id: Optional[str] = None
    # set_triage payload — triage=None clears the verdict (and its reason).
    triage: Optional[Triage] = None
    triage_reason: str = ""
    # set_stage payload — stage=None clears the checklist position.
    stage: Optional[Stage] = None
    learnings: str = ""
