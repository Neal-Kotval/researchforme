import { useEffect, useRef, useState } from "react";
import { getUsage, setUsagePolicy, setConcurrency } from "../../autonomous/api";
import type { ExplorerMode, GlobalUsage, Project, UsageLevel } from "../../autonomous/types";

interface Props {
  projects: Project[];
}

const MODE_WORD: Record<ExplorerMode, string> = {
  sprinting: "Sprinting",
  curbing: "Curbing usage",
  paused: "Idle",
};

const LEVEL_WORD: Record<UsageLevel, string> = {
  low: "Low usage",
  medium: "Medium usage",
  high: "High usage",
  heavy: "Heavy usage",
};

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

/**
 * Always-on global usage gauge (owner ask: a `claude usage`-style read-out).
 * Driven by the shared governor's real snapshot: a low/medium/high/heavy level,
 * a ratio bar toward the effective cap (cap × limit%), the live token rate,
 * projected 24h spend at the current rate, and how many explorations are running.
 * A ⚙ popover sets a **dynamic percent limit** the governor auto-shapes spend
 * around. Vermillion only in the near-limit / rate-limited state.
 */
export default function GlobalUsageBar({ projects }: Props) {
  const [usage, setUsage] = useState<GlobalUsage | null>(null);
  const [editing, setEditing] = useState(false);
  const [capInput, setCapInput] = useState("");
  const [pctInput, setPctInput] = useState(95);
  const [concInput, setConcInput] = useState(32);
  const [concDirty, setConcDirty] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const u = await getUsage();
        if (alive) setUsage(u);
      } catch {
        /* transient — keep last good reading */
      }
      if (alive) timer.current = window.setTimeout(poll, 2000);
    };
    poll();
    return () => {
      alive = false;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, []);

  // Seed the editor from the live policy the first time it's known.
  useEffect(() => {
    if (usage) {
      if (usage.daily_cap != null) setCapInput(String(usage.daily_cap));
      setPctInput(Math.round((usage.limit_pct ?? 1) * 100));
    }
  }, [usage?.daily_cap, usage?.limit_pct]);

  // Track the live concurrency ceiling until the user starts dragging the slider.
  useEffect(() => {
    if (usage?.max_concurrency != null && !concDirty) setConcInput(usage.max_concurrency);
  }, [usage?.max_concurrency, concDirty]);

  const applyConcurrency = async (n: number) => {
    try {
      const u = await setConcurrency(n);
      setUsage(u);
      setConcDirty(false);
    } catch {
      /* keep the slider where the user left it */
    }
  };

  const running = projects.filter((p) => p.status === "running").length;
  const mode: ExplorerMode = usage?.mode ?? "paused";
  const level: UsageLevel = usage?.usage_level ?? "low";
  const alert = !!usage && (usage.in_backoff || usage.recent_limits > 0 || level === "heavy");
  const ratio = usage?.usage_ratio ?? null;
  const pctFill = ratio != null ? Math.min(100, Math.round(ratio * 100)) : null;

  const applyPolicy = async () => {
    const cap = parseInt(capInput, 10);
    try {
      const u = await setUsagePolicy({
        daily_cap_tokens: Number.isNaN(cap) || cap <= 0 ? null : cap,
        limit_pct: Math.max(5, Math.min(100, pctInput)) / 100,
      });
      setUsage(u);
      setEditing(false);
    } catch {
      /* leave the popover open on failure */
    }
  };

  return (
    <div className={`global-usage lvl-${level}${alert ? " alert" : ""}`} role="status" aria-live="polite">
      <div className="gu-left">
        <span className={`gu-dot ${mode}`} />
        <span className={`gu-level lvl-${level}`}>
          {usage ? LEVEL_WORD[level] : "…"}
        </span>
        <span className="gu-mode-sub">· {MODE_WORD[mode].toLowerCase()}</span>
      </div>

      {pctFill != null && (
        <div className="gu-gauge" title={`${pctFill}% of the ${fmt(usage!.effective_cap ?? 0)} effective limit`}>
          <span className="gu-gauge-fill" style={{ width: `${pctFill}%` }} />
          <span className="gu-gauge-pct">{pctFill}%</span>
        </div>
      )}

      <div className="gu-metrics">
        <span className="gu-metric"><b>{fmt(usage?.daily_spent ?? 0)}</b> today</span>
        <span className="gu-sep">·</span>
        <span className="gu-metric"><b className="gu-rate">{fmt(usage?.rate_per_min ?? 0)}</b>/min</span>
        <span className="gu-sep">·</span>
        <span className="gu-metric" title="Projected 24h spend if the current rate holds">
          ~<b>{fmt(usage?.projected_24h ?? 0)}</b>/day proj.
        </span>
        <span className="gu-sep">·</span>
        <span className="gu-metric"><b>{running}</b> running</span>
        <span className="gu-sep">·</span>
        <span className="gu-metric" title="Agents working in parallel right now / the ceiling you set">
          <b>{usage?.active_concurrency ?? 0}</b>/{usage?.max_concurrency ?? concInput} agents
        </span>
        {usage?.in_backoff && (
          <>
            <span className="gu-sep">·</span>
            <span className="gu-metric gu-backoff">resumes ~{Math.ceil(usage.backoff_remaining_s)}s</span>
          </>
        )}
      </div>

      <div className="gu-right">
        <button className="gu-limit-btn" onClick={() => setEditing((v) => !v)} title="Set concurrency + a dynamic usage limit">
          ⚙ {concInput} agents{usage?.daily_cap ? ` · ${pctInput}%` : ""}
        </button>
      </div>

      {editing && (
        <div className="gu-popover" onClick={(e) => e.stopPropagation()}>
          <div className="gu-pop-title">Parallel agents</div>
          <label className="gu-pop-row gu-pop-conc">
            <span>Run <b>{concInput}</b> at once</span>
            <input
              type="range" min={1} max={100} step={1}
              value={concInput}
              onChange={(e) => { setConcInput(Number(e.target.value)); setConcDirty(true); }}
              onMouseUp={() => applyConcurrency(concInput)}
              onKeyUp={() => applyConcurrency(concInput)}
              onTouchEnd={() => applyConcurrency(concInput)}
            />
          </label>
          <div className="gu-pop-quick">
            {[8, 16, 32, 64, 100].map((n) => (
              <button
                key={n}
                className={`gu-quick${concInput === n ? " on" : ""}`}
                onClick={() => { setConcInput(n); applyConcurrency(n); }}
              >{n}</button>
            ))}
          </div>
          <p className="gu-pop-hint">
            How many explorations expand in parallel across all runs. The rate-limit backoff is
            the safety valve, so a high setting just idles spare slots when the model is slow.
          </p>
          <div className="gu-pop-divider" />
          <div className="gu-pop-title">Dynamic usage limit</div>
          <label className="gu-pop-row">
            <span>Daily token cap (100%)</span>
            <input
              type="number" min={0} placeholder="e.g. 2000000"
              value={capInput} onChange={(e) => setCapInput(e.target.value)}
            />
          </label>
          <label className="gu-pop-row">
            <span>Stay under <b>{pctInput}%</b></span>
            <input
              type="range" min={5} max={100} step={5}
              value={pctInput} onChange={(e) => setPctInput(Number(e.target.value))}
            />
          </label>
          <p className="gu-pop-hint">
            The fleet auto-curbs as spend nears cap × {pctInput}%, then pauses — so runs shape
            themselves around your limit.
          </p>
          <div className="gu-pop-actions">
            <button className="btn" onClick={() => setEditing(false)}>Cancel</button>
            <button className="btn btn-primary" onClick={applyPolicy}>Apply</button>
          </div>
        </div>
      )}
    </div>
  );
}
