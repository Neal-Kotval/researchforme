import { useMemo, useState } from "react";
import { scout, createProject, control, ApiError } from "../../autonomous/api";
import type { Project, ScoutCandidate } from "../../autonomous/types";
import { ACTIVE_STATUSES } from "../../hooks/useProjects";
import PreferenceDistillCard from "./PreferenceDistillCard";
import RecentSignals from "./RecentSignals";
import { Button, Card, Composer, EmptyState, SectionHeader } from "../ui";
import { RunCard, type RunControlReq } from "../ui/RunCard";
import AssistantView from "./AssistantView";

type AssistantNav = "home" | "explore" | "pressure" | "compare" | "assistant";

interface Props {
  projects: Project[];
  /** Composer mode — "ask" folds the Assistant into Home (the #/assistant route). */
  mode: "explore" | "ask";
  onOpenProject: (pid: string) => void;
  onOpenNode: (pid: string, nodeId: string) => void;
  onNewExploration: () => void;
  onExploreCandidate: (c: ScoutCandidate) => void;
  onNav: (view: AssistantNav) => void;
  onActed: () => void;
}

const ArrowMark = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.4"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 17 17 7" /><path d="M8.5 7H17v8.5" /></svg>
);

/**
 * Home (v3 consolidation): the Claude-style hero — a greeting, a big composer
 * with an Explore | Ask toggle, and quick-action chips. The engine's findings
 * live on Library; live runs on Explore. Anything already in flight, plus the
 * self-gating preference + signal cards, sits quietly below the fold so nothing
 * is dropped. "Ask" folds the Assistant in (the #/assistant route).
 */
