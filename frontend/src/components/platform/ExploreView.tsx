import { useEffect, useMemo, useRef, useState } from "react";
import { control, subscribeEvents } from "../../autonomous/api";
import {
  nodeTrust,
  normalizeDigest,
  type ExplorerEvent,
  type Project,
  type TreeNode,
  type TreeSnapshot,
} from "../../autonomous/types";
import { ACTIVE_STATUSES, latestActive } from "../../hooks/useProjects";
import FitChip from "../autonomous/FitChip";
import ViabChip from "../autonomous/ViabChip";

interface Props {
  projects: Project[];
  onOpenProject: (pid: string) => void;
  onOpenNode: (pid: string, nodeId: string) => void;
  onNewExploration: () => void;
}

interface LogRow {
  at: string;
  message: string;
}

function fmtTok(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

function ago(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  if (Number.isNaN(s)) return "";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

/** Chip label for a feed row — the source when the message names one. */
function feedSrc(message: string): string {
  const m = message.toLowerCase();
  for (const s of ["reddit", "github", "arxiv", "hn", "hackernews", "newsletter"]) {
    if (m.includes(s)) return s === "hackernews" ? "hn" : s;
  }
  return "engine";
}

/** Right-column status word for a candidate row (mirrors the run states). */
function gapStatus(n: TreeNode): string {
  if (n.state === "pressure_testing") return "in red team";
  if (n.pressure_test && n.pressure_test.lenses.length > 0) {
    return `red team ${n.pressure_test.survived}/${n.pressure_test.lenses.length}`;
  }
  if (n.state === "scored") return "queued for red team";
  if (n.state === "synthesizing") return "synthesizing";
  if (n.state === "expanding" || n.state === "queued") return "mapping";
  return n.state.replace(/_/g, " ");
}

/**
 * Explore (design handoff §2): watch the agent hunt live. A crop-mark-framed
 * run summary with real controls, candidate gaps on the left, the live event
 * feed on the right — all fed by the run's SSE stream. The full tree stays a
 * click away at #/e/:pid.
 */
export default function ExploreView({ projects, onOpenProject, onOpenNode, onNewExploration }: Props) {
  const activeRuns = useMemo(
    () => projects.filter((p) => ACTIVE_STATUSES.has(p.status)),
    [projects]
  );
  const [selectedPid, setSelectedPid] = useState<string | null>(null);
  // Prefer the freshest active run; with nothing active, keep showing the most
  // recent run (final stats + a "done" pill) instead of dropping to empty.
  const newestAny = useMemo(
    () =>
      [...projects].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))[0] ??
      null,
    [projects]
  );
  const pid = selectedPid ?? latestActive(projects)?.id ?? newestAny?.id ?? null;

  // Live mirror of the selected run, folded from its SSE stream.
  const [project, setProject] = useState<Project | null>(null);
  const [nodes, setNodes] = useState<Record<string, TreeNode>>({});
  const [logs, setLogs] = useState<LogRow[]>([]);
  const seqRef = useRef(-1);

  useEffect(() => {
    if (!pid) return;
    setProject(null);
    setNodes({});
    setLogs([]);
    seqRef.current = -1;
    const off = subscribeEvents(pid, {
      onSnapshot: (snap: TreeSnapshot) => {
        setProject(snap.project);
        const map: Record<string, TreeNode> = {};
        for (const n of snap.nodes) map[n.id] = n;
        setNodes(map);
        seqRef.current = snap.last_seq;
      },
      onEvent: (ev: ExplorerEvent) => {
        if (ev.seq <= seqRef.current) return;
        seqRef.current = ev.seq;
        if (ev.project) setProject(ev.project);
        if (ev.node) setNodes((m) => ({ ...m, [ev.node!.id]: ev.node! }));
        if (ev.type === "log" && ev.message) {
          setLogs((l) => [{ at: ev.at, message: ev.message }, ...l].slice(0, 24));
        }
      },
    });
    return off;
  }, [pid]);

  const [busy, setBusy] = useState(false);
  const act = async (req: Parameters<typeof control>[1]) => {
    if (!pid) return;
    setBusy(true);
    try {
      setProject(await control(pid, req));
    } catch {
      /* the stream re-syncs state on the next event */
    } finally {
      setBusy(false);
    }
  };

  if (!pid) {
    return (
      <div className="pf-view">
        <div className="pf-empty">
          No active run to watch. Point the engine at a domain and this screen
          becomes its live feed.
          <br />
          <button className="btn btn-primary" onClick={onNewExploration}>＋ New exploration</button>
        </div>
      </div>
    );
  }

  const p = project ?? projects.find((x) => x.id === pid) ?? null;
  const running = p?.status === "running";
  const pausedFamily = p != null && ACTIVE_STATUSES.has(p.status) && !running;
  const mode = running
    ? (p!.stats.mode === "curbing" ? "curbing" : "sprinting")
    : pausedFamily
      ? "paused"
      : "done";
  const modeWord = mode === "done" ? (p?.status ?? "done").replace(/_/g, " ") : mode;
  const cap = p?.budget.max_tokens ?? null;

  const gaps = Object.values(nodes)
    .filter((n) => n.kind === "gap" || n.kind === "gap_candidate")
    .sort((a, b) => (b.viability ?? -1) - (a.viability ?? -1))
    .slice(0, 6);

  return (
    <div className="pf-view">
      {activeRuns.length > 1 && (
        <div className="pt-picker" role="tablist" aria-label="Active runs">
          {activeRuns.map((r) => (
            <button
              key={r.id}
              role="tab"
              aria-selected={r.id === pid}
              className={r.id === pid ? "on" : ""}
              onClick={() => setSelectedPid(r.id)}
            >
              {r.domain}
            </button>
          ))}
        </div>
      )}

      {/* Run summary — radius 0, crop-mark corner ticks (handoff spec) */}
      <div className="pf-run-summary">
        <span className="pf-tick tl" /><span className="pf-tick tr" />
        <span className="pf-tick bl" /><span className="pf-tick br" />
        <div className="pf-run-summary-head">
          <div className="pf-run-summary-title">
            <span>{p?.domain ?? "…"}</span>
            <span className={`pf-pill ${mode}`}>
              <span className={`pf-dot${running && mode === "sprinting" ? " pulse" : ""}`} />
              {modeWord}
            </span>
          </div>
          <div className="pf-run-summary-actions">
            {running && (
              <button className="btn" disabled={busy} onClick={() => act({ action: "pause" })}>
                Pause run
              </button>
            )}
            {pausedFamily && (
              <button className="btn" disabled={busy} onClick={() => act({ action: "resume" })}>
                Resume run
              </button>
            )}
            {(running || pausedFamily) && (
              <button
                className="btn"
                disabled={busy || p?.budget.pace === "eco"}
                onClick={() => act({ action: "set_pace", pace: "eco" })}
                title="Drop the pace to eco — the governor spends slower"
              >
                Curb spend
              </button>
            )}
          </div>
        </div>
        <div className="pf-run-summary-stats">
          <span className="pf-stat"><b>{p?.stats.nodes ?? "—"}</b><small>nodes mapped</small></span>
          <span className="pf-stat accent"><b>{p?.stats.candidates ?? "—"}</b><small>candidate gaps</small></span>
          <span className="pf-stat"><b>{p?.stats.stars ?? "—"}</b><small>starred</small></span>
          <span className="pf-stat">
            <b>{p ? fmtTok(p.stats.tokens_spent) : "—"}</b>
            <small>{cap ? `of ${fmtTok(cap)} tok cap` : "tok · no cap"}</small>
          </span>
        </div>
      </div>

      {/* finished runs leave a one-line digest behind (H4) — the full card
          lives on the run's Overview tab in the explorer drill-in */}
      {mode === "done" && p && (() => {
        const d = normalizeDigest(p.digest);
        if (!d) return null;
        return (
          <div className="pf-digest-strip">
            <span className="pf-digest-label">
              Run digest
              {d.degraded && (
                <span
                  className="pf-digest-degraded"
                  title="The model was unavailable when this run ended — this digest was computed deterministically, not written by the model."
                >
                  {" "}· deterministic fallback
                </span>
              )}
            </span>
            <span className="pf-digest-text">
              {d.top_spaces.length > 0 && (
                <>Top: {d.top_spaces.map((s) => s.title).join(" · ")}. </>
              )}
              {d.kill_pattern && <>Kill pattern: {d.kill_pattern}</>}
            </span>
            <button className="btn btn-sm" onClick={() => onOpenProject(pid)}>
              Full digest ▸
            </button>
          </div>
        );
      })()}

      <div className="pf-explore-cols">
        <div>
          <div className="pf-col-head">
            <span className="pf-col-title">Candidate gaps</span>
            <button className="btn btn-sm" onClick={() => onOpenProject(pid)}>
              Open full tree ▸
            </button>
          </div>
          {gaps.length > 0 ? (
            <div className="pf-gap-stack">
              {gaps.map((g) => (
                <button key={g.id} className="pf-gap-card" onClick={() => onOpenNode(pid, g.id)}>
                  <div className="pf-gap-chips">
                    <ViabChip value={g.viability} trust={nodeTrust(g)} star={g.star} />
                    <FitChip value={g.fit} labeled />
                    <span className="pf-gap-status">{gapStatus(g)}</span>
                  </div>
                  <div className="pf-gap-title">{g.gap?.title ?? g.title}</div>
                </button>
              ))}
            </div>
          ) : (
            <div className="pf-empty">
              No candidate gaps yet — the engine is still mapping the space.
            </div>
          )}
        </div>

        <div>
          <div className="pf-col-head">
            <span className="pf-col-title">Live activity</span>
            {running && (
              <span className="pf-streaming">
                <span className="pf-dot pulse" />streaming
              </span>
            )}
          </div>
          {logs.length > 0 ? (
            <div className="pf-feed">
              {logs.slice(0, 8).map((l, i) => (
                <div className="pf-feed-row" key={`${l.at}-${i}`}>
                  <span className="src-chip">{feedSrc(l.message)}</span>
                  <span className="pf-feed-text">{l.message}</span>
                  <span className="pf-feed-time">{ago(l.at)}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="pf-empty">
              {running
                ? "Waiting for the next event…"
                : pausedFamily
                  ? "The run is paused — no events streaming."
                  : "The run has finished — no events streaming."}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
