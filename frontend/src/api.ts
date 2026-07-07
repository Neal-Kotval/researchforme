// Thin, typed client for the Market Gap Finder backend.
// Endpoints (proxied through Vite to FastAPI on :8000):
//   POST /api/analyze  -> GapReport   (fetch + synthesize + rank)
//   POST /api/rerank   -> GapReport   (re-weight cached synthesis, no re-fetch)
//   GET  /api/health   -> HealthInfo
import type { GapReport, Weights } from "./types";

const JSON_HEADERS = { "Content-Type": "application/json" } as const;

/** A structured API error carrying the HTTP status so callers can branch. */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    });
  } catch (e) {
    // Network / server-down: surface a friendly message.
    throw new ApiError(0, "Could not reach the analysis server. Is the backend running?");
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j?.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export interface AnalyzeParams {
  area: string;
  subSegments: string[];
  weights?: Weights;
  refresh?: boolean;
  model?: string;
}

/** Run (or refresh) a full analysis for an area. */
export function analyze(p: AnalyzeParams): Promise<GapReport> {
  return postJson<GapReport>("/api/analyze", {
    area: p.area,
    sub_segments: p.subSegments,
    weights: p.weights,
    refresh: p.refresh ?? false,
    model: p.model,
  });
}

/** Re-rank an already-synthesized report with new weights (no re-fetch). */
export function rerank(
  area: string,
  subSegments: string[],
  weights: Weights,
  model?: string
): Promise<GapReport> {
  return postJson<GapReport>("/api/rerank", {
    area,
    sub_segments: subSegments,
    weights,
    model,
  });
}

export interface HealthInfo {
  status?: string;
  llm_mode?: string;
  [k: string]: unknown;
}

/** Lightweight liveness/config probe for the status pill. */
export async function health(): Promise<HealthInfo> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new ApiError(res.status, `Health check failed (${res.status})`);
  return (await res.json()) as HealthInfo;
}
