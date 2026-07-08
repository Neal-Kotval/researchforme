import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  ApiError,
  control,
  createProject,
  deleteProject,
  getIntake,
  listProjects,
  subscribeEvents,
  type ControlRequest,
} from "../../autonomous/api";
import {
  DEFAULT_BUDGET,
  type Budget,
  type CreateProjectRequest,
  type ExplorerEvent,
  type ExplorerMode,
  type IntakeQuestion,
  type Pace,
  type Project,
  type TreeNode,
  type TreeSnapshot,
} from "../../autonomous/types";
import ModelPicker from "../ModelPicker";
import ExplorationSidebar from "./ExplorationSidebar";
import ExplorationTree from "./ExplorationTree";
import GraphCanvas from "./GraphCanvas";
import NodeInspector from "./NodeInspector";
import RunControls from "./RunControls";
import UsageMeter from "./UsageMeter";
import LiveActivity, { type Sample } from "./LiveActivity";
import GlobalUsageBar from "./GlobalUsageBar";
import HomeDashboard from "./HomeDashboard";
import ProjectDigest from "./ProjectDigest";

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
  /** ⌘K "jump to exploration" — focus this project when it changes. */
  focusProjectId?: string | null;
  /** ⌘K "new exploration" — open the new-exploration dialog when this bumps. */
  newExplorationSignal?: number;
  /** App-shell mode nav (rendered at the top of the sidebar). */
  mode?: "single" | "autonomous";
  setMode?: (m: "single" | "autonomous") => void;
}

