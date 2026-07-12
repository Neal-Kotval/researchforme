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

/* --------------------------------------------------- user sensors (S1/S2) -- */
// Triage is the cheap interested/pass verdict; Stage tracks a gap the user is
// actively looking into. Workflow state, not scores — neutral ink, never a hue.
export type Triage = "interested" | "passed";
export type Stage =
  | "found"
  | "interviewing"
  | "smoke_testing"
  | "verdict_build"
  | "verdict_pass";

/** Pass-reason taxonomy (S1) — picker chips; free text is always allowed. */
export const TRIAGE_REASONS = [
  "too_crowded",
  "not_my_skills",
  "too_small",
  "boring",
  "no_distribution",
  "other",
] as const;

export const TRIAGE_REASON_LABELS: Record<(typeof TRIAGE_REASONS)[number], string> = {
  too_crowded: "Too crowded",
  not_my_skills: "Not my skills",
  too_small: "Too small",
  boring: "Boring",
  no_distribution: "No distribution",
  other: "Other",
};

/** Look-into checklist order (S2) — the stage select on starred gaps. */
export const STAGES: Stage[] = [
  "found",
  "interviewing",
  "smoke_testing",
  "verdict_build",
  "verdict_pass",
];

export const STAGE_LABELS: Record<Stage, string> = {
  found: "Found",
  interviewing: "Interviewing",
  smoke_testing: "Smoke testing",
  verdict_build: "Verdict: build",
  verdict_pass: "Verdict: pass",
};

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
  /** S1 triage — the user's cheap verdict. null = untriaged. Set only via the
   *  `set_triage` control action, never by the LLM. */
  triage: Triage | null;
  /** Taxonomy slug or free text; "" when untriaged or no reason given. */
  triage_reason: string;
  /** S2 look-into checklist position on gaps the user is chasing. */
  stage: Stage | null;
  /** What the user found out so far (free text alongside the stage). */
  learnings: string;
  /** C2 Space Watch: sweeps re-check this node's sources for material shifts. */
  watched: boolean;
  /** H2 Research Pack: cached markdown hand-off pack ("" = not generated). */
  research_pack: string;
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
  /** C4 idle-headroom scavenger — opt-in ONLY, default OFF. When true (and the
   *  run is exhausted with ample headroom + unexpanded starred branches) the
   *  manual `continue_deepening` control becomes valid. Never automatic. */
  allow_idle_deepening: boolean;
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
  /** H4 end-of-run digest, written on terminal transition. null/absent until a
   *  run finishes; `degraded: true` marks the deterministic no-LLM fallback. */
  digest?: ProjectDigest | null;
  /** C3 re-run lineage: set on projects created via POST /projects/{pid}/rerun
   *  so a fresh run can be diffed against the run it was cloned from. */
  parent_project_id?: string | null;
  created_at: string;
  updated_at: string;
}

/** One "worth your next hour" space in the end-of-run digest (H4). */
export interface DigestSpace {
  title: string;
  why: string;
}

/** The end-of-run digest (H4): mirrors backend `Project.digest` (a dict). */
export interface ProjectDigest {
  top_spaces: DigestSpace[];
  kill_pattern: string;
  next_questions: string[];
  degraded: boolean;
}

/** The backend stores `Project.digest` as a plain dict — normalize defensively
 *  so a partial or malformed digest degrades to null (never invented fields).
 *  Returns null unless at least one substantive field survives validation. */
export function normalizeDigest(raw: unknown): ProjectDigest | null {
  if (raw == null || typeof raw !== "object" || Array.isArray(raw)) return null;
  const d = raw as Record<string, unknown>;
  const top_spaces: DigestSpace[] = Array.isArray(d.top_spaces)
    ? d.top_spaces
        .filter((s): s is Record<string, unknown> => s != null && typeof s === "object")
        .map((s) => ({
          title: typeof s.title === "string" ? s.title : "",
          why: typeof s.why === "string" ? s.why : "",
        }))
        .filter((s) => s.title.trim() !== "")
    : [];
  const kill_pattern = typeof d.kill_pattern === "string" ? d.kill_pattern : "";
  const next_questions: string[] = Array.isArray(d.next_questions)
    ? d.next_questions.filter((q): q is string => typeof q === "string" && q.trim() !== "")
    : [];
  if (top_spaces.length === 0 && kill_pattern.trim() === "" && next_questions.length === 0) {
    return null;
  }
  return { top_spaces, kill_pattern, next_questions, degraded: d.degraded === true };
}

