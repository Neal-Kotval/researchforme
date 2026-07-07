// Typed client for Autonomous Exploration Mode (SPEC §7).
// Endpoints (proxied through Vite to FastAPI on :8000, mounted under /api):
//   POST   /api/projects              -> Project        (create + optional autostart)
//   GET    /api/projects              -> Project[]      (tab bar, newest first)
//   GET    /api/projects/{id}         -> Project
//   GET    /api/projects/{id}/tree    -> TreeSnapshot   (hydration)
//   POST   /api/projects/{id}/control -> Project        (pause/resume/budget/pace/pin)
//   DELETE /api/projects/{id}         -> {ok:true}
//   GET    /api/projects/{id}/events  -> SSE stream     (snapshot + live ExplorerEvents)
import { ApiError } from "../api";
import type {
  Budget,
  ControlAction,
  CreateProjectRequest,
  ExplorerEvent,
  Pace,
  Project,
  TreeSnapshot,
} from "./types";

export { ApiError };

const JSON_HEADERS = { "Content-Type": "application/json" } as const;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Could not reach the explorer server. Is the backend running?");
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

/** Create (and, by default, autostart) an autonomous exploration project. */
export function createProject(req: CreateProjectRequest): Promise<Project> {
  return request<Project>("/api/projects", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(req),
  });
}

/** Every project, newest first — feeds the tab bar. */
export function listProjects(): Promise<Project[]> {
  return request<Project[]>("/api/projects");
}

/** One project's metadata + live stats. */
export function getProject(pid: string): Promise<Project> {
  return request<Project>(`/api/projects/${encodeURIComponent(pid)}`);
}

/** Full tree snapshot the UI hydrates from before folding live events. */
export function getTree(pid: string): Promise<TreeSnapshot> {
  return request<TreeSnapshot>(`/api/projects/${encodeURIComponent(pid)}/tree`);
}

export interface ControlRequest {
  action: ControlAction;
  budget?: Budget;
  pace?: Pace;
  node_id?: string;
}

/** Apply a run-control action (pause/resume/continue/budget/pace/pin) → updated project. */
export function control(pid: string, req: ControlRequest): Promise<Project> {
  return request<Project>(`/api/projects/${encodeURIComponent(pid)}/control`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(req),
  });
}

/** Stop the worker (if running) and forget the project. */
export function deleteProject(pid: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/projects/${encodeURIComponent(pid)}`, {
    method: "DELETE",
  });
}

export interface SubscribeOptions {
  /** Replay events with seq greater than this cursor on connect. */
  after?: number;
  /** Full-tree (re)hydration — fired on connect and any reconnect. */
  onSnapshot?: (snapshot: TreeSnapshot) => void;
  /** A single live node/project/log event. */
  onEvent: (event: ExplorerEvent) => void;
  /** Transport-level error (EventSource auto-reconnects after). */
  onError?: (e: Event) => void;
}

/**
 * Subscribe to a project's live event stream via SSE.
 *
 * The server sends a `snapshot` event carrying the whole `TreeSnapshot` on every
 * connect (including native EventSource reconnects), then tails `ExplorerEvent`s
 * as plain `data:` messages. We route the former to `onSnapshot` (re-hydrate) and
 * the latter to `onEvent` (fold). Returns an unsubscribe function.
 */
export function subscribeEvents(pid: string, opts: SubscribeOptions): () => void {
  const q = opts.after != null ? `?after=${opts.after}` : "";
  const es = new EventSource(`/api/projects/${encodeURIComponent(pid)}/events${q}`);

  es.addEventListener("snapshot", (e: MessageEvent) => {
    try {
      opts.onSnapshot?.(JSON.parse(e.data) as TreeSnapshot);
    } catch {
      /* malformed frame — ignore, next snapshot recovers */
    }
  });

  es.onmessage = (e: MessageEvent) => {
    try {
      opts.onEvent(JSON.parse(e.data) as ExplorerEvent);
    } catch {
      /* malformed frame — ignore */
    }
  };

  es.onerror = (e) => opts.onError?.(e);

  return () => es.close();
}
