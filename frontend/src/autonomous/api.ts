// Typed client for Autonomous Exploration Mode (SPEC §7).
// Endpoints (proxied through Vite to FastAPI on :8000, mounted under /api):
//   POST   /api/projects              -> Project        (create + optional autostart)
//   GET    /api/projects              -> Project[]      (tab bar, newest first)
//   POST   /api/projects/{id}/control -> Project        (pause/resume/budget/pace/pin)
//   DELETE /api/projects/{id}         -> {ok:true}
//   GET    /api/projects/{id}/events  -> SSE stream     (snapshot + live ExplorerEvents)
import type {
  Budget,
  ControlAction,
  CreateProjectRequest,
  ExplorerEvent,
  GlobalUsage,
  GraveyardItem,
  IntakeQuestion,
  UsagePolicy,
  Pace,
  PortfolioItem,
  Preferences,
  PreferencesState,
  Project,
  ResearchPackResponse,
  ScoutResponse,
  SortedResearch,
  Stage,
  TreeSnapshot,
  Triage,
  UpdatePreferencesRequest,
  WatchedNodeStatus,
} from "./types";

/** A structured API error carrying the HTTP status so callers can branch. */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

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

/** Generate preflight intake questions for a domain (steers the exploration).
 *  An optional brief lets the questions build on context already supplied. */
export function getIntake(
  domain: string,
  brief = "",
): Promise<{ questions: IntakeQuestion[] }> {
  return request<{ questions: IntakeQuestion[] }>("/api/projects/intake", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ domain, brief }),
  });
}

/** Sort a raw wall of the founder's own research into a launchable job. */
export function sortResearch(text: string): Promise<SortedResearch> {
  return request<SortedResearch>("/api/projects/sort-research", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ text }),
  });
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

/** One-shot tree snapshot (no SSE) — lets the dashboard rank ideas across
 *  projects without holding an event stream open per project. */
export function getTree(pid: string): Promise<TreeSnapshot> {
  return request<TreeSnapshot>(`/api/projects/${encodeURIComponent(pid)}/tree`);
}

/** Ask the engine to propose ownable spaces from what is hot right now. */
export function scout(brief = "", avoid: string[] = []): Promise<ScoutResponse> {
  return request<ScoutResponse>("/api/projects/scout", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ brief, avoid }),
  });
}

/** The shared governor's real global usage snapshot — drives the global bar. */
export function getUsage(): Promise<GlobalUsage> {
  return request<GlobalUsage>("/api/usage");
}

/** Set the dynamic usage-shaping policy (daily cap + % limit) → fresh snapshot. */
export function setUsagePolicy(policy: UsagePolicy): Promise<GlobalUsage> {
  return request<GlobalUsage>("/api/usage/policy", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(policy),
  });
}

/** Every scored gap across projects (H1) — the Compare 2×2 dataset. Pure
 *  store rollup, no LLM. `fit: null` gaps belong in the no-steering strip. */
export function getPortfolio(): Promise<PortfolioItem[]> {
  return request<PortfolioItem[]>("/api/portfolio");
}

/** The cross-project anti-portfolio (S3): killed/passed gaps merged with the
 *  curated post-mortem corpus (`external: true`). Store-level SQL, no LLM. */
export function getGraveyard(q = "", limit = 100): Promise<GraveyardItem[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (q.trim()) params.set("q", q.trim());
  return request<GraveyardItem[]>(`/api/graveyard?${params}`);
}

/** Watched nodes + the last alert each (C2) — the dashboard "Recent signals"
 *  block. Pure store reads; sweeps happen elsewhere (POST /api/watch/sweep). */
/** Every idea the user starred, across projects — the shortlist (W-1). */
export function getStarred(): Promise<PortfolioItem[]> {
  return request<PortfolioItem[]>("/api/starred");
}

export function getWatchStatus(): Promise<WatchedNodeStatus[]> {
  return request<WatchedNodeStatus[]>("/api/watch");
}

/** The single learned-preferences row (or null) + the triage-verdict count
 *  that gates the dashboard distill card (H3). No LLM. */
