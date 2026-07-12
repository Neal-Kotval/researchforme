import { useEffect, useRef, useState } from "react";
import { getUsage } from "../../autonomous/api";
import type { GlobalUsage, Project, UsageLevel } from "../../autonomous/types";
import { statusMeta } from "./statusMeta";

interface Props {
  projects: Project[];
  onOpen: (pid: string) => void;
  onNew: () => void;
  onQuickNew: () => void;
}

const LEVEL_WORD: Record<UsageLevel, string> = {
  low: "Low", medium: "Medium", high: "High", heavy: "Heavy",
};

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

/**
 * The autonomous home surface: a prominent `claude usage`-style gauge (level,
 * ratio toward the limit, live rate, projected 24h spend) plus a card for every
 * ongoing exploration — the "glance and dive in" landing.
 */
export default function HomeDashboard({ projects, onOpen, onNew, onQuickNew }: Props) {
  const [usage, setUsage] = useState<GlobalUsage | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const u = await getUsage();
        if (alive) setUsage(u);
      } catch { /* keep last */ }
      if (alive) timer.current = window.setTimeout(poll, 2000);
    };
    poll();
    return () => { alive = false; if (timer.current) window.clearTimeout(timer.current); };
  }, []);

  const level: UsageLevel = usage?.usage_level ?? "low";
  const ratio = usage?.usage_ratio ?? null;
  const pctFill = ratio != null ? Math.min(100, Math.round(ratio * 100)) : null;
  const running = projects.filter((p) => p.status === "running").length;

  const sorted = [...projects].sort((a, b) => {
    const r = (b.status === "running" ? 1 : 0) - (a.status === "running" ? 1 : 0);
    return r !== 0 ? r : (b.updated_at || "").localeCompare(a.updated_at || "");
  });

  return (
    <div className="home-dash">
      <div className="home-head">
        <div>
          <div className="eyebrow">Autonomous mode</div>
          <h2>Your explorations</h2>
          <div className="mod-sub">
            Each card is a live run — open one to see its tree, or point the explorer at a
            new domain.
          </div>
        </div>
        <div className="home-head-actions">
          <button className="btn" onClick={onQuickNew}>⚡ Quick scan</button>
          <button className="btn btn-primary" onClick={onNew}>＋ New exploration</button>
        </div>
      </div>

      {/* usage gauge */}
      <div className={`card home-usage lvl-${level}`}>
        <div className="hu-main">
          <div className="hu-level">
            <span className={`hu-level-word lvl-${level}`}>{usage ? LEVEL_WORD[level] : "—"}</span>
            <span className="hu-level-sub">usage</span>
          </div>
          <div className="hu-figures">
            <div className="hu-fig"><b>{fmt(usage?.daily_spent ?? 0)}</b><span>tokens today</span></div>
            <div className="hu-fig"><b>{fmt(usage?.rate_per_min ?? 0)}</b><span>tokens / min</span></div>
            <div className="hu-fig"><b>~{fmt(usage?.projected_24h ?? 0)}</b><span>projected / day</span></div>
            <div className="hu-fig"><b>{running}</b><span>running now</span></div>
          </div>
        </div>
        {pctFill != null ? (
          <div className="hu-gauge">
            <div className="hu-gauge-track"><span className="hu-gauge-fill" style={{ width: `${pctFill}%` }} /></div>
            <div className="hu-gauge-cap">
              {pctFill}% of {fmt(usage!.effective_cap ?? 0)} limit
              {usage?.daily_cap != null && ` (${Math.round((usage.limit_pct ?? 1) * 100)}% of ${fmt(usage.daily_cap)})`}
            </div>
          </div>
        ) : (
          <div className="hu-nocap">No usage limit set — the bar below can set one.</div>
        )}
      </div>

      {/* exploration cards */}
      {sorted.length === 0 ? (
        <div className="home-empty">
          <div className="ee-mark">◇</div>
          <p>No explorations yet. Start one on any domain and walk away — the explorer maps
          the space, mines live signals, hypothesizes gaps, and red-teams the promising ones.
          Use Quick scan for a fast first read, or New exploration for a full run.</p>
        </div>
      ) : (
        <div className="home-grid">
          {sorted.map((p) => {
            const meta = statusMeta(p);
            const cap = p.budget.max_tokens;
            const pct = cap ? Math.min(100, (p.stats.tokens_spent / cap) * 100) : null;
            return (
              <button key={p.id} className="home-card" onClick={() => onOpen(p.id)}>
                <div className="hc-top">
                  <span className={`es-dot ${meta.dot}${meta.live ? " live" : ""}`} />
                  <span className={`hc-status ${meta.dot}`}>{meta.word}</span>
                </div>
                <div className="hc-domain">{p.domain}</div>
                <div className="hc-stats">
                  <span><b>{p.stats.gaps}</b> gaps</span>
                  <span className="hc-star"><b>{p.stats.stars}</b>★</span>
                  <span><b>{p.stats.max_viability}</b> top</span>
                  <span className="hc-tok">{fmt(p.stats.tokens_spent)} tok</span>
                </div>
                {pct != null && (
                  <div className="hc-bar"><span style={{ width: `${pct}%` }} /></div>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
