// Mirrors backend/app/autonomous/schemas.py. Keep in sync.
import type { Gap, Evidence } from "../types";

export type NodeKind = "domain" | "subarea" | "segment" | "gap_candidate" | "gap";
export type NodeState =
  | "queued"
  | "expanding"
  | "children_ready"
  | "synthesizing"
  | "pressure_testing"
  | "scored"
  | "pruned"
  | "errored";

export type Confidence = "low" | "medium" | "high";
export type TestRigor = "light" | "standard" | "deep";

export interface LensVerdict {
  lens: string;
  verdict: "survives" | "weakens" | "kills";
  argument: string;
  evidence: Evidence[];
}

export interface PressureTest {
  lenses: LensVerdict[];
  survived: number;
  weakened: number;
  killed: number;
  test_rigor: TestRigor;
  summary: string;
  self_critique: string;
}

export interface TreeNode {
  id: string;
  project_id: string;
  parent_id: string | null;
  kind: NodeKind;
  state: NodeState;
  title: string;
  rationale: string;
  keywords: string[];
  depth: number;
  priority: number;
  gap: Gap | null;
  viability: number | null;
  confidence: Confidence | null;
  pressure_test: PressureTest | null;
  /** Founder fit (0..100, orthogonal to viability): "is this space for YOU",
   *  scored from the project's steering context. null = no steering provided
   *  or scoring unavailable — render nothing for null, never a fake 0. */
  fit: number | null;
  fit_reason: string;
  star: boolean;
  pinned: boolean;
  /** C2 Space Watch: sweeps re-check this node's sources for material shifts.
   *  Optional until the Wave D exploration surfaces land the full sensor set. */
  watched?: boolean;
  child_ids: string[];
  error: string | null;
  tokens_spent: number;
  created_at: string;
  updated_at: string;
}

export type Pace = "eco" | "balanced" | "sprint";

export interface Budget {
  max_tokens: number | null;
  daily_cap_tokens: number | null;
  max_nodes: number | null;
  time_limit_minutes: number | null;
  pace: Pace;
  star_threshold: number;
  milestone_tokens: number;
}

export type ProjectStatus =
  | "running"
  | "paused"
  | "usage_paused"
  | "milestone_paused"
  | "exhausted"
  | "budget_spent"
  | "time_limit"
  | "errored";

export type ExplorerMode = "sprinting" | "curbing" | "paused";

export interface ProjectStats {
  nodes: number;
  gaps: number;
  candidates: number;
  stars: number;
  tokens_spent: number;
  max_viability: number;
  frontier_size: number;
  mode: ExplorerMode;
  next_resume_at: string | null;
  stop_reason: string | null;
}

export interface Project {
  id: string;
  domain: string;
  sub_segments: string[];
  decompose_model: string;
  synth_model: string;
  pressure_model: string;
  status: ProjectStatus;
  budget: Budget;
  stats: ProjectStats;
  steering?: SteeringContext;
  created_at: string;
  updated_at: string;
}

export type EventType =
  | "project_created"
  | "project_updated"
  | "node_added"
  | "node_updated"
  | "node_pruned"
  | "log"
  | "watch_alert";

/** One Space Watch material-shift alert (C2): ≥3 new source items or a new
 *  regulatory/outcomes hit on a watched node. Evidence is only ever the
 *  actual new source items. Mirrors backend `WatchAlert`. */
export interface WatchAlert {
  node_id: string;
  summary: string;
  evidence: Evidence[];
  new_items: number;
  weight_delta: number;
  regulatory_hit: boolean;
  at: string;
}

export interface ExplorerEvent {
  seq: number;
  project_id: string;
  type: EventType;
  at: string;
  node: TreeNode | null;
  project: Project | null;
  message: string;
  /** Present on `watch_alert` events only. */
  alert?: WatchAlert | null;
}

export interface TreeSnapshot {
  project: Project;
  nodes: TreeNode[];
  last_seq: number;
}

/** One rejected space in the cross-project anti-portfolio (GET /api/graveyard).
 *  Internal items are killed/passed gap nodes; `external: true` marks curated
 *  post-mortem corpus entries, which carry no project of their own.
 *  Mirrors backend app/autonomous/schemas.py `GraveyardItem`. */
export interface GraveyardItem {
  project_id: string | null;
  project_domain: string | null;
  node_id: string;
  title: string;
  thesis_first_line: string;
  viability: number | null;
  kill_lenses: string[];
  triage_reason: string;
  updated_at: string | null;
  external: boolean;
}

/** One watched node + its most recent alert (GET /api/watch) — the dashboard
 *  "recent signals / movers" block. Mirrors backend `WatchedNodeStatus`. */
export interface WatchedNodeStatus {
  project_id: string;
  project_domain: string | null;
  node: TreeNode;
  last_alert: WatchAlert | null;
}

/** What POST /api/watch/sweep returns. Mirrors backend `WatchSweepResult`. */
export interface WatchSweepResult {
  swept: number;
  alerts: WatchAlert[];
}

export type UsageLevel = "low" | "medium" | "high" | "heavy";

/** Shared governor snapshot for the global usage bar (GET /api/usage). */
export interface GlobalUsage {
  spent_total: number;
  daily_spent: number;
  rate_per_min: number;
  mode: ExplorerMode;
  in_backoff: boolean;
  backoff_remaining_s: number;
  recent_limits: number;
  max_concurrency: number;
  // Usage-shaping policy + derived gauge fields.
  daily_cap: number | null;
  limit_pct: number;
  effective_cap: number | null;
  usage_ratio: number | null;
  usage_level: UsageLevel;
  projected_24h: number;
}

