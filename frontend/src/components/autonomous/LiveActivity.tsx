import { useMemo } from "react";
import type { ExplorerMode, Project } from "../../autonomous/types";

/** One folded frame of a project's stats, captured from the SSE stream. */
export interface Sample {
  t: number;
  tokens: number;
  nodes: number;
  gaps: number;
  stars: number;
  candidates: number;
  frontier: number;
  maxViability: number;
  mode: ExplorerMode;
}

interface Props {
  project: Project;
  history: Sample[];
}

const MODE_WORD: Record<ExplorerMode, string> = {
  sprinting: "Sprinting",
  curbing: "Curbing",
  paused: "Idle",
};

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

/* --------------------------------------------------------------- svg paths -- */
// Build a smooth-enough area + line path from a series of values, laid out on an
// even x-grid (index-based reads cleaner than bursty wall-clock gaps for a live
// sparkline). Values are normalized to [0..max] over the given box.
function paths(values: number[], w: number, h: number, pad = 2) {
  if (values.length === 0) return { line: "", area: "" };
  const max = Math.max(1, ...values);
  const n = values.length;
  const x = (i: number) => (n === 1 ? w / 2 : pad + (i / (n - 1)) * (w - pad * 2));
  const y = (v: number) => h - pad - (v / max) * (h - pad * 2);
  let line = "";
  for (let i = 0; i < n; i++) line += `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(values[i]).toFixed(1)} `;
  const area = `${line}L${x(n - 1).toFixed(1)},${h} L${x(0).toFixed(1)},${h} Z`;
  return { line: line.trim(), area };
}

/** Rate of a monotonically-rising counter over the trailing `windowMs`. */
function ratePerMin(history: Sample[], pick: (s: Sample) => number, windowMs = 30_000): number {
  if (history.length < 2) return 0;
  const last = history[history.length - 1];
  const since = last.t - windowMs;
  let anchor = history[0];
  for (let i = history.length - 1; i >= 0; i--) {
    if (history[i].t <= since) { anchor = history[i]; break; }
    anchor = history[i];
  }
  const dt = (last.t - anchor.t) / 60_000;
  if (dt <= 0) return 0;
  return Math.max(0, (pick(last) - pick(anchor)) / dt);
}

/* ------------------------------------------------------------------ tiles -- */
function StatTile({
  label, value, sub, series, accent,
}: {
  label: string;
  value: string;
  sub?: string;
  series: number[];
  accent?: boolean;
}) {
  const { line, area } = paths(series, 96, 30);
  return (
    <div className={`la-tile${accent ? " accent" : ""}`}>
      <div className="la-tile-head">
        <span className="la-tile-label">{label}</span>
        {sub && <span className="la-tile-sub">{sub}</span>}
      </div>
      <div className="la-tile-val">{value}</div>
      <svg className="la-spark" viewBox="0 0 96 30" preserveAspectRatio="none" aria-hidden="true">
        <path className="la-spark-area" d={area} />
        <path className="la-spark-line" d={line} />
      </svg>
    </div>
  );
}

/* =============================================================== LiveActivity */
/**
 * The exploration, *shown happening* (owner ask: "streaming graphs, not loading
 * screens"). Folds the live SSE stats series into: a pulsing status line with
 * live throughput, three streaming KPI sparklines (spend / nodes / gaps), and a
 * primary timeline area chart of cumulative spend with the frontier as a faint
 * backing band and a ● discovery marker each time a new gap lands. All inline
 * SVG on the design-system tokens — no chart lib, no external requests.
 */
export default function LiveActivity({ project, history }: Props) {
  const s = project.stats;
  const running = project.status === "running";

  const {
    tokensSeries, nodesSeries, gapsSeries, frontierSeries, gapMarkers, spendArea, spendLine, frontierArea,
  } = useMemo(() => {
    const h = history.length ? history : [
      { t: Date.now(), tokens: s.tokens_spent, nodes: s.nodes, gaps: s.gaps, stars: s.stars, candidates: s.candidates, frontier: s.frontier_size, maxViability: s.max_viability, mode: s.mode } as Sample,
    ];
    const tokensSeries = h.map((p) => p.tokens);
    const nodesSeries = h.map((p) => p.nodes);
    const gapsSeries = h.map((p) => p.gaps);
    const frontierSeries = h.map((p) => p.frontier);
    // Discovery markers: indices where the gap count ticked up.
    const gapMarkers: number[] = [];
    for (let i = 1; i < h.length; i++) if (h[i].gaps > h[i - 1].gaps) gapMarkers.push(i);
    const spend = paths(tokensSeries, 640, 132, 4);
    const front = paths(frontierSeries, 640, 132, 4);
    return {
      tokensSeries, nodesSeries, gapsSeries, frontierSeries, gapMarkers,
      spendArea: spend.area, spendLine: spend.line, frontierArea: front.area,
    };
  }, [history, s]);

  const nodeRate = ratePerMin(history, (p) => p.nodes);
  const tokRate = ratePerMin(history, (p) => p.tokens);

  // x of a marker index on the 640-wide chart, matching paths()'s layout.
  const n = Math.max(tokensSeries.length, 1);
  const markerX = (i: number) => (n === 1 ? 320 : 4 + (i / (n - 1)) * (640 - 8));
  const markerMax = Math.max(1, ...tokensSeries);
  const markerY = (i: number) => 132 - 4 - (tokensSeries[i] / markerMax) * (132 - 8);

  return (
    <div className="card live-activity">
      <div className="la-head">
        <div className="la-title">
          <span className={`la-live-dot ${s.mode}${running ? " on" : ""}`} />
          <h3>Live activity</h3>
        </div>
        <div className="la-status">
          <span className={`mode-pill ${s.mode}`}>
            <span className="mp-dot" />
            {MODE_WORD[s.mode]}
          </span>
          {running && (
            <span className="la-rate">
              {nodeRate >= 0.1 ? `${nodeRate.toFixed(1)} nodes/min` : "warming up…"}
              {tokRate >= 1 ? ` · ${fmt(tokRate)} tok/min` : ""}
            </span>
          )}
        </div>
      </div>

      <div className="la-tiles">
        <StatTile label="Tokens" value={fmt(s.tokens_spent)} series={tokensSeries} accent />
        <StatTile label="Nodes" value={`${s.nodes}`} sub={`${s.frontier_size} queued`} series={nodesSeries} />
        <StatTile label="Gaps" value={`${s.gaps}`} sub={`${s.stars}★`} series={gapsSeries} />
      </div>

      <div className="la-chart">
        <div className="la-chart-cap">
          <span>Cumulative spend</span>
          <span className="la-chart-cap-b">frontier depth · ● new gap</span>
        </div>
        <svg className="la-timeline" viewBox="0 0 640 132" preserveAspectRatio="none" role="img"
             aria-label="Streaming chart of cumulative token spend over the exploration">
          <path className="la-frontier-area" d={frontierArea} />
          <path className="la-spend-area" d={spendArea} />
          <path className="la-spend-line" d={spendLine} />
          {gapMarkers.map((i) => (
            <circle key={i} className="la-gap-marker" cx={markerX(i)} cy={markerY(i)} r={3} />
          ))}
          {tokensSeries.length > 0 && (
            <circle
              className={`la-head-dot ${running ? "pulse" : ""}`}
              cx={markerX(tokensSeries.length - 1)}
              cy={markerY(tokensSeries.length - 1)}
              r={4}
            />
          )}
        </svg>
      </div>
    </div>
  );
}
