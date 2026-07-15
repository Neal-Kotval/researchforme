import { useMemo, useState } from "react";
import { scout, createProject, ApiError } from "../../autonomous/api";
import { nodeTrust, type Project, type ScoutCandidate } from "../../autonomous/types";
import { ACTIVE_STATUSES } from "../../hooks/useProjects";
import FitChip from "../autonomous/FitChip";
import ViabChip from "../autonomous/ViabChip";
import { usePressureTestedIdeas } from "./usePressureTestedIdeas";
import PreferenceDistillCard from "./PreferenceDistillCard";
import RecentSignals from "./RecentSignals";

interface Props {
  projects: Project[];
  onOpenProject: (pid: string) => void;
  onOpenNode: (pid: string, nodeId: string) => void;
  onNewExploration: () => void;
  onExploreCandidate: (c: ScoutCandidate) => void;
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

/**
 * Home (design handoff §1): the machine's findings first (Worth your
 * attention), then what it is doing (Active exploration), then where to look
 * next (Suggested spaces). Composed from platform primitives — no legacy
 * dashboard embedding.
 */
export default function HomeView({
  projects,
  onOpenProject,
  onOpenNode,
  onNewExploration,
  onExploreCandidate,
}: Props) {
  // One-line quick explore: type a market, press Enter, it's running. The fast
  // path to "more ideas" — sensible defaults, no dialog. Steering lives in the
  // full drawer for when you want it.
  const [quickDomain, setQuickDomain] = useState("");
  const [quickBusy, setQuickBusy] = useState(false);
  const [quickErr, setQuickErr] = useState<string | null>(null);
  const quickExplore = async () => {
    const domain = quickDomain.trim();
    if (domain.length < 2 || quickBusy) return;
    setQuickBusy(true);
    setQuickErr(null);
    try {
      const p = await createProject({
        domain, autostart: true, budget: { max_nodes: 70, pace: "balanced" },
      });
      setQuickDomain("");
      onOpenProject(p.id);
    } catch (e) {
      setQuickErr(e instanceof ApiError ? e.message : "Could not start that exploration.");
    } finally {
      setQuickBusy(false);
    }
  };

  const { ideas, loading } = usePressureTestedIdeas();
  const survivors = useMemo(
    () =>
      ideas
        .filter((i) => (i.node.pressure_test?.survived ?? 0) > 0 && (i.node.viability ?? 0) > 0)
        .slice(0, 3),
    [ideas]
  );

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

  /* ----------------------------------------------------------------- scout -- */
  const [scoutBrief, setScoutBrief] = useState("");
  const [scoutState, setScoutState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [scoutErr, setScoutErr] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<ScoutCandidate[]>([]);
  const runScout = async () => {
    setScoutState("loading");
    setScoutErr(null);
    try {
      const res = await scout(scoutBrief.trim());
      setCandidates(res.candidates);
      setScoutState("ready");
    } catch (e) {
      setScoutErr(e instanceof ApiError ? e.message : "Scout failed — the backend did not answer.");
      setScoutState("error");
    }
  };

  return (
    <div className="pf-view">
      {/* One-line quick explore — the fast path to more ideas. */}
      <div className="quick-explore">
        <span className="qe-icon" aria-hidden>▸</span>
        <input
          className="qe-input"
          placeholder="Explore a market… (e.g. tooling for solo accountants) — Enter to launch"
          value={quickDomain}
          onChange={(e) => setQuickDomain(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void quickExplore(); }}
          disabled={quickBusy}
        />
        <button
          className="btn btn-primary qe-go"
          onClick={() => void quickExplore()}
          disabled={quickBusy || quickDomain.trim().length < 2}
        >
          {quickBusy ? "Launching…" : "Explore ▸"}
        </button>
        <button className="qe-steer" onClick={onNewExploration}
                title="Open the full drawer to add steering, intake, and budget">
          steer…
        </button>
      </div>
      {quickErr && <div className="qe-err">{quickErr}</div>}

      {/* 1 — Worth your attention */}
      <section className="pfm">
        <div className="pfm-head">
          <div className="pfm-title">Worth your attention</div>
          <div className="pfm-sub">Ideas that survived the red team, ranked by fit × viability.</div>
        </div>
        {survivors.length > 0 ? (
          <div className="pf-grid3">
            {survivors.map((i) => {
              const n = i.node;
              const pt = n.pressure_test!;
              const trust = nodeTrust(n);
              return (
                <button key={n.id} className="pf-idea-card" onClick={() => onOpenNode(i.pid, n.id)}>
                  <div className="pf-idea-chips">
                    <ViabChip value={n.viability} trust={trust} star={n.star} />
                    {trust === "unverified" && <span className="pf-idea-unv">unverified</span>}
                    <FitChip value={n.fit} labeled />
                  </div>
                  <div className="pf-idea-title">{n.gap?.title ?? n.title}</div>
                  {n.gap?.why_now && <div className="pf-idea-why">{n.gap.why_now}</div>}
                  <div className="pf-idea-meta">
                    {i.domain}
                    {n.confidence ? ` · ${n.confidence} confidence` : ""}
                    {` · red team ${pt.survived}/${pt.lenses.length}`}
                  </div>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="pf-empty">
            {loading
              ? "Checking the latest runs…"
              : "No idea has survived a red team yet. They land here once a run scores and pressure-tests its candidates."}
          </div>
        )}
      </section>

      {/* 2 — Active exploration */}
      <section className="pfm">
        <div className="pfm-head">
          <div className="pfm-title">Active exploration</div>
          <div className="pfm-sub">
            What the engine is doing right now. Pause any run — it resumes exactly where it stopped.
          </div>
        </div>
        {active.length > 0 ? (
          <div className="pf-run-stack">
            {active.map((p) => {
              const mode = runMode(p);
              const cap = p.budget.max_tokens;
              const pctW = cap ? Math.min(100, (p.stats.tokens_spent / cap) * 100) : 100;
              return (
                <button key={p.id} className="pf-run-card" onClick={() => onOpenProject(p.id)}>
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
                      <span
                        className={cap ? "" : "nocap"}
                        style={{ width: `${pctW}%` }}
                      />
                    </div>
                    <span className="pf-run-cap">
                      {fmtTok(p.stats.tokens_spent)}
                      {cap ? ` of ${fmtTok(cap)} tok cap` : " tok · no cap"}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="pf-empty">
            Nothing is running.
            <br />
            <button className="btn btn-primary" onClick={onNewExploration}>＋ New exploration</button>
          </div>
        )}
      </section>

      {/* 3 — Suggested spaces */}
      <section className="pfm">
        <div className="pfm-head">
          <div className="pfm-title">Suggested spaces</div>
          <div className="pfm-sub">
            Domains the engine proposes from signal convergence — each shows what triggered it.
          </div>
        </div>

        <div className="pf-scout-bar">
          <input
            className="pf-scout-brief"
            placeholder="Optional: who you are / what you're good at — sharpens the suggestions"
            value={scoutBrief}
            onChange={(e) => setScoutBrief(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && scoutState !== "loading" && runScout()}
          />
          <button className="btn btn-primary" onClick={runScout} disabled={scoutState === "loading"}>
            {scoutState === "loading" ? "Scouting…" : "Scout for spaces"}
          </button>
        </div>

        {scoutState === "error" && (
          <div className="pf-scout-note" role="alert">
            ⚠︎ {scoutErr}{" "}
            <button className="btn btn-sm" onClick={runScout} style={{ marginLeft: 8 }}>Retry</button>
          </div>
        )}
        {scoutState === "idle" && (
          <div className="pf-scout-note">
            Not sure where to point the explorer? Scout reads live signals (Reddit, HN, GitHub,
            newsletters) and proposes spaces shaped like ownable companies.
          </div>
        )}
        {scoutState === "ready" && candidates.length === 0 && (
          <div className="pf-scout-note">
            No ownable spaces surfaced from the current signals. Add a line about yourself above
            and scout again — steering narrows the hunt.
          </div>
        )}
        {scoutState === "ready" && candidates.length > 0 && (
          <div className="pf-grid3">
            {candidates.map((c, i) => (
              <div className="pf-scout-card" key={c.domain + i}>
                <div className="pf-scout-title">{c.domain}</div>
                <div className="pf-scout-sigs">
                  {c.signals.slice(0, 2).map((s, j) => (
                    <a
                      key={j}
                      className="pf-scout-sig"
                      href={s.url || undefined}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <span className="src-chip">{s.source}</span>
                      <span className="pf-sig-text">{s.title}</span>
                    </a>
                  ))}
                </div>
                <button className="btn" onClick={() => onExploreCandidate(c)}>
                  Explore this space
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 4 — distill the triage record into steering (H3, review-before-apply) */}
      <PreferenceDistillCard />

      {/* 5 — Recent signals: what moved on the watched spaces (C2) */}
      <RecentSignals onOpenNode={onOpenNode} />
    </div>
  );
}
