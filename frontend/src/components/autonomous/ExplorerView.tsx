import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  ApiError,
  control,
  createProject,
  deleteProject,
  listProjects,
  subscribeEvents,
  type ControlRequest,
} from "../../autonomous/api";
import {
  hasSteeringContext,
  type Budget,
  type CreateProjectRequest,
  type ExplorerEvent,
  type ExplorerMode,
  type Pace,
  type Project,
  type ScoutCandidate,
  type TreeNode,
  type TreeSnapshot,
} from "../../autonomous/types";
import ExplorationSidebar from "./ExplorationSidebar";
import ExplorationTree from "./ExplorationTree";
import GraphCanvas from "./GraphCanvas";
import EvolutionGraph from "./EvolutionGraph";
import NodeInspector from "./NodeInspector";
import RunControls from "./RunControls";
import UsageMeter from "./UsageMeter";
import LiveActivity, { type Sample } from "./LiveActivity";
import GlobalUsageBar from "./GlobalUsageBar";
import HomeDashboard from "./HomeDashboard";
import ProjectDigest from "./ProjectDigest";
import RunDigestCard from "./RunDigestCard";
import { statusMeta } from "./statusMeta";
import type { Route } from "../../hooks/useHashRoute";

import NewExplorationDialog, { type Depth, type DialogPrefill } from "./NewExplorationDialog";
export type { Depth };


/* -------------------------------------------------------------- live series -- */
// A time-series of each project's stats, folded from the SSE stream, so the UI
// can *show the exploration happening* (streaming spend/throughput/frontier
// graphs) instead of a spinner. Kept small + capped; purely client-side.
const HISTORY_CAP = 400;

function avgViabilityOf(nodes: Record<string, TreeNode>): number {
  let sum = 0;
  let count = 0;
  for (const n of Object.values(nodes)) {
    if ((n.kind === "gap" || n.kind === "gap_candidate") && n.viability != null) {
      sum += n.viability;
      count += 1;
    }
  }
  return count > 0 ? Math.round(sum / count) : 0;
}

function sampleOf(project: Project, nodes: Record<string, TreeNode>, atMs: number): Sample {
  const s = project.stats;
  return {
    t: atMs,
    tokens: s.tokens_spent,
    nodes: s.nodes,
    gaps: s.gaps,
    stars: s.stars,
    candidates: s.candidates,
    frontier: s.frontier_size,
    maxViability: s.max_viability,
    avgViability: avgViabilityOf(nodes),
    mode: s.mode as ExplorerMode,
  };
}

function pushSample(history: Sample[], next: Sample): Sample[] {
  const last = history[history.length - 1];
  // Collapse near-duplicate frames (same counters within 700ms) so the graph
  // reflects real work, not SSE chatter — but always keep mode flips.
  if (
    last &&
    last.tokens === next.tokens &&
    last.nodes === next.nodes &&
    last.gaps === next.gaps &&
    last.stars === next.stars &&
    last.mode === next.mode &&
    next.t - last.t < 700
  ) {
    return history;
  }
  const out = history.length >= HISTORY_CAP ? history.slice(history.length - HISTORY_CAP + 1) : history.slice();
  out.push(next);
  return out;
}

function atMs(iso: string): number {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? Date.now() : t;
}

