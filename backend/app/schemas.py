"""Shared data contracts for the Market Gap Finder.

This module is the single source of truth for the shape of data that flows
through the pipeline and out to the frontend. The LLM synthesis step is forced
to emit JSON matching `Gap`; the API returns `GapReport`. Keep frontend
`src/types.ts` in sync with these models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Source status                                                               #
# --------------------------------------------------------------------------- #
class SourceName(str, Enum):
    REDDIT = "reddit"
    ARXIV = "arxiv"
    HACKERNEWS = "hackernews"
    GITHUB = "github"
    NEWSLETTER = "newsletter"
    JOBS = "jobs"
    APPREVIEWS = "appreviews"
    REGULATORY = "regulatory"
    STACKEXCHANGE = "stackexchange"
    OUTCOMES = "outcomes"
    POSTMORTEMS = "postmortems"


class SourceStatus(str, Enum):
    LIVE = "live"          # real API responded with real data
    MOCK = "mock"          # no credentials -> realistic fixture data
    UNAVAILABLE = "unavailable"  # credentials present but the fetch failed
    EMPTY = "empty"        # queried fine but nothing relevant returned


class SourceReport(BaseModel):
    """Per-source telemetry surfaced in the UI ('which sources fired')."""

    name: SourceName
    status: SourceStatus
    item_count: int = 0
    freshest: Optional[datetime] = None  # newest datapoint we ingested
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    note: Optional[str] = None           # human-readable ('rate-limited', 'no key', ...)
    query_terms: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Raw & extracted signals (internal; not all shipped to the UI)               #
# --------------------------------------------------------------------------- #
class RawItem(BaseModel):
    """A normalized unit from any source before signal extraction."""

    source: SourceName
    id: str
    title: str
    body: str = ""
    url: str = ""
    created: Optional[datetime] = None
    # source-specific strength signals (upvotes, citations, series delta, ...)
    weight: float = 0.0
    meta: dict = Field(default_factory=dict)


class DemandSignal(BaseModel):
    """A distilled unmet-need / pain / interest indicator."""

    source: SourceName
    kind: Literal["pain", "wish", "complaint", "interest", "search"]
    quote: str
    url: str = ""
    strength: float = 0.0            # normalized 0..1
    date: Optional[datetime] = None
    tags: list[str] = Field(default_factory=list)
    live: bool = True                # provenance: False if mined from mock data


class CapabilitySignal(BaseModel):
    """A 'what just became feasible' tailwind (arXiv momentum, econ shift)."""

    source: SourceName
    description: str
    url: str = ""
    momentum: float = 0.0            # acceleration proxy, normalized 0..1
    date: Optional[datetime] = None
    live: bool = True                # provenance: False if mined from mock data


class SupplySignal(BaseModel):
    """An incumbent / existing-solution hint mined from the sources."""

    source: SourceName
    name: str
    hint: str                        # what was said about them
    url: str = ""
    live: bool = True                # provenance: False if mined from mock data


class FundingHint(BaseModel):
    """A funding-announcement crowding hint mined from newsletter items.

    Deterministic extraction (no LLM): "X raises $12M Series A ..." headlines
    become {company, round_hint, space_tokens, url} so the pressure test's
    'crowded' lens can see who just got funded near a candidate space.
    """

    company: str
    round_hint: str = ""             # e.g. "series a, $12M" — whatever was stated
    space_tokens: list[str] = Field(default_factory=list)  # what space they fund
    url: str = ""
    live: bool = True                # provenance: False if mined from mock data


class ExtractedSignals(BaseModel):
    """Everything the synthesis step reasons over, per run."""

    area: str
    sub_segments: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    demand: list[DemandSignal] = Field(default_factory=list)
    capability: list[CapabilitySignal] = Field(default_factory=list)
    supply: list[SupplySignal] = Field(default_factory=list)
    # Additive (Phase 3 C1): funding-round crowding hints from newsletters.
    funding: list[FundingHint] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# The gap contract (LLM output shape)                                         #
# --------------------------------------------------------------------------- #
SCORE_KEYS = (
    "demand_strength",
    "competitive_openness",
    "trend_tailwind",
    "feasibility",
    "willingness_to_pay",
)


class Scores(BaseModel):
    """Each 1..5. Higher is more attractive."""

    demand_strength: int = Field(ge=1, le=5)
    competitive_openness: int = Field(ge=1, le=5)
    trend_tailwind: int = Field(ge=1, le=5)
    feasibility: int = Field(ge=1, le=5)
    willingness_to_pay: int = Field(ge=1, le=5)


class Evidence(BaseModel):
    source: SourceName
    url: str
    quote: str
    date: Optional[str] = None       # kept as string; sources vary in precision
    live: bool = True                # provenance: False if it traces to mock/canned data


class Competitor(BaseModel):
    name: str
    url: str = ""
    positioning: str                 # what they're actually known for
    segment: str                     # who they target
    price_tier: str                  # rough tier: free / $ / $$ / $$$ / enterprise
    weakness: str                    # the blind spot that leaves the gap open


class CompanyConcept(BaseModel):
    """The company-shaped framing of a gap: the standalone business you'd build.

    Optional on ``Gap`` so historical data (and any degrade-to-no-LLM path) still
    loads; when present it turns a thin "market gap" into a coherent company —
    what you build, who for, how it earns, the wedge→platform arc, its moat, and
    an explicit verdict on whether this is a *company* rather than a *feature*.
    """

    product: str = ""                # what you actually build (2-3 sentences)
    icp: str = ""                    # ideal customer profile — who it's for
    business_model: str = ""         # how it makes money + rough pricing shape
    expansion_path: str = ""         # wedge → product → platform arc
    moat: str = ""                   # durable defensibility
    standalone: bool = True          # is this a company, not just a feature?
    standalone_reason: str = ""      # why it stands alone (or why it's thin)


class Gap(BaseModel):
    """One candidate market gap. This is what the LLM must return per item."""

    title: str
    thesis: str                      # one-line
    # Plain-language explainer for a smart reader who is NOT in this field: what
    # is broken today, who it hurts, and what this would actually do about it.
    # Every other field is compressed jargon aimed at someone already fluent in
    # the domain — this is the one place the idea has to stand on its own. A gap
    # nobody can explain to an outsider is a gap nobody can sell, hire for, or
    # raise on. Empty string = the model gave nothing; the UI falls back to the
    # thesis rather than inventing one.
    easy_explain: str = ""
    scores: Scores
    company: Optional[CompanyConcept] = None  # company-shaped framing (optional)
    evidence: list[Evidence] = Field(default_factory=list)
    competitors: list[Competitor] = Field(default_factory=list, max_length=8)
    wedge: str                       # the sharp initial entry point
    riskiest_assumption: str         # what must be validated first
    weakest_link: str                # the softest score / biggest doubt, named
    why_now: str = ""                # the recent shift that unlocks this gap
    empty_for_a_reason: bool = False # True if this is likely empty-for-a-reason
    empty_reason: str = ""           # ...and why, if so
    grounded: bool = True            # False if every evidence item failed the grounding gate
    evidence_dropped: int = 0        # ungrounded evidence items dropped by the gate
    novelty: int = Field(default=3, ge=1, le=5)  # creativity / non-obviousness
    sub_segment: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("competitors")
    @classmethod
    def _cap_competitors(cls, v: list[Competitor]) -> list[Competitor]:
        return v[:5]                  # top 5 per the spec


# --------------------------------------------------------------------------- #
# Scoring weights & the served report                                         #
# --------------------------------------------------------------------------- #
class Weights(BaseModel):
    """User-tunable weighting for the composite rank score. Auto-normalized."""

    demand_strength: float = 1.0
    competitive_openness: float = 1.0
    trend_tailwind: float = 0.8
    # Feasibility advises; it must not gate. At 0.8 it was ~17.8% of the
    # composite, so feasibility 1 vs 5 swung viability by ~17.8 points — more
    # than a full weaken penalty (9.0) — and with star_threshold at 75 it alone
    # decided starring. Every other key here is a fact about the MARKET; this is
    # the only one that is a fact about the FOUNDER, and a founder fact has no
    # business carrying that much weight in a market-viability composite. A
    # capital-intensive idea should be scored on whether the market is real, not
    # docked for being expensive — the founder decides what to resource.
    feasibility: float = 0.4
    willingness_to_pay: float = 0.9

    def normalized(self) -> dict[str, float]:
        total = sum(getattr(self, k) for k in SCORE_KEYS) or 1.0
        return {k: getattr(self, k) / total for k in SCORE_KEYS}


class RankedGap(BaseModel):
    """A Gap plus its server-computed composite score, for the table/2x2."""

    gap: Gap
    composite: float                 # 0..5 weighted blend
    rank: int


class GapReport(BaseModel):
    """The full payload the frontend renders."""

    area: str
    sub_segments: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sources: list[SourceReport] = Field(default_factory=list)
    weights: Weights = Field(default_factory=Weights)
    gaps: list[RankedGap] = Field(default_factory=list)
    llm_mode: str = "unknown"        # 'agent-sdk' | 'cli' | 'api' | 'fixture'
    model: str = ""                  # the Claude model used for synthesis
    cache_hit: bool = False
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# API request models                                                          #
# --------------------------------------------------------------------------- #
class AnalyzeRequest(BaseModel):
    area: str = Field(min_length=2, max_length=200)
    sub_segments: list[str] = Field(default_factory=list)
    weights: Optional[Weights] = None
    refresh: bool = False            # bypass the ingest cache
    reweight_only: bool = False      # re-rank cached synthesis without re-fetch
    model: Optional[str] = None      # which Claude model to synthesize with


class RankRequest(BaseModel):
    """Re-rank an already-synthesized report with new weights (no re-fetch)."""

    area: str
    sub_segments: list[str] = Field(default_factory=list)
    weights: Weights
    model: Optional[str] = None      # only used if the rerank falls back to a full run
