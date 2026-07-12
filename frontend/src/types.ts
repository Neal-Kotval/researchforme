// Mirrors backend/app/schemas.py. Keep in sync.

export type SourceName =
  | "reddit"
  | "arxiv"
  | "hackernews"
  | "github"
  | "newsletter"
  | "jobs"
  | "appreviews"
  | "regulatory"
  | "outcomes"
  | "postmortems";

/** Short, human-facing label for each signal source. */
export const SOURCE_LABEL: Record<SourceName, string> = {
  reddit: "Reddit",
  arxiv: "arXiv",
  hackernews: "HN",
  github: "GitHub",
  newsletter: "Newsletter",
  jobs: "Jobs",
  appreviews: "App reviews",
  regulatory: "Regulatory",
  outcomes: "Outcomes",
  postmortems: "Post-mortems",
};

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
  /** Provenance: false = served from canned/fixture data, not a live fetch. */
  live?: boolean;
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

/** One-sentence hover definitions — every score is a hypothesis, not a fact. */
export const SCORE_HELP: Record<ScoreKey, string> = {
  demand_strength:
    "How loudly the market asks for this — inferred from signal volume, not proven demand.",
  competitive_openness:
    "How much room incumbents leave open — read from competitor coverage, not a guarantee.",
  trend_tailwind:
    "Whether the underlying trend pushes this forward — a bet on direction, not timing.",
  feasibility:
    "How buildable this looks for a small team — an estimate, not an engineering plan.",
  willingness_to_pay:
    "Whether buyers plausibly pay for this — inferred from analogous spend, not from customers.",
};