export default function ExplorerView({ focusProjectId, newExplorationSignal, mode, setMode }: ExplorerViewProps = {}) {
  const [state, dispatch] = useReducer(reduce, { byId: {}, order: [] });
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selByProject, setSelByProject] = useState<Record<string, string | null>>({});
  const [showNew, setShowNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<"overview" | "nodes">("nodes");
  const [treeMode, setTreeMode] = useState<"canvas" | "list">("canvas");

  const orderSig = state.order.join(",");

  const refresh = useCallback(async () => {
    try {
      const ps = await listProjects();
      dispatch({ type: "setProjects", projects: ps });
    } catch (e) {
      setErr(errMsg(e, "Could not load explorations."));
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

  // Opening a project always lands on the Nodes/tree with no stale idea selected
  // (a lingering per-project selection used to dump you straight into a full-page
  // idea detail). Clear the selection and reset the tab on every project switch.
  useEffect(() => {
    if (activeId) {
      setSelByProject((prev) => (prev[activeId] ? { ...prev, [activeId]: null } : prev));
      setView("nodes");
    }
  }, [activeId]);

  // ⌘K: jump to a specific exploration (refresh first if we don't have it yet).
  useEffect(() => {
    if (!focusProjectId) return;
    setActiveId(focusProjectId);
    if (!state.byId[focusProjectId]) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusProjectId]);

  // ⌘K: open the new-exploration dialog (ignore the initial 0).
  useEffect(() => {
    if (newExplorationSignal && newExplorationSignal > 0) setShowNew(true);
  }, [newExplorationSignal]);

  // Default to the Home dashboard (activeId === null); only clear a selection
  // that points at a now-deleted project. Never auto-jump into a project, so the
  // landing surface is the "glance at everything" home.
  useEffect(() => {
    if (activeId && !state.byId[activeId]) setActiveId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderSig]);

  const active = activeId ? state.byId[activeId] : null;
  const allProjects = useMemo(
    () => state.order.map((id) => state.byId[id]?.project).filter(Boolean) as Project[],
    [state]
  );

  const selectedId = activeId ? selByProject[activeId] ?? null : null;
  const selectNode = useCallback(
    (id: string | null) => {
      if (!activeId) return;
      setSelByProject((prev) => ({ ...prev, [activeId]: id }));
    },
    [activeId]
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
      setActiveId(p.id);
      setShowNew(false);
    },
    [refresh]
  );

  // Delete by id (the sidebar row asks for confirmation inline — no blocking
  // browser dialog). Remove locally at once for a snappy feel; if it was the
  // active tab, fall through to the next exploration.
  const onDelete = useCallback(
    async (id: string) => {
      try {
        await deleteProject(id);
        dispatch({ type: "removeProject", id });
        setActiveId((cur) => (cur === id ? state.order.find((x) => x !== id) ?? null : cur));
      } catch (e) {
        setErr(errMsg(e, "Could not delete the exploration."));
        refresh();
      }
    },
    [state.order, refresh]
  );

  /* ------------------------------------------------------------------ view -- */
  const project = active?.project ?? null;

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
          onSelect={setActiveId}
          onNew={() => setShowNew(true)}
          onDelete={onDelete}
          onHome={() => setActiveId(null)}
          mode={mode}
          setMode={setMode}
        />

        <div className="exp-main">
          {!project ? (
            <HomeDashboard
              projects={allProjects}
              onOpen={setActiveId}
              onNew={() => setShowNew(true)}
            />
          ) : (
            <>
              <div className="exp-topline">
                <div className="exp-title">
                  <h2>{project.domain}</h2>
                  {project.sub_segments.length > 0 && (
                    <span className="exp-subseg">{project.sub_segments.join(" · ")}</span>
                  )}
                </div>
                {/* slim always-visible live status (the heavy graphs live in Overview) */}
                <div className="exp-statstrip">
                  <span className={`ss-mode ${project.stats.mode}`}>
                    <span className="ss-dot" />{project.stats.mode}
                  </span>
                  <span className="ss-stat"><b>{project.stats.nodes}</b> nodes</span>
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
                    Nodes <span className="exp-tab-n">{Object.keys(active!.nodes).length}</span>
                  </button>
                  {selectedNode && (
                    <button role="tab" aria-selected className="exp-tab active idea">
                      {selectedNode.kind === "gap" || selectedNode.kind === "gap_candidate"
                        ? "Idea" : titleKind(selectedNode.kind)}
                    </button>
                  )}
                </div>
                <div className="exp-tab-controls">
                  <RunControls project={project} busy={busy} onControl={onControl} compact />
                </div>
              </div>

              {selectedNode ? (
                <div className="card exp-detail">
                  <button className="inspect-back" onClick={() => selectNode(null)}>
                    ‹ Back to {view === "overview" ? "overview" : "nodes"}
                  </button>
                  <NodeInspector
                    node={selectedNode}
                    childNodes={selectedChildren}
                    onSelectChild={selectNode}
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
                      className={treeMode === "list" ? "on" : ""}
                      onClick={() => setTreeMode("list")}
                      title="Indented list"
                    >☰ List</button>
                  </div>
                </div>
              ) : (
                <>
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
        <NewExplorationDialog onClose={() => setShowNew(false)} onCreate={onCreate} />
      )}
    </div>
  );
}

/* =================================================== New exploration dialog == */
interface DialogProps {
  onClose: () => void;
  onCreate: (req: CreateProjectRequest) => Promise<void>;
}

function NewExplorationDialog({ onClose, onCreate }: DialogProps) {
  const [domain, setDomain] = useState("");
  const [segs, setSegs] = useState<string[]>([]);
  const [segText, setSegText] = useState("");
  const [decompose, setDecompose] = useState("claude-haiku-4-5-20251001");
  const [synth, setSynth] = useState("claude-opus-4-8");
  const [pressure, setPressure] = useState("claude-opus-4-8");
  const [budget, setBudget] = useState<Budget>({ ...DEFAULT_BUDGET });
  const [autostart, setAutostart] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Preflight intake: generated clarifying questions that steer the exploration.
  const [questions, setQuestions] = useState<IntakeQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [intakeLoading, setIntakeLoading] = useState(false);

  const refineWithQuestions = async () => {
    if (domain.trim().length < 2) {
      setError("Enter a domain first, then refine.");
      return;
    }
    setIntakeLoading(true);
    setError(null);
    try {
      const { questions: qs } = await getIntake(domain.trim());
      setQuestions(qs);
    } catch (e) {
      setError(errMsg(e, "Could not load intake questions."));
    } finally {
      setIntakeLoading(false);
    }
  };

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const addSeg = () => {
    const v = segText.trim().replace(/,$/, "");
    if (v && !segs.includes(v)) setSegs([...segs, v]);
    setSegText("");
  };
  const setBudgetNum = (k: keyof Budget, raw: string) => {
    const n = parseInt(raw, 10);
    setBudget((b) => ({ ...b, [k]: raw.trim() === "" || Number.isNaN(n) ? null : n }));
  };

  const submit = async () => {
    if (domain.trim().length < 2) {
      setError("Give the explorer a domain (2+ characters).");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const intake = Object.fromEntries(
        Object.entries(answers).filter(([, v]) => v && v.trim())
      );
      await onCreate({
        domain: domain.trim(),
        sub_segments: segs,
        budget,
        decompose_model: decompose,
        synth_model: synth,
        pressure_model: pressure,
        intake,
        autostart,
      });
    } catch (e) {
      setError(errMsg(e, "Could not start the exploration."));
      setSubmitting(false);
    }
  };

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <div className="new-dialog" role="dialog" aria-modal="true" aria-label="New exploration">
        <div className="nd-head">
          <div>
            <div className="eyebrow">Autonomous mode</div>
            <h3>New exploration</h3>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="nd-body">
          <label className="field-label">
            Domain <span className="req">*</span>
          </label>
          <input
            ref={inputRef}
            className="big-input"
            placeholder="e.g. embedded AI"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && submit()}
          />

          {/* preflight intake — clarifying questions that steer the exploration */}
          <div className="nd-intake">
            <button
              type="button"
              className="nd-refine"
              onClick={refineWithQuestions}
              disabled={intakeLoading}
            >
              {intakeLoading ? "Thinking…" : questions.length ? "↻ Regenerate questions" : "✦ Refine with questions"}
            </button>
            <span className="nd-refine-hint">
              {questions.length ? "Answer any that help — they steer the tree." : "Let it ask a few sharp questions first (optional)."}
            </span>
          </div>

          {questions.length > 0 && (
            <div className="nd-questions">
              {questions.map((q, i) => (
                <div className="nd-q" key={i}>
                  <div className="nd-q-label">{q.question}</div>
                  <div className="nd-q-chips">
                    {q.suggestions.map((s) => (
                      <button
                        type="button"
                        key={s}
                        className={`nd-q-chip${answers[q.question] === s ? " on" : ""}`}
                        onClick={() =>
                          setAnswers((a) => ({ ...a, [q.question]: a[q.question] === s ? "" : s }))
                        }
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                  <input
                    className="nd-q-free"
                    placeholder="…or type your own"
                    value={q.suggestions.includes(answers[q.question]) ? "" : answers[q.question] ?? ""}
                    onChange={(e) => setAnswers((a) => ({ ...a, [q.question]: e.target.value }))}
                  />
                </div>
              ))}
            </div>
          )}

          <label className="field-label" style={{ marginTop: 16 }}>
            Seed sub-segments <span className="nd-opt">(optional)</span>
          </label>
          <div className="chips-input">
            {segs.map((s) => (
              <span className="seg-chip" key={s}>
                {s}
                <button onClick={() => setSegs(segs.filter((x) => x !== s))} aria-label={`Remove ${s}`}>
                  ×
                </button>
              </span>
            ))}
            <input
              value={segText}
              onChange={(e) => setSegText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === ",") {
                  e.preventDefault();
                  addSeg();
                } else if (e.key === "Backspace" && !segText && segs.length) {
                  setSegs(segs.slice(0, -1));
                }
              }}
              onBlur={addSeg}
              placeholder={segs.length ? "" : "Add a segment, press Enter"}
            />
          </div>

          {/* pace */}
          <div className="nd-row">
            <div className="nd-col">
              <label className="field-label">Pace</label>
              <div className="seg-control" role="radiogroup" aria-label="Pace">
                {(["eco", "balanced", "sprint"] as Pace[]).map((p) => (
                  <button
                    key={p}
                    type="button"
                    role="radio"
                    aria-checked={budget.pace === p}
                    aria-pressed={budget.pace === p}
                    className="seg-option"
                    onClick={() => setBudget((b) => ({ ...b, pace: p }))}
                  >
                    <span className="so-label" style={{ textTransform: "capitalize" }}>
                      {p}
                    </span>
                  </button>
                ))}
              </div>
            </div>
            <div className="nd-col">
              <div className="slider-top">
                <label className="field-label" style={{ margin: 0 }}>
                  Star threshold
                </label>
                <span className="slider-val">≥ {budget.star_threshold}</span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={budget.star_threshold}
                onChange={(e) => setBudget((b) => ({ ...b, star_threshold: Number(e.target.value) }))}
              />
            </div>
          </div>

          {/* models */}
          <label className="field-label" style={{ marginTop: 18 }}>
            Model policy
          </label>
          <div className="nd-models">
            <div className="nd-model">
              <span className="ndm-lab">Decompose</span>
              <ModelPicker value={decompose} onChange={setDecompose} compact />
            </div>
            <div className="nd-model">
              <span className="ndm-lab">Synthesize</span>
              <ModelPicker value={synth} onChange={setSynth} compact />
            </div>
            <div className="nd-model">
              <span className="ndm-lab">Pressure-test</span>
              <ModelPicker value={pressure} onChange={setPressure} compact />
            </div>
          </div>

          {/* budget caps */}
          <label className="field-label" style={{ marginTop: 18 }}>
            Budget &amp; caps <span className="nd-opt">(blank = no cap)</span>
          </label>
          <div className="nd-budget">
            {(
              [
                ["max_tokens", "Max tokens", "∞"],
                ["daily_cap_tokens", "Daily cap", "∞"],
                ["max_nodes", "Max nodes", "∞"],
                ["time_limit_minutes", "Time limit (min)", "∞"],
                ["milestone_tokens", "Milestone every", "off"],
              ] as [keyof Budget, string, string][]
            ).map(([k, label, ph]) => (
              <label className="rc-field" key={k}>
                <span>{label}</span>
                <input
                  type="number"
                  min={0}
                  placeholder={ph}
                  value={(budget[k] as number | null) ?? ""}
                  onChange={(e) => setBudgetNum(k, e.target.value)}
                />
              </label>
            ))}
          </div>

          <label className="nd-check">
            <input
              type="checkbox"
              checked={autostart}
              onChange={(e) => setAutostart(e.target.checked)}
            />
            Start exploring immediately
          </label>

          {error && <div className="nd-error">{error}</div>}
        </div>

        <div className="nd-foot">
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={submitting}>
            {submitting ? "Starting…" : autostart ? "Explore ▸" : "Create"}
          </button>
        </div>
      </div>
    </>
  );
}
