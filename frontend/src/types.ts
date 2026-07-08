// Mirrors backend/app/schemas.py. Keep in sync.

export type SourceName = "reddit" | "arxiv" | "hackernews" | "github" | "newsletter";
export type SourceStatusKind = "live" | "mock" | "unavailable" | "empty";

/** Short, human-facing label for each signal source. */
export const SOURCE_LABEL: Record<SourceName, string> = {
  reddit: "Reddit",
  arxiv: "arXiv",
  hackernews: "HN",
  github: "GitHub",
  newsletter: "Newsletter",
};

export interface SourceReport {
  name: SourceName;
  status: SourceStatusKind;
  item_count: number;
  freshest: string | null;
  fetched_at: string;
  note: string | null;
  query_terms: string[];
}

export const SCORE_KEYS = [
  "demand_strength",
  "competitive_openness",
  "trend_tailwind",
  "feasibility",
  "willingness_to_pay",
] as const;
export type ScoreKey = (typeof SCORE_KEYS)[number];

export type Scores = Record<ScoreKey, number>;

export interface Evidence {
  source: SourceName;
  url: string;
  quote: string;
  date: string | null;
}

export interface Competitor {
  name: string;
  url: string;
  positioning: string;
  segment: string;
  price_tier: string;
  weakness: string;
}

/** The company-shaped framing of a gap: the standalone business you'd build. */
export interface CompanyConcept {
  product: string;
  icp: string;
  business_model: string;
  expansion_path: string;
  moat: string;
  standalone: boolean;
  standalone_reason: string;
}

export interface Gap {
  title: string;
  thesis: string;
  company?: CompanyConcept | null;
  scores: Scores;
  evidence: Evidence[];
  competitors: Competitor[];
  wedge: string;
  riskiest_assumption: string;
  weakest_link: string;
  why_now: string;
  empty_for_a_reason: boolean;
  empty_reason: string;
  novelty: number;
  sub_segment: string;
  tags: string[];
}

export interface RankedGap {
  gap: Gap;
  composite: number;
  rank: number;
}

export type Weights = Record<ScoreKey, number>;

export interface GapReport {
  area: string;
  sub_segments: string[];
  generated_at: string;
  sources: SourceReport[];
  weights: Weights;
  gaps: RankedGap[];
  llm_mode: string;
  model: string;
  cache_hit: boolean;
  warnings: string[];
}

export interface AnalyzeRequest {
  area: string;
  sub_segments?: string[];
  weights?: Weights;
  refresh?: boolean;
  reweight_only?: boolean;
  model?: string;
}

// --- selectable synthesis models (mirrors config.AVAILABLE_MODELS) ---------
export interface ModelInfo {
  id: string;
  label: string;
  sub: string;
}

export const MODELS: ModelInfo[] = [
  { id: "claude-opus-4-8", label: "Opus 4.8", sub: "Deepest" },
  { id: "claude-sonnet-5", label: "Sonnet 5", sub: "Balanced" },
  { id: "claude-haiku-4-5-20251001", label: "Haiku 4.5", sub: "Fastest" },
];

export const DEFAULT_MODEL = "claude-opus-4-8";

/** Short label for a model id (falls back to the raw id). */
export function modelLabel(id: string): string {
  return MODELS.find((m) => m.id === id)?.label ?? id;
}

export const SCORE_LABELS: Record<ScoreKey, string> = {
  demand_strength: "Demand",
  competitive_openness: "Openness",
  trend_tailwind: "Tailwind",
  feasibility: "Feasibility",
  willingness_to_pay: "WTP",
};

export const DEFAULT_WEIGHTS: Weights = {
  demand_strength: 1.0,
  competitive_openness: 1.0,
  trend_tailwind: 0.8,
  feasibility: 0.8,
  willingness_to_pay: 0.9,
};