export function getPreferences(): Promise<PreferencesState> {
  return request<PreferencesState>("/api/preferences");
}

/** ONE user-initiated cheap-model pass over the triage verdicts → a PENDING
 *  proposal (H3). Nothing steers a run until the user confirms it. A backend
 *  that can't distill honestly answers 503 — never an invented preference. */
export function distillPreferences(): Promise<Preferences> {
  return request<Preferences>("/api/preferences/distill", { method: "POST" });
}

/** Confirm (`status: "active"`, possibly with edited text) or dismiss the
 *  proposal. Only active text is ever injected into prompts. */
export function updatePreferences(req: UpdatePreferencesRequest): Promise<Preferences> {
  return request<Preferences>("/api/preferences", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(req),
  });
}

export interface ControlRequest {
  action: ControlAction;
  budget?: Budget;
  pace?: Pace;
  node_id?: string;
  /** set_triage payload — `triage: null` clears the verdict (and its reason). */
  triage?: Triage | null;
  triage_reason?: string;
  /** set_stage payload — `stage: null` clears the checklist position. */
  stage?: Stage | null;
  learnings?: string;
}

/** Apply a run-control action (pause/resume/continue/budget/pace/pin) → updated project. */
export function control(pid: string, req: ControlRequest): Promise<Project> {
  return request<Project>(`/api/projects/${encodeURIComponent(pid)}/control`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(req),
  });
}

/** Generate (or fetch the cached) Research Pack for a gap node — ONE strong-model
 *  call, cached on the node; `refresh` forces a regenerate. A backend that can't
 *  produce a real pack answers 503 (honest degrade — surface it, never fake it). */
export function getResearchPack(
  pid: string,
  nid: string,
  refresh = false,
): Promise<ResearchPackResponse> {
  const q = refresh ? "?refresh=1" : "";
  return request<ResearchPackResponse>(
    `/api/projects/${encodeURIComponent(pid)}/nodes/${encodeURIComponent(nid)}/research-pack${q}`,
    { method: "POST", headers: JSON_HEADERS },
  );
}