export default function HomeView({
  projects, mode, onOpenProject, onOpenNode, onNewExploration, onExploreCandidate, onNav, onActed,
}: Props) {
  // One-line quick explore — type a market, launch a run.
  const [quickDomain, setQuickDomain] = useState("");
  const [quickBusy, setQuickBusy] = useState(false);
  const [quickErr, setQuickErr] = useState<string | null>(null);
  const quickExplore = async () => {
    const domain = quickDomain.trim();
    if (domain.length < 2 || quickBusy) return;
    setQuickBusy(true);
    setQuickErr(null);
    try {
      const p = await createProject({ domain, autostart: true, budget: { max_nodes: 70, pace: "balanced" } });
      setQuickDomain("");
      onOpenProject(p.id);
    } catch (e) {
      setQuickErr(e instanceof ApiError ? e.message : "Could not start that exploration.");
    } finally {
      setQuickBusy(false);
    }
  };

  const active = useMemo(
    () => projects.filter((p) => ACTIVE_STATUSES.has(p.status)).sort((a, b) => {
      const r = (b.status === "running" ? 1 : 0) - (a.status === "running" ? 1 : 0);
      return r !== 0 ? r : (b.updated_at || "").localeCompare(a.updated_at || "");
    }),
    [projects]
  );

  const [busyPid, setBusyPid] = useState<string | null>(null);
  const runControl = (pid: string, req: RunControlReq) => {
    setBusyPid(pid);
    control(pid, req).catch(() => {}).finally(() => setBusyPid(null));
  };

  // Scout — folded behind a chip; reveals inline when asked for.
  const [scoutOpen, setScoutOpen] = useState(false);
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

  // Ask mode folds the Assistant into Home. After all hooks so order is stable.
  if (mode === "ask") return <AssistantView onNav={onNav} onActed={onActed} />;

  return (
    <>
      <div className="home-hero">
        <div className="home-greet"><span className="home-greet-mark"><ArrowMark /></span>What are we hunting?</div>

        <div className="home-modes">
          <div className="home-mode-seg" role="tablist" aria-label="Composer mode">
            <button role="tab" aria-selected="true" className="home-mode-btn on">Explore</button>
            <button role="tab" aria-selected="false" className="home-mode-btn" onClick={() => onNav("assistant")}>Ask</button>
          </div>
        </div>

        <Composer
          size="hero"
          value={quickDomain}
          onChange={setQuickDomain}
          onSubmit={() => void quickExplore()}
          placeholder="Explore a market… (e.g. domestic semiconductor packaging)"
          disabled={quickBusy}
          ariaLabel="Explore a market"
          onSliders={onNewExploration}
          slidersTitle="Budget & steering — open the full drawer"
          submit={{ disabled: quickDomain.trim().length < 2, busy: quickBusy, title: "Launch exploration" }}
        />
        {quickErr && <div className="home-err ui-inline-err">{quickErr}</div>}

        <div className="home-chips">
          <button className="home-chip" onClick={() => setScoutOpen((o) => !o)}>Scout spaces</button>
          <button className="home-chip" onClick={() => { window.location.hash = "#/autonomous"; }}>Autonomous</button>
          <button className="home-chip" onClick={() => onNav("compare")}>Compare ideas</button>
          <button className="home-chip" onClick={onNewExploration}>New exploration</button>
        </div>
      </div>

      <div className="home-belowfold">
        {/* Scout — revealed by the chip; proposes ownable spaces from live signal. */}
        {scoutOpen && (
          <section>
            <SectionHeader title="Scout for spaces"
              sub="Scout reads live signals (Reddit, HN, GitHub, newsletters) and proposes spaces shaped like ownable companies. A line about yourself narrows the hunt." />
            <Composer
              value={scoutBrief}
              onChange={setScoutBrief}
              onSubmit={() => scoutState !== "loading" && runScout()}
              placeholder="Optional: who you are / what you're good at — sharpens the suggestions"
              ariaLabel="Who you are, what you're good at"
              submit={{ busy: scoutState === "loading", title: "Scout for spaces" }}
            />
            {scoutState === "error" && (
              <div className="ui-inline-err" role="alert" style={{ marginTop: 10 }}>
                ⚠︎ {scoutErr} <button className="ui-readmore" onClick={runScout} style={{ marginLeft: 6 }}>Retry</button>
              </div>
            )}
            {scoutState === "ready" && candidates.length === 0 && (
              <Card pad style={{ marginTop: 12 }}>
                <EmptyState title="No ownable spaces surfaced"
                  body="Nothing shaped like a company came out of the current signals. Add a line about yourself and scout again." />
              </Card>
            )}
            {scoutState === "ready" && candidates.length > 0 && (
              <div className="ui-cardgrid" style={{ marginTop: 12 }}>
                {candidates.map((c, i) => (
                  <Card key={c.domain + i} className="ui-ideacard">
                    <div className="ui-idea-title">{c.domain}</div>
                    <div className="hv-scout-sigs">
                      {c.signals.slice(0, 2).map((s, j) => (
                        <a key={j} className="hv-scout-sig" href={s.url || undefined} target="_blank" rel="noreferrer">
                          <span className="ui-chip ui-chip--slate ui-chip--sm">{s.source}</span>
                          <span className="hv-scout-sig-txt">{s.title}</span>
                        </a>
                      ))}
                    </div>
                    <div className="ui-chiprow">
                      <Button variant="quiet" size="sm" onClick={() => onExploreCandidate(c)}>Explore this space →</Button>
                    </div>
                  </Card>
                ))}
              </div>
            )}
          </section>
        )}

        {/* Anything already in flight stays visible here. */}
        {active.length > 0 && (
          <section>
            <SectionHeader title="Active exploration"
              sub="What the engine is doing right now. Pause any run — it resumes exactly where it stopped." />
            <div className="ui-run-stack">
              {active.map((p) => (
                <RunCard key={p.id} p={p} onOpen={() => onOpenProject(p.id)}
                  onControl={(req) => runControl(p.id, req)} busy={busyPid === p.id} />
              ))}
            </div>
          </section>
        )}

        {/* Self-gating: distilled preferences + watched-signal movement. */}
        <PreferenceDistillCard />
        <RecentSignals onOpenNode={onOpenNode} />
      </div>
    </>
  );
}
