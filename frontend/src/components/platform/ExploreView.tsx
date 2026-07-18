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
import ViabChip from "../autonomous/ViabChip";
import { Button, Card, Chip, EmptyState, Segmented } from "../ui";
import { RunCard, type RunControlReq } from "../ui/RunCard";

interface Props {
  projects: Project[];
  onOpenProject: (pid: string) => void;
  onOpenNode: (pid: string, nodeId: string) => void;
  onNewExploration: () => void;
}

interface LogRow { at: string; message: string; }

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

/** Right-column status caption for a candidate row (mirrors the run states). */
function gapStatus(n: TreeNode): string {
  if (n.state === "pressure_testing") return "in red team";
  if (n.pressure_test && n.pressure_test.lenses.length > 0) {
    return `red team ${n.pressure_test.survived}/${n.pressure_test.lenses.length} lenses`;
  }
  if (n.state === "scored") return "queued for red team";
  if (n.state === "synthesizing") return "synthesizing";
  if (n.state === "expanding" || n.state === "queued") return "mapping";
  return n.state.replace(/_/g, " ");
}

const Chevron = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="m9 6 6 6-6 6" /></svg>
);

/**
 * Explore (v3 §Explore): watch the agent hunt live. The shared RunCard header
 * with Resume/Curb, candidate-gap rows on the left, the live event feed on the
 * right — all fed by the run's SSE stream. The full tree stays a click away.
 */
export default function ExploreView({ projects, onOpenProject, onOpenNode, onNewExploration }: Props) {
  const activeRuns = useMemo(() => projects.filter((p) => ACTIVE_STATUSES.has(p.status)), [projects]);
  const [selectedPid, setSelectedPid] = useState<string | null>(null);
  const newestAny = useMemo(
    () => [...projects].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))[0] ?? null,
    [projects]
  );
  const pid = selectedPid ?? latestActive(projects)?.id ?? newestAny?.id ?? null;

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
  const act = async (req: RunControlReq) => {
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
      <div className="pf-view ui-page">
        <Card pad>
          <EmptyState title="No active run to watch"
            body="Point the engine at a domain and this screen becomes its live feed — the gaps it finds and the signals it reads, in real time."
            action={{ label: "New exploration", onClick: onNewExploration, iconLeft: "＋" }} />
        </Card>
      </div>
    );
  }

  const p = project ?? projects.find((x) => x.id === pid) ?? null;
  const running = p?.status === "running";
  const pausedFamily = p != null && ACTIVE_STATUSES.has(p.status) && !running;

  const gaps = Object.values(nodes)
    .filter((n) => n.kind === "gap" || n.kind === "gap_candidate")
    .sort((a, b) => (b.viability ?? -1) - (a.viability ?? -1))
    .slice(0, 6);

  return (
    <div className="pf-view ui-page">
      {activeRuns.length > 1 && (
        <Segmented items={activeRuns.map((r) => ({ id: r.id, label: r.domain }))}
          value={pid} onChange={setSelectedPid} ariaLabel="Active runs" />
      )}

      {p ? (
        <RunCard p={p} onOpen={() => onOpenProject(pid)} onControl={act} busy={busy} />
      ) : (
        <Card pad><div className="ui-empty-body" style={{ textAlign: "center" }}>Connecting to the run…</div></Card>
      )}

      {/* finished runs leave a one-line digest behind */}
      {!running && !pausedFamily && p && (() => {
        const d = normalizeDigest(p.digest);
        if (!d) return null;
        return (
          <Card pad className="ui-chiprow" style={{ justifyContent: "space-between", gap: 14 }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="ui-stat-label" style={{ marginBottom: 4 }}>
                Run digest
                {d.degraded && (
                  <span title="The model was unavailable when this run ended — this digest was computed deterministically, not written by the model.">
                    {" · "}deterministic fallback
                  </span>
                )}
              </div>
              <div className="ui-row-cap">
                {d.top_spaces.length > 0 && <>Top: {d.top_spaces.map((s) => s.title).join(" · ")}. </>}
                {d.kill_pattern && <>Kill pattern: {d.kill_pattern}</>}
              </div>
            </div>
            <Button variant="secondary" size="sm" onClick={() => onOpenProject(pid)}>Full digest</Button>
          </Card>
        );
      })()}

      <div className="ui-split">
        <section>
          <div className="ui-colhead">
            <span className="ui-colhead-title">Candidate gaps</span>
            <Button variant="secondary" size="sm" onClick={() => onOpenProject(pid)}>Open full tree</Button>
          </div>
          {gaps.length > 0 ? (
            <div className="ui-rowlist">
              {gaps.map((g) => (
                <button key={g.id} className="ui-row" onClick={() => onOpenNode(pid, g.id)}>
                  <ViabChip value={g.viability} trust={nodeTrust(g)} star={g.star} />
                  <div className="ui-row-main">
                    <div className="ui-row-title">{g.gap?.title ?? g.title}</div>
                    <div className="ui-row-cap">{gapStatus(g)}</div>
                  </div>
                  <span className="ui-row-chev"><Chevron /></span>
                </button>
              ))}
            </div>
          ) : (
            <Card pad>
              <EmptyState title="No candidate gaps yet" body="The engine is still mapping the space — gaps appear here as it synthesizes them." />
            </Card>
          )}
        </section>

        <section>
          <div className="ui-colhead">
            <span className="ui-colhead-title">Live activity</span>
            {running && <Chip tone="tint" dot="accent" pulse>streaming</Chip>}
          </div>
          {logs.length > 0 ? (
            <div className="ui-feed">
              {logs.slice(0, 8).map((l, i) => (
                <div className="ui-feed-row" key={`${l.at}-${i}`}>
                  <span className="ui-chip ui-chip--slate ui-chip--sm">{feedSrc(l.message)}</span>
                  <span className="ui-feed-text">{l.message}</span>
                  <span className="ui-feed-time">{ago(l.at)}</span>
                </div>
              ))}
            </div>
          ) : (
            <Card pad>
              <EmptyState
                title={running ? "Waiting for the next event…" : pausedFamily ? "The run is paused" : "The run has finished"}
                body={running ? undefined : pausedFamily ? "No events streaming while paused — resume to watch it hunt again." : "No events streaming — reopen the full tree to review what it found."} />
            </Card>
          )}
        </section>
      </div>
    </div>
  );
}
