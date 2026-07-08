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
  star: boolean;
  pinned: boolean;
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
  | "log";

export interface ExplorerEvent {
  seq: number;
  project_id: string;
  type: EventType;
  at: string;
  node: TreeNode | null;
  project: Project | null;
  message: string;
}

export interface TreeSnapshot {
  project: Project;
  nodes: TreeNode[];
  last_seq: number;
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
