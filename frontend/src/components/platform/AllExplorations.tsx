import { useMemo, useState } from "react";
import type { Project } from "../../autonomous/types";
import { ACTIVE_STATUSES } from "../../hooks/useProjects";
import { statusMeta } from "../autonomous/statusMeta";
import { SectionHeader } from "../ui";
import "./AllExplorations.css";

interface Props {
  projects: Project[];
  /** The one currently being watched below — gets a subtle "watching" marker. */
  activePid?: string | null;
  /** Open an exploration fullscreen (its whole tree + inspector). */
  onOpen: (pid: string) => void;
}

const INITIAL = 6; // cards shown before the faded "show more".

/** Rank: live runs first, then most-recently-updated. */
function rank(a: Project, b: Project): number {
  const al = ACTIVE_STATUSES.has(a.status) ? 1 : 0;
  const bl = ACTIVE_STATUSES.has(b.status) ? 1 : 0;
  if (al !== bl) return bl - al;
  return (b.updated_at || "").localeCompare(a.updated_at || "");
}

/**
 * The whole exploration folder as a scannable card grid — every run at a glance
 * (domain, live status, what it found), the first few shown and the rest behind
 * a faded "Show more". Click a card to open that exploration fullscreen.
 */
export default function AllExplorations({ projects, activePid, onOpen }: Props) {
  const [expanded, setExpanded] = useState(false);
  const sorted = useMemo(() => [...projects].sort(rank), [projects]);

  if (sorted.length === 0) return null;

  const overflow = sorted.length - INITIAL;
  const collapsed = !expanded && overflow > 0;
  const shown = collapsed ? sorted.slice(0, INITIAL) : sorted;
  const liveCount = sorted.filter((p) => ACTIVE_STATUSES.has(p.status)).length;

  return (
    <section className="allx">
      <SectionHeader
        title="All explorations"
        sub={`${sorted.length} exploration${sorted.length === 1 ? "" : "s"}${
          liveCount ? ` · ${liveCount} live` : ""
        }. Click any one to open it fullscreen.`}
      />

      <div className={`allx-wrap${collapsed ? " collapsed" : ""}`}>
        <div className="allx-grid">
          {shown.map((p) => {
            const m = statusMeta(p);
            const s = p.stats;
            return (
              <button
                key={p.id}
                className={`allx-card${p.id === activePid ? " watching" : ""}`}
                onClick={() => onOpen(p.id)}
                title={`Open “${p.domain}” fullscreen`}
              >
                <div className="allx-top">
                  <span className={`allx-dot dot-${m.dot}${m.live ? " live" : ""}`} />
                  <span className="allx-status">{m.word}</span>
                  {p.id === activePid && <span className="allx-watching">watching</span>}
                </div>
                <div className="allx-domain">{p.domain}</div>
                <div className="allx-stats">
                  <span><b>{s.gaps}</b> gaps</span>
                  <span className="allx-sep">·</span>
                  <span><b>{s.stars}</b>★</span>
                  {s.max_viability > 0 && (
                    <>
                      <span className="allx-sep">·</span>
                      <span>top <b>{s.max_viability}</b></span>
                    </>
                  )}
                </div>
              </button>
            );
          })}
        </div>
        {collapsed && <div className="allx-fade" aria-hidden="true" />}
      </div>

      {overflow > 0 && (
        <div className="allx-more">
          <button className="allx-more-btn" onClick={() => setExpanded((v) => !v)}>
            {collapsed ? `Show ${overflow} more` : "Show less"}
          </button>
        </div>
      )}
    </section>
  );
}
