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

from ..schemas import Evidence, Gap


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
    star: bool = False
    pinned: bool = False                       # user-pinned (boosts priority)

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


class Project(BaseModel):
    id: str
    domain: str
    sub_segments: list[str] = Field(default_factory=list)
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
    autostart: bool = True


class IntakeQuestion(BaseModel):
    """One preflight clarifying question with a few suggested answers."""

    question: str
    suggestions: list[str] = Field(default_factory=list)


class IntakeRequest(BaseModel):
    domain: str = Field(min_length=2, max_length=200)


class IntakeResponse(BaseModel):
    questions: list[IntakeQuestion] = Field(default_factory=list)


class ControlAction(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    CONTINUE_MILESTONE = "continue_milestone"  # acknowledge a milestone check-in
    SET_BUDGET = "set_budget"
    SET_PACE = "set_pace"
    PIN_NODE = "pin_node"
    UNPIN_NODE = "unpin_node"


class ControlRequest(BaseModel):
    action: ControlAction
    budget: Optional[Budget] = None
    pace: Optional[Pace] = None
    node_id: Optional[str] = None