/** Stop the worker (if running) and forget the project. */
export function deleteProject(pid: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/projects/${encodeURIComponent(pid)}`, {
    method: "DELETE",
  });
}

/* ------------------------------------------------------------- assistant -- */
export interface AssistantMessage {
  role: "user" | "assistant";
  text: string;
}

/** One tool call the assistant actually ran (rendered as a command block). */
export interface AssistantAction {
  tool: string; // dotted display form, e.g. "gap.explore.start"
  args: Record<string, unknown>;
  result: string; // compact JSON the tool returned
}

export interface AssistantReply {
  reply: string;
  actions: AssistantAction[];
  backend: string;
}

/** One conversational turn against the platform's tool layer (agent-sdk).
 *  Pass a projectSlug to scope the chat to a library project — the assistant
 *  then has that project's documents in context and doc.* tools to edit them. */
export function assistantChat(
  messages: AssistantMessage[],
  projectSlug?: string,
): Promise<AssistantReply> {
  return request<AssistantReply>("/api/assistant/chat", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ messages, project_slug: projectSlug ?? null }),
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


/* ------------------------------------------------------------- library --- */
/* Phase 5: projects are folders, documents are markdown files on disk. */

export interface LibraryProject {
  slug: string;
  title: string;
  created_at: string;
  updated_at: string;
  doc_count: number;
  idea_count: number;
  status: string;
  /** Project critique score, present once the red team has been run. */
  viability?: number | null;
  confidence?: string;
  verdict?: string;
}

export interface CritiqueResult {
  path: string;
  title: string;
  viability: number;
  confidence: string;
  verdict_line: string;
}

export const PROJECT_STATUSES = ["exploring", "validating", "committed", "shelved"] as const;

export type DocKind =
  | "plan"
  | "critique"
  | "consolidation"
  | "idea"
  | "research"
  | "doc";

export interface LibraryDoc {
  path: string;
  title: string;
  updated_at: string;
  size: number;
  kind: DocKind;
  /** An idea worked by the strong model, not a raw import. */
  developed: boolean;
}

export interface ProjectBundle {
  slug: string;
  title: string;
  markdown: string;
  doc_count: number;
}

export interface LibraryDocContent {
  path: string;
  title: string;
  content: string;
  mtime: number;
}

export interface ImportedIdea {
  path: string;
  title: string;
  developed: boolean;
  /** Why a requested development pass didn't happen — surfaced, never swallowed. */
  note: string;
}

export interface ImportResult {
  project: LibraryProject;
  imported: ImportedIdea[];
}

export interface ImportIdeaInput {
  project_id: string;
  node_id: string;
  title: string;
  markdown: string;
}

export function getLibraryRoot(): Promise<{ root: string }> {
  return request<{ root: string }>("/api/library/root");
}

export function listLibraryProjects(): Promise<LibraryProject[]> {
  return request<LibraryProject[]>("/api/library/projects");
}

export function createLibraryProject(title: string, thesis = ""): Promise<LibraryProject> {
  return request<LibraryProject>("/api/library/projects", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ title, thesis }),
  });
}

export function getLibraryProject(slug: string): Promise<LibraryProject> {
  return request<LibraryProject>(`/api/library/projects/${encodeURIComponent(slug)}`);
}

export function setProjectStatus(slug: string, status: string): Promise<LibraryProject> {
  return request<LibraryProject>(`/api/library/projects/${encodeURIComponent(slug)}/status`, {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ status }),
  });
}

export function listLibraryDocs(slug: string): Promise<LibraryDoc[]> {
  return request<LibraryDoc[]>(`/api/library/projects/${encodeURIComponent(slug)}/docs`);
}

export function readLibraryDoc(slug: string, path: string): Promise<LibraryDocContent> {
  return request<LibraryDocContent>(
    `/api/library/projects/${encodeURIComponent(slug)}/doc?path=${encodeURIComponent(path)}`,
  );
}

/** Every document of a project as one portable markdown blob (copy-whole-project). */
export function bundleLibraryProject(slug: string): Promise<ProjectBundle> {
  return request<ProjectBundle>(
    `/api/library/projects/${encodeURIComponent(slug)}/bundle`,
  );
}

export function writeLibraryDoc(
  slug: string,
  path: string,
  content: string,
  baseMtime: number | null,
): Promise<LibraryDoc> {
  return request<LibraryDoc>(`/api/library/projects/${encodeURIComponent(slug)}/doc`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ path, content, base_mtime: baseMtime }),
  });
}

/** Import starred ideas into a NEW project (W-3). */
export function importIdeasToNewProject(
  ideas: ImportIdeaInput[],
  newProjectTitle: string,
  develop: boolean,
): Promise<ImportResult> {
  return request<ImportResult>("/api/library/import", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ ideas, develop, new_project_title: newProjectTitle }),
  });
}

/** Add starred ideas to an EXISTING project — a project holds many ideas. */
export function importIdeasInto(
  slug: string,
  ideas: ImportIdeaInput[],
  develop: boolean,
): Promise<ImportResult> {
  return request<ImportResult>(`/api/library/projects/${encodeURIComponent(slug)}/import`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ ideas, develop }),
  });
}


/** Consolidate a project's ideas into one thesis + plan (W-4). Writes
 *  consolidation.md; returns its path. Slow (one strong-model pass). */
export function consolidateProject(slug: string): Promise<{ path: string; title: string }> {
  return request<{ path: string; title: string }>(
    `/api/library/projects/${encodeURIComponent(slug)}/consolidate`,
    { method: "POST", headers: JSON_HEADERS },
  );
}

/** Red-team the assembled project: 7 lenses + web search, scored, plus a
 *  falsification plan. Writes critique.md. Slow (one web-enabled pass). */
export function critiqueProject(slug: string): Promise<CritiqueResult> {
  return request<CritiqueResult>(
    `/api/library/projects/${encodeURIComponent(slug)}/critique`,
    { method: "POST", headers: JSON_HEADERS },
  );
}
