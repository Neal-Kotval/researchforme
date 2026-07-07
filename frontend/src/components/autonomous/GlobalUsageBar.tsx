import { useEffect, useRef, useState } from "react";
import { getUsage } from "../../autonomous/api";
import type { ExplorerMode, GlobalUsage, Project } from "../../autonomous/types";

interface Props {
  projects: Project[];
}

const MODE_WORD: Record<ExplorerMode, string> = {
  sprinting: "Sprinting",
  curbing: "Curbing usage",
  paused: "Idle",
};

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

/**
 * The always-on global usage bar (SPEC §6/§10.3): a slim, persistent strip
 * driven by the shared governor's *real* snapshot (GET /api/usage) — combined
 * 24h spend, live token rate, the shared cooperating mode, and how many
 * explorations are running right now. Neutral by default; it turns vermillion
 * only in the "near a limit / backing off" state, per the design system.
 */
export default function GlobalUsageBar({ projects }: Props) {
  const [usage, setUsage] = useState<GlobalUsage | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const u = await getUsage();
        if (alive) setUsage(u);
      } catch {
        /* transient — keep the last good reading */
      }
      if (alive) timer.current = window.setTimeout(poll, 2000);
    };
    poll();
    return () => {
      alive = false;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, []);

  const running = projects.filter((p) => p.status === "running").length;
  const mode: ExplorerMode = usage?.mode ?? "paused";
  const alert = !!usage && (usage.in_backoff || usage.recent_limits > 0);

  return (
    <div className={`global-usage${alert ? " alert" : ""}`} role="status" aria-live="polite">
      <div className="gu-left">
        <span className={`gu-dot ${mode}`} />
        <span className="gu-mode">{alert ? "Curbing — rate limited" : MODE_WORD[mode]}</span>
      </div>

      <div className="gu-metrics">
        <span className="gu-metric">
          <b>{fmt(usage?.daily_spent ?? 0)}</b> tokens today
        </span>
        <span className="gu-sep">·</span>
        <span className="gu-metric">
          <b className="gu-rate">{fmt(usage?.rate_per_min ?? 0)}</b> tok/min
        </span>
        <span className="gu-sep">·</span>
        <span className="gu-metric">
          <b>{running}</b> running
        </span>
        {usage && usage.in_backoff && (
          <>
            <span className="gu-sep">·</span>
            <span className="gu-metric gu-backoff">
              resumes ~{Math.ceil(usage.backoff_remaining_s)}s
            </span>
          </>
        )}
      </div>

      <div className="gu-right">
        <span className="gu-conc">shared cap {usage?.max_concurrency ?? "–"}×</span>
      </div>
    </div>
  );
}
