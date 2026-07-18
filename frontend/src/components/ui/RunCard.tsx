/* ============================================================================
   RunCard — the ONE run card, shared by Home · Autonomous · Explore.
   name + status chip · nodes/gaps/starred/ran tabular stats · spend meter ·
   inline transport ("Resume run"/"Pause"/"Keep going") + "Curb spend".
   Clicking the body opens the run; the action buttons are legal siblings of
   the open-button (never nested), so a11y stays clean.
   ========================================================================== */
import type { Project } from "../../autonomous/types";
import { ACTIVE_STATUSES } from "../../hooks/useProjects";
import { Chip, StatRow, Meter } from "./index";

/** Loose shape for the control dispatch RunCard needs (a subset of ControlRequest). */
export type RunControlReq =
  | { action: "resume" }
  | { action: "pause" }
  | { action: "continue_milestone" }
  | { action: "set_pace"; pace: "eco" | "balanced" | "sprint" };

export function fmtTok(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

/** Wall-clock runtime: to now while running, frozen at last activity otherwise. */
export function elapsed(p: Project): string {
  const end = p.status === "running" ? Date.now() : Date.parse(p.updated_at);
  const ms = end - Date.parse(p.created_at);
  if (Number.isNaN(ms) || ms < 0) return "—";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 100) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.round(h / 24)}d`;
}

type StatusDot = "accent" | "slate" | "ink";
function runMode(p: Project): { word: string; dot: StatusDot; pulse: boolean; tone: "tint" | "slate" } {
  if (p.status === "running") {
    if (p.stats.mode === "curbing") return { word: "curbing", dot: "slate", pulse: false, tone: "slate" };
    return { word: "sprinting", dot: "accent", pulse: true, tone: "tint" };
  }
  if (ACTIVE_STATUSES.has(p.status)) return { word: "paused", dot: "slate", pulse: false, tone: "slate" };
  return { word: p.status.replace(/_/g, " "), dot: "ink", pulse: false, tone: "slate" };
}

/** The "what is it doing right now" line under a run's name. */
export function nowLine(p: Project): string {
  if (p.status === "running") {
    if (p.stats.mode === "curbing") return "Governor slowing spend — deep-rigor passes deferred to the next window";
    return `Hunting across ${p.stats.frontier_size} open branch${p.stats.frontier_size === 1 ? "" : "es"} — ${p.stats.candidates} candidates so far`;
  }
  if (p.status === "paused") return "Paused — resumes exactly where it stopped";
  if (p.status === "usage_paused") return "Waiting on the usage governor — resumes automatically";
  if (p.status === "milestone_paused") return "Milestone reached — waiting for your go-ahead";
  return p.stats.stop_reason ?? "Stopped";
}

interface RunCardProps {
  p: Project;
  onOpen: () => void;
  /** When provided, inline transport + curb actions render. */
  onControl?: (req: RunControlReq) => void;
  busy?: boolean;
}

export function RunCard({ p, onOpen, onControl, busy }: RunCardProps) {
  const mode = runMode(p);
  const cap = p.budget.max_tokens;
  const pct = cap ? Math.min(100, (p.stats.tokens_spent / cap) * 100) : 100;
  const running = p.status === "running";
  const active = ACTIVE_STATUSES.has(p.status);

  // Contextual transport button (mirrors RunControls).
  const transport: { label: string; req: RunControlReq } | null =
    p.status === "milestone_paused" ? { label: "Keep going", req: { action: "continue_milestone" } }
    : running ? { label: "Pause", req: { action: "pause" } }
    : { label: "Resume run", req: { action: "resume" } };
  const canCurb = active && p.budget.pace !== "eco";

  return (
    <div className="ui-card rc">
      <button className="rc-open" onClick={onOpen} aria-label={`Open ${p.domain}`}>
        <div className="rc-head">
          <span className="rc-name">{p.domain}</span>
          <Chip tone={mode.tone} dot={mode.dot} pulse={mode.pulse}>{mode.word}</Chip>
        </div>
        <div className="rc-now">{nowLine(p)}</div>
        <StatRow stats={[
          { label: "nodes", value: p.stats.nodes },
          { label: "gaps", value: p.stats.gaps },
          { label: "starred", value: p.stats.stars },
          { label: running ? "running" : "ran", value: elapsed(p) },
        ]} />
      </button>
      <div className="rc-side">
        <Meter pct={pct} caption={
          <span className="rc-cap">
            <span className="mono">{fmtTok(p.stats.tokens_spent)} tok</span>
            {cap ? <> · <span className="mono">{fmtTok(cap)}</span> cap</> : " · no cap"}
          </span>
        } />
        {onControl && (
          <div className="rc-actions">
            <button className="btn btn-sm btn-quiet" disabled={busy}
              onClick={() => onControl(transport.req)}>{transport.label}</button>
            {canCurb && (
              <button className="btn btn-sm btn-quiet" disabled={busy}
                title="Drop the pace to eco — the governor spends slower"
                onClick={() => onControl({ action: "set_pace", pace: "eco" })}>Curb spend</button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
