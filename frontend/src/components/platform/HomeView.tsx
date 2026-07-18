import { useMemo, useState } from "react";
import { scout, createProject, control, ApiError } from "../../autonomous/api";
import { nodeTrust, type Project, type ScoutCandidate } from "../../autonomous/types";
import { ACTIVE_STATUSES } from "../../hooks/useProjects";
import FitChip from "../autonomous/FitChip";
import ViabChip from "../autonomous/ViabChip";
import { usePressureTestedIdeas } from "./usePressureTestedIdeas";
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

/** Verdict text clamped to 3 lines with an in-place Read more. */
function Clamp({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 130;
  return (
    <div>
      <div className={open || !long ? "ui-idea-verdict" : "ui-idea-verdict ui-clamp ui-clamp-3"}>{text}</div>
      {long && (
        <button className="ui-readmore" onClick={() => setOpen((o) => !o)}>
          {open ? "Read less" : "Read more"}
        </button>
      )}
    </div>
  );
}

/** One survivor idea — score + title + clamped verdict + meta chips. */
function IdeaCard({ i, onOpen }: { i: ReturnType<typeof usePressureTestedIdeas>["ideas"][number]; onOpen: () => void }) {
  const n = i.node;
  const pt = n.pressure_test!;
  const trust = nodeTrust(n);
  return (
    <Card className="ui-ideacard">
      <div className="ui-idea-top">
        <ViabChip value={n.viability} trust={trust} star={n.star} />
        <FitChip value={n.fit} labeled />
      </div>
      <button className="ui-idea-title ui-idea-titlebtn" onClick={onOpen}>{n.gap?.title ?? n.title}</button>
      {n.gap?.why_now && <Clamp text={n.gap.why_now} />}
      <div className="ui-chiprow">
        <span className="ui-chip ui-chip--slate ui-chip--sm">{i.domain}</span>
        {n.confidence && <span className="ui-chip ui-chip--outline ui-chip--sm">{n.confidence} confidence</span>}
        <span className="ui-chip ui-chip--slate ui-chip--sm">red team {pt.survived}/{pt.lenses.length}</span>
      </div>
    </Card>
  );
}

/**
 * Home (v3 §Home): findings first (Worth your attention) → what's running now
 * (Active exploration, the shared RunCard) → where to look next (Suggested
 * spaces) → what moved (Recent signals). Built from v3 primitives.
 */
export default function HomeView({
  projects, mode, onOpenProject, onOpenNode, onNewExploration, onExploreCandidate, onNav, onActed,
}: Props) {
  // One-line quick explore: type a market, Enter, it's running (sensible
  // defaults, no dialog). Full steering lives in the drawer via "Add steering".
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

  const { ideas, loading } = usePressureTestedIdeas();
  const survivors = useMemo(
    () => ideas.filter((i) => (i.node.pressure_test?.survived ?? 0) > 0 && (i.node.viability ?? 0) > 0).slice(0, 3),
    [ideas]
  );

  const active = useMemo(
    () => projects.filter((p) => ACTIVE_STATUSES.has(p.status)).sort((a, b) => {
      const r = (b.status === "running" ? 1 : 0) - (a.status === "running" ? 1 : 0);
      return r !== 0 ? r : (b.updated_at || "").localeCompare(a.updated_at || "");
    }),
    [projects]
  );

  // Inline run controls on the RunCard — mutate; App's poll re-syncs status.
  const [busyPid, setBusyPid] = useState<string | null>(null);
  const runControl = (pid: string, req: RunControlReq) => {
    setBusyPid(pid);
    control(pid, req).catch(() => {}).finally(() => setBusyPid(null));
  };

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

  // Ask mode folds the Assistant into Home (the #/assistant route). Rendered
  // after all hooks above so the hook order never changes when toggling modes.
  if (mode === "ask") return <AssistantView onNav={onNav} onActed={onActed} />;

  return (
    <div className="pf-view ui-page">
      {/* Launch — the composer hero: one line to a running exploration. */}
      <section>
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
        {quickErr && <div className="ui-inline-err">{quickErr}</div>}
      </section>

      {/* 1 — Worth your attention */}
      <section>
        <SectionHeader title="Worth your attention"
          sub="Ideas that survived the red team, ranked by fit × viability." />
        {survivors.length > 0 ? (
          <div className="ui-cardgrid">
            {survivors.map((i) => <IdeaCard key={i.node.id} i={i} onOpen={() => onOpenNode(i.pid, i.node.id)} />)}
          </div>
        ) : (
          <Card pad>
            <EmptyState
              title={loading ? "Checking the latest runs…" : "Nothing has survived the red team yet"}
              body={loading ? undefined : "Ideas land here once a run scores its candidates and the six adversarial lenses have had their turn."}
            />
          </Card>
        )}
      </section>

      {/* 2 — Active exploration (shared RunCard, with inline Resume / Curb) */}
      <section>
        <SectionHeader title="Active exploration"
          sub="What the engine is doing right now. Pause any run — it resumes exactly where it stopped." />
        {active.length > 0 ? (
          <div className="ui-run-stack">
            {active.map((p) => (
              <RunCard key={p.id} p={p} onOpen={() => onOpenProject(p.id)}
                onControl={(req) => runControl(p.id, req)} busy={busyPid === p.id} />
            ))}
          </div>
        ) : (
          <Card pad>
            <EmptyState title="Nothing is running"
              body="Point the engine at a market and it maps, synthesizes, and red-teams on its own."
              action={{ label: "New exploration", onClick: onNewExploration, iconLeft: "＋" }} />
          </Card>
        )}
      </section>

      {/* 3 — Suggested spaces */}
      <section>
        <SectionHeader title="Suggested spaces"
          sub="Domains the engine proposes from signal convergence — each shows what triggered it." />
        <Composer
          value={scoutBrief}
          onChange={setScoutBrief}
          onSubmit={() => scoutState !== "loading" && runScout()}
          placeholder="Optional: who you are / what you're good at — sharpens the suggestions"
          ariaLabel="Who you are, what you're good at"
          submit={{ busy: scoutState === "loading", title: "Scout for spaces" }}
        />
        {scoutState === "idle" && (
          <div className="ui-field-help">
            Scout reads live signals (Reddit, HN, GitHub, newsletters) and proposes spaces shaped
            like ownable companies. A line about yourself narrows the hunt.
          </div>
        )}
        {scoutState === "error" && (
          <div className="ui-inline-err" role="alert">
            ⚠︎ {scoutErr} <button className="ui-readmore" onClick={runScout} style={{ marginLeft: 6 }}>Retry</button>
          </div>
        )}
        {scoutState === "ready" && candidates.length === 0 && (
          <Card pad style={{ marginTop: 12 }}>
            <EmptyState title="No ownable spaces surfaced"
              body="Nothing shaped like a company came out of the current signals. Add a line about yourself above and scout again — steering narrows the hunt." />
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

      {/* 4 — distilled preferences (self-gates; review-before-apply) */}
      <PreferenceDistillCard />

      {/* 5 — Recent signals: what moved on the watched spaces */}
      <RecentSignals onOpenNode={onOpenNode} />
    </div>
  );
}
