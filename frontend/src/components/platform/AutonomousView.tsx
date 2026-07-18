import { useMemo, useState } from "react";
import { createProject, control, ApiError } from "../../autonomous/api";
import type { Project } from "../../autonomous/types";
import { ACTIVE_STATUSES } from "../../hooks/useProjects";
import { Card, Composer, EmptyState, SectionHeader } from "../ui";
import { RunCard, type RunControlReq } from "../ui/RunCard";

interface Props {
  projects: Project[];
  onOpenProject: (pid: string) => void;
  onNewExploration: () => void;
}

const STEPS = [
  { t: "Map the space", d: "Decomposes your market into sub-segments and open branches, then fans out across live signal." },
  { t: "Synthesize gaps", d: "Cross-references demand, capability, and supply to name the whitespace worth building on." },
  { t: "Red-team", d: "Six adversarial lenses attack every candidate before it ever reaches your attention." },
  { t: "Rank & pause", d: "Scores survivors by fit × viability; the governor curbs spend and resumes on its own." },
];

/**
 * Autonomous research (v3 §Autonomous): a hero launch line, the live runs, and
 * the loop explainer that folds away once you have history. Point the engine at
 * a market, hand it a budget — it maps → synthesizes → red-teams → ranks on its
 * own. Built from the shared RunCard so it reads identically to Home/Explore.
 */
export default function AutonomousView({ projects, onOpenProject, onNewExploration }: Props) {
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const launch = async () => {
    const d = domain.trim();
    if (d.length < 2 || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const p = await createProject({ domain: d, autostart: true, budget: { max_nodes: 70, pace: "balanced" } });
      setDomain("");
      onOpenProject(p.id);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not launch that run.");
    } finally {
      setBusy(false);
    }
  };

  const active = useMemo(
    () => projects.filter((p) => ACTIVE_STATUSES.has(p.status)).sort((a, b) => {
      const r = (b.status === "running" ? 1 : 0) - (a.status === "running" ? 1 : 0);
      return r !== 0 ? r : (b.updated_at || "").localeCompare(a.updated_at || "");
    }),
    [projects]
  );
  const recent = useMemo(
    () => projects.filter((p) => !ACTIVE_STATUSES.has(p.status))
      .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || "")).slice(0, 4),
    [projects]
  );
  const hasRuns = active.length + recent.length > 0;

  const [busyPid, setBusyPid] = useState<string | null>(null);
  const runControl = (pid: string, req: RunControlReq) => {
    setBusyPid(pid);
    control(pid, req).catch(() => {}).finally(() => setBusyPid(null));
  };

  // Once there's history the explainer collapses behind a toggle; first-run sees it inline.
  const [howOpen, setHowOpen] = useState(false);
  const showSteps = !hasRuns || howOpen;

  return (
    <div className="pf-view ui-page">
      {/* Hero launch — one line to a running autonomous agent. */}
      <section>
        <Composer
          size="hero"
          value={domain}
          onChange={setDomain}
          onSubmit={() => void launch()}
          placeholder="Point the engine at a market… (e.g. onboarding tooling for solo law firms)"
          disabled={busy}
          ariaLabel="Point the engine at a market"
          onSliders={onNewExploration}
          slidersTitle="Budget & steering — open the full drawer"
          submit={{ disabled: busy || domain.trim().length < 2, busy, title: "Launch run" }}
        />
        {err && <div className="ui-inline-err">{err}</div>}
      </section>

      {/* Live autonomous runs */}
      <section>
        <SectionHeader title="Live autonomous runs"
          sub="What the engine is working on unattended. Open any run to steer, pause, or drill in — it resumes exactly where it stopped." />
        {active.length > 0 ? (
          <div className="ui-run-stack">
            {active.map((p) => (
              <RunCard key={p.id} p={p} onOpen={() => onOpenProject(p.id)}
                onControl={(req) => runControl(p.id, req)} busy={busyPid === p.id} />
            ))}
          </div>
        ) : (
          <Card pad>
            <EmptyState title="No autonomous run is going right now"
              body="Point the engine at a market above and it maps, synthesizes, and red-teams on its own — pausing under the governor."
              action={{ label: "Launch a run", onClick: onNewExploration, iconLeft: "＋" }} />
          </Card>
        )}
      </section>

      {/* How the loop works — inline on first run, collapsible once there's history */}
      <section>
        <SectionHeader title="How autonomous research works"
          sub="You give it a market and a budget. It runs the loop on its own until the budget is spent or you pause it."
          howItWorks={hasRuns ? { open: howOpen, onToggle: () => setHowOpen((o) => !o), label: showSteps ? "Hide" : "Show steps" } : undefined} />
        {showSteps && (
          <ol className="ui-steps" style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {STEPS.map((s, i) => (
              <li className="ui-step" key={s.t}>
                <span className="ui-step-n">{i + 1}</span>
                <div>
                  <div className="ui-step-t">{s.t}</div>
                  <div className="ui-step-d">{s.d}</div>
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>

      {/* Recent runs */}
      {recent.length > 0 && (
        <section>
          <SectionHeader title="Recent runs"
            sub="Finished or stopped explorations — reopen one to resume or review." />
          <div className="ui-run-stack">
            {recent.map((p) => (
              <RunCard key={p.id} p={p} onOpen={() => onOpenProject(p.id)}
                onControl={(req) => runControl(p.id, req)} busy={busyPid === p.id} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