export interface UsagePolicy {
  daily_cap_tokens?: number | null;
  limit_pct?: number;
}

/** Rich founder context that steers every LLM step of an exploration. */
export interface SteeringContext {
  brief?: string;
  advantages?: string[];
  constraints?: string[];
  avoid?: string[];
  time_horizon?: string;
  research?: string;
}

export interface CreateProjectRequest {
  domain: string;
  sub_segments?: string[];
  budget?: Partial<Budget>;
  decompose_model?: string;
  synth_model?: string;
  pressure_model?: string;
  intake?: Record<string, string>;
  steering?: SteeringContext;
  autostart?: boolean;
}

export interface IntakeQuestion {
  question: string;
  suggestions: string[];
}

/** A raw research paste sorted into a ready-to-launch job (POST /projects/sort-research). */
export interface SortedResearch {
  domain: string;
  sub_segments: string[];
  brief: string;
  research: string;
}

/* ------------------------------------------------------------------ scout -- */
// POST /api/projects/scout — the engine proposes ownable spaces from what is
// hot right now. Stateless; mirrors ScoutRequest/ScoutResponse in schemas.py.

export interface ScoutRequest {
  brief?: string;      // optional founder context
  avoid?: string[];    // spaces to exclude
}

/** One trending item that triggered a scout candidate — always grounded in the
 *  fetched input set, never LLM-invented. */
export interface ScoutSignal {
  source: string;
  title: string;
  url: string;
}

/** A candidate DOMAIN shaped like an ownable space, with its trigger signals. */
export interface ScoutCandidate {
  domain: string;
  rationale: string;
  signals: ScoutSignal[];
  suggested_sub_segments: string[];
  degraded: boolean;   // true = deterministic fallback (LLM unavailable)
}

/** Per-source telemetry ("which sources fired") — mirrors SourceReport. */
export interface SourceReport {
  name: string;
  status: string;
  item_count: number;
  freshest: string | null;
  fetched_at: string;
  note: string | null;
  query_terms: string[];
}

export interface ScoutResponse {
  candidates: ScoutCandidate[];
  sources: SourceReport[];
  generated_at: string;
}

/** True when a steering context actually says anything (drives fit scoring). */
export function hasSteeringContext(s: SteeringContext | undefined | null): boolean {
  if (!s) return false;
  return Boolean(
    s.brief?.trim() ||
      s.advantages?.length ||
      s.constraints?.length ||
      s.avoid?.length ||
      s.time_horizon?.trim() ||
      s.research?.trim()
  );
}

export type ControlAction =
  | "pause"
  | "resume"
  | "continue_milestone"
  | "set_budget"
  | "set_pace"
  | "pin_node"
  | "unpin_node";

export const DEFAULT_BUDGET: Budget = {
  max_tokens: null,
  daily_cap_tokens: null,
  max_nodes: 400,
  time_limit_minutes: null,
  pace: "balanced",
  star_threshold: 75,
  milestone_tokens: 0,
};

/** viability (0..100) → ramp token index for the compact viability chip. */
export function viabilityRamp(v: number | null): string {
  if (v == null) return "var(--data-neutral)";
  const i = Math.min(4, Math.max(0, Math.floor((v / 100) * 5)));
  return `var(--ramp-${i})`;
}

/** Composite ranking score for "fit × viability" ordering (memo §4). A node
 *  with no fit score ranks by viability alone — never by a fabricated fit. */
export function fitViabScore(n: Pick<TreeNode, "viability" | "fit">): number {
  if (n.viability == null) return -1;
  return n.fit != null ? (n.viability * n.fit) / 100 : n.viability;
}

/* -------------------------------------------------- display sanitation -- */
// The backend rides its prompt-facing steering block ("== FOUNDER STEERING
// (honour all of this) == …") on the root node's rationale so decomposition
// stays steered. That block is machine-facing — never show it to the founder.
const STEERING_BLOCK = /\n?== FOUNDER STEERING[\s\S]*$/;

/** A node rationale safe to render: prompt machinery stripped. */
export function displayRationale(rationale: string | null | undefined): string {
  return (rationale ?? "").replace(STEERING_BLOCK, "").trim();
}

/* ------------------------------------------------------- trust encoding -- */
// Memo §2: a number's visual weight must match how much it deserves belief.
// earned      = high confidence + standard/deep rigor → solid, full color.
// provisional = medium confidence → outlined numeral.
// unverified  = low confidence OR light rigor OR untested → dashed,
//               desaturated, smaller, labelled "unverified".
export type TrustLevel = "earned" | "provisional" | "unverified";

export function trustLevel(
  confidence: Confidence | null,
  rigor: TestRigor | null | undefined
): TrustLevel {
  if (confidence == null || confidence === "low" || rigor == null || rigor === "light") {
    return "unverified";
  }
  return confidence === "high" ? "earned" : "provisional";
}

/** Trust for a whole node (gap chips in the tree, digest rows, inspector). */
export function nodeTrust(n: TreeNode): TrustLevel {
  return trustLevel(n.confidence, n.pressure_test?.test_rigor ?? null);
}

// A lens verdict the backend filled heuristically (LLM unreachable / skipped)
// must never wear a full-authority Survives/Weakens pill. Backends mark these
// in the argument text; empty evidence on a light-rigor pass means the same.
export function isUnevaluatedLens(l: LensVerdict, rigor: TestRigor): boolean {
  return (
    /not evaluated|heuristic fallback/i.test(l.argument) ||
    (l.evidence.length === 0 && rigor === "light")
  );
}
