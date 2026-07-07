import type { ExplorerMode, Project } from "../../autonomous/types";

interface Props {
  project: Project;
  allProjects?: Project[];
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${n}`;
}
function fmtTime(iso: string | null): string | null {
  if (!iso) return null;
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return null;
  return t.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
const MODE_WORD: Record<ExplorerMode, string> = {
  sprinting: "Sprinting",
  curbing: "Curbing usage",
  paused: "Idle",
};

/**
 * Live usage meter (SPEC §10.3 / §6): tokens spent against the budget, the
 * governor's current mode, the frontier size, and — when usage-paused — the
 * scheduled resume time. A compact global row sums spend + shows the shared
 * concurrency mode across all open explorations.
 */
export default function UsageMeter({ project, allProjects }: Props) {
  const s = project.stats;
  const cap = project.budget.max_tokens;
  const pct = cap ? Math.min(100, (s.tokens_spent / cap) * 100) : 0;
  const resume = fmtTime(s.next_resume_at);

  const globalSpend = allProjects?.reduce((a, p) => a + p.stats.tokens_spent, 0) ?? s.tokens_spent;
  const anySprint = allProjects?.some((p) => p.stats.mode === "sprinting");
  const anyCurb = allProjects?.some((p) => p.stats.mode === "curbing");
  const globalMode: ExplorerMode = anySprint ? "sprinting" : anyCurb ? "curbing" : "paused";

  return (
    <div className="usage-meter">
      <div className="um-main">
        <div className="um-metric">
          <span className="um-num">{fmtTokens(s.tokens_spent)}</span>
          <span className="um-unit">
            tokens{cap ? ` / ${fmtTokens(cap)}` : ""}
          </span>
        </div>
        <span className={`mode-pill ${s.mode}`}>
          <span className="mp-dot" />
          {MODE_WORD[s.mode]}
        </span>
      </div>

      {cap ? (
        <div className="um-bar">
          <span className="um-bar-fill" style={{ width: `${pct}%` }} />
        </div>
      ) : null}

      <div className="um-stats">
        <span className="um-stat">
          <b>{s.frontier_size}</b> in frontier
        </span>
        <span className="um-stat">
          <b>{s.nodes}</b> nodes
        </span>
        <span className="um-stat">
          <b>{s.max_viability}</b> top viability
        </span>
        {resume && (
          <span className="um-stat resume">resumes ~{resume}</span>
        )}
      </div>

      {allProjects && allProjects.length > 1 && (
        <div className="um-global">
          <span className={`mode-dot ${globalMode}`} />
          <span>
            All explorations · <b>{fmtTokens(globalSpend)}</b> tokens ·{" "}
            {MODE_WORD[globalMode].toLowerCase()}
          </span>
        </div>
      )}
    </div>
  );
}
