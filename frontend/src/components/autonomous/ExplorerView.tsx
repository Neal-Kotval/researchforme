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
  DEFAULT_BUDGET,
  type Budget,
  type CreateProjectRequest,
  type ExplorerEvent,
  type ExplorerMode,
  type Pace,
  type Project,
  type TreeNode,
  type TreeSnapshot,
} from "../../autonomous/types";
import ModelPicker from "../ModelPicker";
import ProjectTabs from "./ProjectTabs";
import ExplorationTree from "./ExplorationTree";
import NodeInspector from "./NodeInspector";
import RunControls from "./RunControls";
import UsageMeter from "./UsageMeter";
import LiveActivity, { type Sample } from "./LiveActivity";
import GlobalUsageBar from "./GlobalUsageBar";
import ProjectDigest from "./ProjectDigest";

/* -------------------------------------------------------------- live series -- */
// A time-series of each project's stats, folded from the SSE stream, so the UI
// can *show the exploration happening* (streaming spend/throughput/frontier
// graphs) instead of a spinner. Kept small + capped; purely client-side.
const HISTORY_CAP = 400;

function sampleOf(project: Project, atMs: number): Sample {
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
      const history = pushSample(prev?.history ?? [], sampleOf(project, Date.now()));
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
        ? pushSample(ps.history, sampleOf(ev.project, atMs(ev.at)))
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
export default function ExplorerView() {
  const [state, dispatch] = useReducer(reduce, { byId: {}, order: [] });
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selByProject, setSelByProject] = useState<Record<string, string | null>>({});
  const [showNew, setShowNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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

  // One SSE subscription per project; re-established whenever the set changes.
  useEffect(() => {
    if (!state.order.length) return;
    const unsubs = state.order.map((id) =>
      subscribeEvents(id, {
        onSnapshot: (snapshot) => dispatch({ type: "hydrate", snapshot }),
        onEvent: (ev) => dispatch({ type: "event", ev }),
      })
    );
    return () => unsubs.forEach((u) => u());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderSig]);

  // Keep an active tab valid as projects come and go.
  useEffect(() => {
    if (!state.order.length) {
      if (activeId !== null) setActiveId(null);
      return;
    }
    if (!activeId || !state.byId[activeId]) setActiveId(state.order[0]);
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

  const onDelete = useCallback(async () => {
    if (!activeId) return;
    if (!window.confirm("Delete this exploration and its whole tree? This cannot be undone.")) return;
    const id = activeId;
    try {
      await deleteProject(id);
      dispatch({ type: "removeProject", id });
    } catch (e) {
      setErr(errMsg(e, "Could not delete the exploration."));
    }
  }, [activeId]);

  /* ------------------------------------------------------------------ view -- */
  const project = active?.project ?? null;

  return (
    <div className="explorer">
      <ProjectTabs
        projects={allProjects}
        activeId={activeId}
        onSelect={setActiveId}
        onNew={() => setShowNew(true)}
      />

      {err && (
        <div className="exp-error" role="alert">
          <span className="w-ico">⚠︎</span>
          {err}
          <button className="exp-error-x" onClick={() => setErr(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}

      {!project ? (
        <div className="explorer-empty">
          <div className="ee-mark">◇</div>
          <h2>Spawn an autonomous explorer</h2>
          <p>
            Give it a domain and walk away. It recursively decomposes the space, mines signals,
            hypothesizes gaps, pressure-tests the promising ones, and scores each 0–100 — spending
            more effort where it's paying off.
          </p>
          <button className="btn btn-primary" onClick={() => setShowNew(true)}>
            ＋ New exploration
          </button>
        </div>
      ) : (
        <div className="exp-layout">
          <div className="exp-left">
            <div className="exp-topline">
              <div className="exp-title">
                <h2>{project.domain}</h2>
                {project.sub_segments.length > 0 && (
                  <span className="exp-subseg">{project.sub_segments.join(" · ")}</span>
                )}
              </div>
              <button className="exp-del" onClick={onDelete} title="Delete exploration">
                ✕ Delete
              </button>
            </div>

            <UsageMeter project={project} allProjects={allProjects} />

            <LiveActivity project={project} history={active!.history} />

            <div className="card exp-tree-card">
              <ExplorationTree
                nodes={active!.nodes}
                rootId={rootId}
                selectedId={selectedId}
                onSelect={selectNode}
              />
            </div>
          </div>

          <aside className="exp-right">
            <div className="card exp-panel">
              <RunControls project={project} busy={busy} onControl={onControl} />
            </div>

            <div className="card exp-panel exp-inspect">
              {selectedNode ? (
                <>
                  <button className="inspect-back" onClick={() => selectNode(null)}>
                    ‹ Back to digest
                  </button>
                  <NodeInspector
                    node={selectedNode}
                    childNodes={selectedChildren}
                    onSelectChild={selectNode}
                  />
                </>
              ) : (
                <ProjectDigest nodes={active!.nodes} onSelect={selectNode} />
              )}
            </div>
          </aside>
        </div>
      )}

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
      await onCreate({
        domain: domain.trim(),
        sub_segments: segs,
        budget,
        decompose_model: decompose,
        synth_model: synth,
        pressure_model: pressure,
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
