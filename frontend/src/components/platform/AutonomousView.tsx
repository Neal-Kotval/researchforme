import { useMemo, useState } from "react";
import { createProject, ApiError } from "../../autonomous/api";
import type { Project } from "../../autonomous/types";
import { ACTIVE_STATUSES } from "../../hooks/useProjects";

interface Props {
  projects: Project[];
  onOpenProject: (pid: string) => void;
  onNewExploration: () => void;
}

function fmtTok(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

/** Wall-clock runtime: to now while running, frozen at last activity when not. */
function elapsed(p: Project): string {
  const end = p.status === "running" ? Date.now() : Date.parse(p.updated_at);
  const ms = end - Date.parse(p.created_at);
  if (Number.isNaN(ms) || ms < 0) return "—";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 100) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.round(h / 24)}d`;
}

/** Mode pill class + label for a run card. */
function runMode(p: Project): { cls: string; word: string; live: boolean } {
  if (p.status === "running") {
    const m = p.stats.mode;
    if (m === "curbing") return { cls: "curbing", word: "curbing", live: false };
    return { cls: "sprinting", word: "sprinting", live: true };
  }
  if (ACTIVE_STATUSES.has(p.status)) return { cls: "paused", word: "paused", live: false };
  return { cls: "done", word: p.status.replace(/_/g, " "), live: false };
}

/** The "what is it doing right now" line under a run's name. */
function nowLine(p: Project): string {
  if (p.status === "running") {
    if (p.stats.mode === "curbing") {
      return "Governor slowing spend — deep-rigor passes deferred to the next window";
    }
    return `Hunting across ${p.stats.frontier_size} open branch${p.stats.frontier_size === 1 ? "" : "es"} — ${p.stats.candidates} candidates so far`;
  }
  if (p.status === "paused") return "Paused — resumes exactly where it stopped";
  if (p.status === "usage_paused") return "Waiting on the usage governor — resumes automatically";
  if (p.status === "milestone_paused") return "Milestone reached — waiting for your go-ahead";
  return p.stats.stop_reason ?? "Stopped";
}

/** One run row — mirrors Home's active-run card so the two screens read alike. */
function RunCard({ p, onOpen }: { p: Project; onOpen: () => void }) {
  const mode = runMode(p);
  const cap = p.budget.max_tokens;
  const pctW = cap ? Math.min(100, (p.stats.tokens_spent / cap) * 100) : 100;
  return (
    <button className="pf-run-card" onClick={onOpen}>
      <div className="pf-run-main">
        <div className="pf-run-head">
          <span className="pf-run-name">{p.domain}</span>
          <span className={`pf-pill ${mode.cls}`}>
            <span className={`pf-dot${mode.live ? " pulse" : ""}`} />
            {mode.word}
          </span>
        </div>
        <div className="pf-run-now">{nowLine(p)}</div>
        <div className="pf-run-stats">
          <span className="pf-stat"><b>{p.stats.nodes}</b><small>nodes</small></span>
          <span className="pf-stat"><b>{p.stats.gaps}</b><small>gaps</small></span>
          <span className="pf-stat"><b>{p.stats.stars}</b><small>starred</small></span>
          <span className="pf-stat"><b>{elapsed(p)}</b><small>{p.status === "running" ? "running" : "ran"}</small></span>
        </div>
      </div>
      <div className="pf-run-spend">
        <span className="pf-run-spend-label">Spend</span>
        <div className="pf-run-bar">
          <span className={cap ? "" : "nocap"} style={{ width: `${pctW}%` }} />
        </div>
        <span className="pf-run-cap">
          {fmtTok(p.stats.tokens_spent)}
          {cap ? ` of ${fmtTok(cap)} tok cap` : " tok · no cap"}
        </span>
      </div>
    </button>
  );
}

const STEPS = [
  { t: "Map the space", d: "Decomposes your market into sub-segments and open branches, then fans out across live signal." },
  { t: "Synthesize gaps", d: "Cross-references demand, capability, and supply to name the whitespace worth building on." },
  { t: "Red-team", d: "Six adversarial lenses attack every candidate before it ever reaches your attention." },
  { t: "Rank & pause", d: "Scores survivors by fit × viability; the governor curbs spend and resumes on its own." },
];

/**
 * Autonomous research (Flow §2): the fully-autonomous mode. Point the engine at
 * a market, hand it a budget, and it maps → synthesizes → red-teams → ranks on
 * its own, pausing and resuming under the governor. This is the landing page for
 * that mode — a fast launch line, the live runs, and how the loop works. Deep
 * per-run drill-down still lives in Explore / the exploration view.
 */
export default function AutonomousView({ projects, onOpenProject, onNewExploration }: Props) {
  // Fast path: type a market, press Enter, an autonomous run is going. Steering
  // (intake, advantages, budget) lives in the full drawer via "steer…".
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const launch = async () => {
    const d = domain.trim();
    if (d.length < 2 || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const p = await createProject({
        domain: d, autostart: true, budget: { max_nodes: 70, pace: "balanced" },
      });
      setDomain("");
      onOpenProject(p.id);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not launch that run.");
    } finally {
      setBusy(false);
    }
  };

  const active = useMemo(
    () =>
      projects
        .filter((p) => ACTIVE_STATUSES.has(p.status))
        .sort((a, b) => {
          const r = (b.status === "running" ? 1 : 0) - (a.status === "running" ? 1 : 0);
          return r !== 0 ? r : (b.updated_at || "").localeCompare(a.updated_at || "");
        }),
    [projects]
  );

  const recent = useMemo(
    () =>
      projects
        .filter((p) => !ACTIVE_STATUSES.has(p.status))
        .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))
        .slice(0, 4),
    [projects]
  );

  return (
    <div className="pf-view">
      {/* Fast launch — one line to a running autonomous agent. */}
      <div className="quick-explore">
        <span className="qe-icon" aria-hidden>▸</span>
        <input
          className="qe-input"
          placeholder="Point the engine at a market… (e.g. onboarding tooling for solo law firms) — Enter to launch"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void launch(); }}
          disabled={busy}
        />
        <button
          className="btn btn-primary qe-go"
          onClick={() => void launch()}
          disabled={busy || domain.trim().length < 2}
        >
          {busy ? "Launching…" : "Launch ▸"}
        </button>
        <button className="qe-steer" onClick={onNewExploration}
                title="Open the full drawer to add steering, intake, and budget">
          steer…
        </button>
      </div>
      {err && <div className="qe-err">{err}</div>}

      {/* 1 — Live autonomous runs */}
      <section className="pfm">
        <div className="pfm-head">
          <div className="pfm-title">Live autonomous runs</div>
          <div className="pfm-sub">
            What the engine is working on unattended. Open any run to steer, pause, or drill in —
            it resumes exactly where it stopped.
          </div>
        </div>
        {active.length > 0 ? (
          <div className="pf-run-stack">
            {active.map((p) => (
              <RunCard key={p.id} p={p} onOpen={() => onOpenProject(p.id)} />
            ))}
          </div>
        ) : (
          <div className="pf-empty">
            No autonomous run is going right now.
            <br />
            <button className="btn btn-primary" onClick={onNewExploration}>＋ Launch a run</button>
          </div>
        )}
      </section>

      {/* 2 — How the loop works */}
      <section className="pfm">
        <div className="pfm-head">
          <div className="pfm-title">How autonomous research works</div>
          <div className="pfm-sub">
            You give it a market and a budget. It runs the loop on its own until the budget is spent
            or you pause it.
          </div>
        </div>
        <ol className="pf-steps">
          {STEPS.map((s, i) => (
            <li className="pf-step" key={s.t}>
              <span className="pf-step-n">{i + 1}</span>
              <div>
                <div className="pf-step-t">{s.t}</div>
                <div className="pf-step-d">{s.d}</div>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* 3 — Recent runs (only when the engine has history) */}
      {recent.length > 0 && (
        <section className="pfm">
          <div className="pfm-head">
            <div className="pfm-title">Recent runs</div>
            <div className="pfm-sub">Finished or stopped explorations — reopen one to resume or review.</div>
          </div>
          <div className="pf-run-stack">
            {recent.map((p) => (
              <RunCard key={p.id} p={p} onOpen={() => onOpenProject(p.id)} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