/** POST /api/projects/{pid}/nodes/{nid}/research-pack (H2). `cached` is true
 *  when the pack was served from `Node.research_pack` without a new LLM call;
 *  `?refresh=1` regenerates. A backend that can't produce a real pack returns
 *  503 (honest degrade — never canned content). Mirrors `ResearchPackResponse`. */
export interface ResearchPackResponse {
  node_id: string;
  markdown: string;
  cached: boolean;
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

export type PreferenceStatus = "pending" | "active" | "dismissed";

/** The distilled learned-preferences row (H3) — POST /api/preferences/distill
 *  writes a pending proposal; only `status: "active"` text is ever injected
 *  into prompts. Mirrors backend `Preferences`. */
export interface Preferences {
  learned_preferences: string;
  status: PreferenceStatus;
  updated_at: string;
}

/** What GET /api/preferences returns; `triage_count` backs the dashboard
 *  "distill what your passes say" card threshold (>=8 verdicts).
 *  Mirrors backend `PreferencesState`. */
export interface PreferencesState {
  preferences: Preferences | null;
  triage_count: number;
}

/** Review/edit/confirm/dismiss payload for POST /api/preferences.
 *  Mirrors backend `UpdatePreferencesRequest`. */
export interface UpdatePreferencesRequest {
  learned_preferences: string;
  status: "active" | "dismissed";
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
  | "unpin_node"
  | "set_triage"
  | "set_stage"
  | "watch_node"
  | "unwatch_node"
  | "continue_deepening";

export const DEFAULT_BUDGET: Budget = {
  max_tokens: null,
  daily_cap_tokens: null,
  max_nodes: 400,
  time_limit_minutes: null,
  pace: "balanced",
  star_threshold: 75,
  milestone_tokens: 0,
  allow_idle_deepening: false,
};

/* ------------------------------------------------- re-run diff (C3) -- */

/** Body for POST /api/projects/{pid}/rerun. The clone links back via
 *  `parent_project_id`; `autostart` defaults false — a re-run never spends
 *  tokens unbidden. Mirrors backend `RerunRequest`. */
export interface RerunRequest {
  autostart?: boolean;
}

/** One gap present on only one side of a re-run diff. Mirrors `DiffEntry`. */
export interface DiffEntry {
  node_id: string;
  title: string;
  viability: number | null;
  fit: number | null;
}

/** A title-matched gap whose viability or fit shifted between runs; `*_from`
 *  is the baseline (`?against=`) run, `*_to` the requested project. null =
 *  unscored on that side, never fabricated. Mirrors backend `MovedGap`. */
export interface MovedGap {
  title: string;
  viability_from: number | null;
  viability_to: number | null;
  fit_from: number | null;
  fit_to: number | null;
}

/** GET /api/projects/{pid}/diff?against={other} — node-level diff of scored
 *  gaps by normalized-title match. Pure store computation, no LLM.
 *  Mirrors backend `ProjectDiff`. */
export interface ProjectDiff {
  project_id: string;
  against: string;
  new: DiffEntry[];
  gone: DiffEntry[];
  moved: MovedGap[];
}

/* --------------------------------------------------- portfolio (H1) -- */

/** One scored gap in the cross-project portfolio (GET /api/portfolio) — the
 *  2×2 fit × viability scatter dataset. `fit: null` gaps render in a separate
 *  "no steering" strip, never faked onto the plot. Mirrors `PortfolioItem`. */
export interface PortfolioItem {
  project_id: string;
  domain: string | null;
  node_id: string;
  title: string;
  viability: number | null;
  fit: number | null;
  confidence: Confidence | null;
  star: boolean;
  triage: Triage | null;
  stage: Stage | null;
  updated_at: string | null;
}

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
