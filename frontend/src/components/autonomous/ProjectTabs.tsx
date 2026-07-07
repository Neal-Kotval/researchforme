import type { Project } from "../../autonomous/types";

interface Props {
  projects: Project[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}

/** A short spend label: "12.4k" tokens, or "0" when idle. */
function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
  return `${n}`;
}

type DotKind = "sprinting" | "curbing" | "paused" | "done" | "error";

/** Map a project's status + governor mode to a status-dot kind + word. */
export function statusMeta(p: Project): { dot: DotKind; word: string; live: boolean } {
  switch (p.status) {
    case "running":
      if (p.stats.mode === "sprinting") return { dot: "sprinting", word: "sprinting", live: true };
      if (p.stats.mode === "curbing") return { dot: "curbing", word: "curbing", live: true };
      return { dot: "sprinting", word: "running", live: true };
    case "paused":
      return { dot: "paused", word: "paused", live: false };
    case "usage_paused":
      return { dot: "curbing", word: "usage-paused", live: false };
    case "milestone_paused":
      return { dot: "curbing", word: "check-in", live: false };
    case "exhausted":
      return { dot: "done", word: "exhausted", live: false };
    case "budget_spent":
      return { dot: "done", word: "budget spent", live: false };
    case "time_limit":
      return { dot: "done", word: "time limit", live: false };
    case "errored":
      return { dot: "error", word: "errored", live: false };
    default:
      return { dot: "paused", word: p.status, live: false };
  }
}

/**
 * Horizontal tab bar: one tab per autonomous project plus a "New exploration"
 * button. Each tab carries the domain, a live status dot, gap/⭐ counts, and a
 * compact spend read-out — the "come back and glance" surface (SPEC §10.1).
 */
export default function ProjectTabs({ projects, activeId, onSelect, onNew }: Props) {
  return (
    <div className="proj-tabs" role="tablist" aria-label="Explorations">
      <div className="proj-tabs-scroll">
        {projects.map((p) => {
          const active = p.id === activeId;
          const meta = statusMeta(p);
          const cap = p.budget.max_tokens;
          const pct = cap ? Math.min(100, (p.stats.tokens_spent / cap) * 100) : 0;
          return (
            <button
              key={p.id}
              role="tab"
              aria-selected={active}
              className={`proj-tab${active ? " active" : ""}`}
              onClick={() => onSelect(p.id)}
              title={`${p.domain} · ${meta.word}`}
            >
              <span className={`pt-dot ${meta.dot}${meta.live ? " live" : ""}`} aria-hidden />
              <span className="pt-body">
                <span className="pt-domain">{p.domain}</span>
                <span className="pt-meta">
                  <span className="pt-word">{meta.word}</span>
                  {p.stats.gaps > 0 && <span className="pt-count">{p.stats.gaps} gaps</span>}
                  {p.stats.stars > 0 && (
                    <span className="pt-count star">★ {p.stats.stars}</span>
                  )}
                  <span className="pt-count dim">{fmtTokens(p.stats.tokens_spent)} tok</span>
                </span>
                {cap ? (
                  <span className="pt-spend" aria-hidden>
                    <span className="pt-spend-fill" style={{ width: `${pct}%` }} />
                  </span>
                ) : null}
              </span>
            </button>
          );
        })}
      </div>
      <button className="proj-tab-new" onClick={onNew} title="Start a new exploration">
        <span className="ptn-plus">＋</span> New exploration
      </button>
    </div>
  );
}