function titleKind(kind: string): string {
  return kind.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function fmtTok(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${n}`;
}

/* ------------------------------------------------------------------ store -- */
// A live, client-side mirror of every project's event-sourced tree. Hydrated
// from the SSE `snapshot` frame, then folded forward from node/project events.
interface PState {
  project: Project;
  nodes: Record<string, TreeNode>;
  lastSeq: number;
  history: Sample[];
}
interface StoreState {
  byId: Record<string, PState>;
  order: string[];
}
type Action =
  | { type: "setProjects"; projects: Project[] }
  | { type: "hydrate"; snapshot: TreeSnapshot }
  | { type: "event"; ev: ExplorerEvent }
  | { type: "patchProject"; project: Project }
  | { type: "removeProject"; id: string };

function reduce(state: StoreState, action: Action): StoreState {
  switch (action.type) {
    case "setProjects": {
      const order = action.projects.map((p) => p.id);
      const byId: Record<string, PState> = {};
      for (const p of action.projects) {
        const prev = state.byId[p.id];
        byId[p.id] = prev
          ? { ...prev, project: p }
          : { project: p, nodes: {}, lastSeq: -1, history: [] };
      }
      return { byId, order };
    }
    case "hydrate": {
      const { project, nodes, last_seq } = action.snapshot;
      const map: Record<string, TreeNode> = {};
      for (const n of nodes) map[n.id] = n;
      const prev = state.byId[project.id];
      // Preserve any history already gathered, then seed a current point so the
      // graph has an anchor even before the first live event lands.
      const history = pushSample(prev?.history ?? [], sampleOf(project, map, Date.now()));
      return {
        ...state,
        byId: {
          ...state.byId,
          [project.id]: { project, nodes: map, lastSeq: last_seq, history },
        },
      };
    }
    case "event": {
      const ev = action.ev;
      const ps = state.byId[ev.project_id];
      if (!ps || ev.seq <= ps.lastSeq) return state;
      const nodes = ev.node ? { ...ps.nodes, [ev.node.id]: ev.node } : ps.nodes;
      const project = ev.project ?? ps.project;
      // Every event that carries fresh project stats becomes a graph sample.
      const history = ev.project
        ? pushSample(ps.history, sampleOf(ev.project, nodes, atMs(ev.at)))
        : ps.history;
      return {
        ...state,
        byId: {
          ...state.byId,
          [ev.project_id]: {
            project,
            nodes,
            lastSeq: Math.max(ps.lastSeq, ev.seq),
            history,
          },
        },
      };
    }
    case "patchProject": {
      const ps = state.byId[action.project.id];
      if (!ps) return state;
      return {
        ...state,
        byId: { ...state.byId, [action.project.id]: { ...ps, project: action.project } },
      };
    }
    case "removeProject": {
      const byId = { ...state.byId };
      delete byId[action.id];
      return { byId, order: state.order.filter((i) => i !== action.id) };
    }
  }
}

function errMsg(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback;
}

/* ============================================================ ExplorerView == */
/**
 * The whole autonomous screen (SPEC §10): a project tab bar, a live-updating
 * client-side tree store fed by SSE, the exploration tree on the left, the node
 * inspector / digest on the right, and per-project run controls + usage meter.
 * Several projects run at once — each has its own event subscription so every tab
 * stays live.
 */
interface ExplorerViewProps {
  route: Route;
  navHome: () => void;
  navProject: (projectId: string) => void;
  navNode: (projectId: string, nodeId: string) => void;
  navMode: (projectId: string, mode: "canvas" | "evolution" | "list") => void;
  /** ⌘K "new exploration" — open the new-exploration dialog when this bumps. */
  newExplorationSignal?: number;
}

export default function ExplorerView({ route, navHome, navProject, navNode, navMode, newExplorationSignal }: ExplorerViewProps) {
  const [state, dispatch] = useReducer(reduce, { byId: {}, order: [] });
  const [loaded, setLoaded] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [newDepth, setNewDepth] = useState<Depth>("standard");
  const [prefill, setPrefill] = useState<DialogPrefill | null>(null);
  const openNew = useCallback((depth: Depth) => {
    setNewDepth(depth);
    setPrefill(null);
    setShowNew(true);
  }, []);
  // Scout → drawer: launch the new-exploration dialog pre-seeded with a
  // suggested space (domain + sub-segments + rationale as the founder brief).
  const openScoutPrefill = useCallback((c: ScoutCandidate) => {
    setNewDepth("standard");
    setPrefill({
      domain: c.domain,
      segs: c.suggested_sub_segments,
      brief: c.rationale,
    });
    setShowNew(true);
  }, []);
  const [busy, setBusy] = useState(false);
  // Digest → follow-up: launch a new exploration prefilled to chase one of the
  // finished run's open questions (H4). Steering, not spend — autostart stays
  // the user's call inside the dialog.
  const openFollowUp = useCallback((project: Project, question: string) => {
    setNewDepth("standard");
    setPrefill({
      domain: project.domain,
      segs: project.sub_segments,
      brief: `Follow-up run on "${project.domain}". Open question from the previous run: ${question}`,
    });
    setShowNew(true);
  }, []);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<"overview" | "nodes">("nodes");

  // Navigation is derived from the URL — never held as separate state.
  const activeId = route.view === "exploration" ? route.projectId : null;
  const selectedId = route.view === "exploration" ? route.nodeId : null;
  // The tree draw-mode is a routable, deep-linkable part of the URL (#/e/:id,
  // #/e/:id/evolution, #/e/:id/list) — not local state — so it survives refresh,
  // is shareable, and the browser back button walks the views.
  const treeMode: "canvas" | "list" | "evolution" =
    route.view === "exploration" ? (route.mode ?? "canvas") : "canvas";
  const setTreeMode = (mode: "canvas" | "list" | "evolution") => {
    if (activeId) navMode(activeId, mode);
  };

  const orderSig = state.order.join(",");

  const refresh = useCallback(async () => {
    try {
      const ps = await listProjects();
      dispatch({ type: "setProjects", projects: ps });
    } catch (e) {
      setErr(errMsg(e, "Could not load explorations."));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Keep the whole list fresh (status/stats for every row in the sidebar) with a
  // light poll — cheap, and far kinder than holding an SSE stream open for every
  // project at once (which never lets the network idle and doesn't scale).
  useEffect(() => {
    const t = window.setInterval(refresh, 4000);
    return () => window.clearInterval(t);
  }, [refresh]);

  // Stream live events for the ACTIVE exploration only — that's the one whose
  // tree + graphs are on screen. Its snapshot re-hydrates on every (re)connect.
  useEffect(() => {
    if (!activeId) return;
    const unsub = subscribeEvents(activeId, {
      onSnapshot: (snapshot) => dispatch({ type: "hydrate", snapshot }),
      onEvent: (ev) => dispatch({ type: "event", ev }),
    });
    return () => unsub();
  }, [activeId]);

  // Opening a project always lands on the Nodes tab. Selection lives in the URL.
  useEffect(() => {
    if (activeId) setView("nodes");
  }, [activeId]);

  // ⌘K: open the new-exploration dialog (ignore the initial 0).
  useEffect(() => {
    if (newExplorationSignal && newExplorationSignal > 0) openNew("standard");
  }, [newExplorationSignal, openNew]);

  // A URL that points at a project we don't have (deleted, or a bad deep link)
  // falls back home — but only once the list has actually loaded, so a valid
  // deep link isn't bounced before its project arrives.
  useEffect(() => {
    if (loaded && activeId && !state.byId[activeId]) navHome();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderSig, loaded, activeId]);

  const active = activeId ? state.byId[activeId] : null;
  const allProjects = useMemo(
    () => state.order.map((id) => state.byId[id]?.project).filter(Boolean) as Project[],
    [state]
  );

  const selectNode = useCallback(
    (id: string | null) => {
      if (!activeId) return;
      if (id) navNode(activeId, id);
      else navProject(activeId);
    },
    [activeId, navNode, navProject]
  );

  const selectedNode = active && selectedId ? active.nodes[selectedId] ?? null : null;
  const rootId = useMemo(() => {
    if (!active) return null;
    return Object.values(active.nodes).find((n) => n.parent_id == null)?.id ?? null;
  }, [active]);
  const selectedChildren = useMemo(() => {
    if (!active || !selectedNode) return [];
    return Object.values(active.nodes)
      .filter((n) => n.parent_id === selectedNode.id)
      .sort((a, b) => (b.viability ?? -1) - (a.viability ?? -1) || b.priority - a.priority);
  }, [active, selectedNode]);

  /* --------------------------------------------------------------- actions -- */
  const onControl = useCallback(
    async (req: ControlRequest) => {
      if (!activeId) return;
      setBusy(true);
      setErr(null);
      try {
        const p = await control(activeId, req);
        dispatch({ type: "patchProject", project: p });
      } catch (e) {
        setErr(errMsg(e, "That control action failed."));
      } finally {
        setBusy(false);
      }
    },
    [activeId]
  );

  const onCreate = useCallback(
    async (req: CreateProjectRequest) => {
      const p = await createProject(req);
      await refresh();
      navProject(p.id);
      setShowNew(false);
    },
    [refresh, navProject]
  );

  // Delete by id (the sidebar row asks for confirmation inline — no blocking
  // browser dialog). Remove locally at once for a snappy feel; if it was the
  // active tab, fall through to the next exploration.
  const onDelete = useCallback(
    async (id: string) => {
      try {
        await deleteProject(id);
        dispatch({ type: "removeProject", id });
        if (activeId === id) navHome();
      } catch (e) {
        setErr(errMsg(e, "Could not delete the exploration."));
        refresh();
      }
    },
    [activeId, navHome, refresh]
  );

  /* ------------------------------------------------------------------ view -- */
  const project = active?.project ?? null;
  // One node count everywhere: the hydrated client tree is the authoritative set
  // (server-side stats.nodes can drift on old runs); fall back to stats only
  // before the SSE snapshot lands so the strip never flashes 0.
  const nodeCount = active
    ? Object.keys(active.nodes).length || active.project.stats.nodes
    : 0;
  const projectMeta = project ? statusMeta(project) : null;

  return (
    <div className="explorer">
      {err && (
        <div className="exp-error" role="alert">
          <span className="w-ico">⚠︎</span>
          {err}
          <button className="exp-error-x" onClick={() => setErr(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}

      <div className="exp-shell">
        <ExplorationSidebar
          projects={allProjects}
          activeId={activeId}
          onSelect={navProject}
          onNew={() => openNew("standard")}
          onDelete={onDelete}
          onHome={navHome}
        />

        <div className="exp-main">
          {!project ? (
            <HomeDashboard
              projects={allProjects}
              onOpen={navProject}
              onOpenNode={navNode}
              onNew={() => openNew("standard")}
              onQuickNew={() => openNew("quick")}
              onExploreCandidate={openScoutPrefill}
            />
          ) : (
            <>
              <nav className="exp-crumbs" aria-label="Breadcrumb">
                <button className="crumb" onClick={navHome}>Dashboard</button>
                <span className="crumb-sep" aria-hidden>›</span>
                <button
                  className={`crumb${!selectedNode ? " current" : ""}`}
                  onClick={() => activeId && navProject(activeId)}
                  aria-current={!selectedNode ? "page" : undefined}
                >
                  {project.domain}
                </button>
                {selectedNode && (
                  <>
                    <span className="crumb-sep" aria-hidden>›</span>
                    <span className="crumb current" aria-current="page">
                      {selectedNode.kind === "gap" || selectedNode.kind === "gap_candidate"
                        ? "Idea" : titleKind(selectedNode.kind)}
                    </span>
                  </>
                )}
              </nav>

              <div className="exp-topline">
                <div className="exp-title">
                  <h2>{project.domain}</h2>
                  {project.sub_segments.length > 0 && (
                    <span className="exp-subseg">{project.sub_segments.join(" · ")}</span>
                  )}
                </div>
                {/* slim always-visible live status (the heavy graphs live in Overview) */}
                <div className="exp-statstrip">
                  <span className={`ss-mode ${projectMeta!.dot}`}>
                    <span className="ss-dot" />{projectMeta!.word}
                  </span>
                  <span className="ss-stat"><b>{nodeCount}</b> nodes</span>
                  <span className="ss-stat"><b>{project.stats.gaps}</b> gaps</span>
                  {project.stats.stars > 0 && <span className="ss-stat star"><b>{project.stats.stars}</b>★</span>}
                  <span className="ss-stat"><b>{project.stats.max_viability}</b> top</span>
                  <span className="ss-stat dim">{fmtTok(project.stats.tokens_spent)} tok</span>
                </div>
              </div>

              {/* Tab strip: Overview (digest + controls) vs Nodes (the tree).
                  Selecting any idea opens a full-width detail page (like the
                  single-area gap page) with a breadcrumb back to the tree. */}
              <div className="exp-tabbar">
                <div className="exp-tabs" role="tablist" aria-label="Exploration views">
                  <button
                    role="tab" aria-selected={!selectedNode && view === "overview"}
                    className={`exp-tab${!selectedNode && view === "overview" ? " active" : ""}`}
                    onClick={() => { selectNode(null); setView("overview"); }}
                  >
                    Overview
                  </button>
                  <button
                    role="tab" aria-selected={!selectedNode && view === "nodes"}
                    className={`exp-tab${!selectedNode && view === "nodes" ? " active" : ""}`}
                    onClick={() => { selectNode(null); setView("nodes"); }}
                  >
                    Nodes <span className="exp-tab-n">{nodeCount}</span>
                  </button>
                </div>
                <div className="exp-tab-controls">
                  <RunControls project={project} busy={busy} onControl={onControl} compact />
                </div>
              </div>

              {selectedNode ? (
                <div className="card exp-detail">
                  <NodeInspector
                    node={selectedNode}
                    childNodes={selectedChildren}
                    onSelectChild={selectNode}
                    hasSteering={hasSteeringContext(project.steering)}
                    busy={busy}
                    projectId={project.id}
                    project={project}
                    onTogglePin={(nodeId, pinned) =>
                      onControl({ action: pinned ? "pin_node" : "unpin_node", node_id: nodeId })
                    }
                    onToggleStar={(nodeId, starred) =>
                      onControl({
                        action: starred ? "star_node" : "unstar_node",
                        node_id: nodeId,
                      })
                    }
                    onSetTriage={(nodeId, triage, reason) =>
                      onControl({
                        action: "set_triage",
                        node_id: nodeId,
                        triage,
                        triage_reason: reason,
                      })
                    }
                    onSetStage={(nodeId, stage, learnings) =>
                      onControl({ action: "set_stage", node_id: nodeId, stage, learnings })
                    }
                    onToggleWatch={(nodeId, watched) =>
                      onControl({
                        action: watched ? "watch_node" : "unwatch_node",
                        node_id: nodeId,
                      })
                    }
                  />
                </div>
              ) : view === "nodes" ? (
                <div className="card exp-canvas-card">
                  {treeMode === "canvas" ? (
                    <GraphCanvas
                      nodes={active!.nodes}
                      rootId={rootId}
                      selectedId={selectedId}
                      onSelect={selectNode}
                    />
                  ) : treeMode === "evolution" ? (
                    <EvolutionGraph
                      nodes={active!.nodes}
                      rootId={rootId}
                      selectedId={selectedId}
                      onSelect={selectNode}
                    />
                  ) : (
                    <ExplorationTree
                      nodes={active!.nodes}
                      rootId={rootId}
                      selectedId={selectedId}
                      onSelect={selectNode}
                    />
                  )}
                  <div className="canvas-viewswitch">
                    <button
                      className={treeMode === "canvas" ? "on" : ""}
                      onClick={() => setTreeMode("canvas")}
                      title="Infinite canvas"
                    >⛶ Canvas</button>
                    <button
                      className={treeMode === "evolution" ? "on" : ""}
                      onClick={() => setTreeMode("evolution")}
                      title="Live evolution — animated force graph"
                    >✺ Evolution</button>
                    <button
                      className={treeMode === "list" ? "on" : ""}
                      onClick={() => setTreeMode("list")}
                      title="Indented list"
                    >☰ List</button>
                  </div>
                </div>
              ) : (
                <>
                  <RunDigestCard
                    project={project}
                    onFollowUp={(q) => openFollowUp(project, q)}
                  />
                  <UsageMeter project={project} allProjects={allProjects} />
                  <LiveActivity project={project} history={active!.history} />
                  <div className="exp-overview">
                    <div className="card exp-panel exp-inspect">
                      <ProjectDigest nodes={active!.nodes} onSelect={selectNode} />
                    </div>
                    <div className="card exp-panel">
                      <RunControls project={project} busy={busy} onControl={onControl} />
                    </div>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>

      {state.order.length > 0 && <GlobalUsageBar projects={allProjects} />}

      {showNew && (
        <NewExplorationDialog
          initialDepth={newDepth}
          prefill={prefill}
          onClose={() => setShowNew(false)}
          onCreate={onCreate}
        />
      )}
    </div>
  );
}
