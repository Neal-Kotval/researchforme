import { useMemo, useState } from "react";
import type { Project } from "../../autonomous/types";
import { statusMeta } from "./ProjectTabs";

interface Props {
  projects: Project[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onHome: () => void;
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
  return `${n}`;
}

// Rank: live runs first, then most recently updated — so the active work floats up.
function rank(p: Project): number {
  const live = p.status === "running" ? 2 : p.status === "paused" ? 1 : 0;
  return live;
}

/**
 * The explorations sidebar (replaces the cramped horizontal tab bar): a search
 * box + a scrollable, ranked list of every exploration with its live status,
 * gap/⭐ counts and spend, plus a reliable inline-confirm delete on each row
 * (no blocking browser dialog). Scales past a handful of projects — the whole
 * point of "come back to many explorations".
 */
export default function ExplorationSidebar({
  projects, activeId, onSelect, onNew, onDelete, onHome,
}: Props) {
  const [q, setQ] = useState("");
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = needle
      ? projects.filter(
          (p) =>
            p.domain.toLowerCase().includes(needle) ||
            p.sub_segments.some((s) => s.toLowerCase().includes(needle))
        )
      : projects.slice();
    return list.sort((a, b) => {
      const r = rank(b) - rank(a);
      if (r !== 0) return r;
      return (b.updated_at || "").localeCompare(a.updated_at || "");
    });
  }, [projects, q]);

  return (
    <aside className="exp-sidebar" aria-label="Explorations">
      <div className="es-sec-label">Explorations</div>
      <div className="es-head">
        <button
          className={`es-home${activeId === null ? " active" : ""}`}
          onClick={onHome}
        >
          <span className="es-home-ico" aria-hidden>⌂</span> Home
        </button>
        <button className="es-new" onClick={onNew}>
          <span className="es-new-plus">＋</span> New exploration
        </button>
      </div>

      <div className="es-search">
        <span className="es-search-ico" aria-hidden>⌕</span>
        <input
          type="search"
          placeholder="Search explorations…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search explorations"
        />
        {q && (
          <button className="es-search-x" onClick={() => setQ("")} aria-label="Clear search">×</button>
        )}
      </div>

      <div className="es-list" role="tablist" aria-label="Explorations">
        {filtered.length === 0 ? (
          <div className="es-empty">{q ? "No explorations match." : "No explorations yet."}</div>
        ) : (
          filtered.map((p) => {
            const active = p.id === activeId;
            const meta = statusMeta(p);
            const confirming = confirmId === p.id;
            return (
              <div
                key={p.id}
                role="tab"
                aria-selected={active}
                tabIndex={0}
                className={`es-row${active ? " active" : ""}`}
                onClick={() => onSelect(p.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(p.id); }
                }}
              >
                <span className={`es-dot ${meta.dot}${meta.live ? " live" : ""}`} aria-hidden />
                <span className="es-body">
                  <span className="es-domain">{p.domain}</span>
                  <span className="es-meta">
                    <span className={`es-word ${meta.dot}`}>{meta.word}</span>
                    {p.stats.gaps > 0 && <span className="es-count">{p.stats.gaps} gaps</span>}
                    {p.stats.stars > 0 && <span className="es-count star">★{p.stats.stars}</span>}
                    <span className="es-count dim">{fmtTokens(p.stats.tokens_spent)} tok</span>
                  </span>
                </span>

                {confirming ? (
                  <span className="es-confirm" onClick={(e) => e.stopPropagation()}>
                    <button
                      className="es-confirm-yes"
                      title="Confirm delete"
                      onClick={() => { onDelete(p.id); setConfirmId(null); }}
                    >
                      Delete
                    </button>
                    <button
                      className="es-confirm-no"
                      title="Cancel"
                      onClick={() => setConfirmId(null)}
                    >
                      ×
                    </button>
                  </span>
                ) : (
                  <button
                    className="es-del"
                    aria-label={`Delete ${p.domain}`}
                    title="Delete exploration"
                    onClick={(e) => { e.stopPropagation(); setConfirmId(p.id); }}
                  >
                    🗑
                  </button>
                )}
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
