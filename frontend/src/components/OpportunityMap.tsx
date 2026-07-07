import { useMemo } from "react";
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  CartesianGrid,
  ReferenceLine,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { RankedGap } from "../types";

interface Props {
  gaps: RankedGap[];
  selectedTitle: string | null;
  onSelect: (title: string) => void;
}

/* ---- trend-tailwind (1..5) → sequential ramp token (neutral → vermillion) -
   Bound to the --ramp-* design tokens so the chart stays on the system. */
function tailwindColor(score: number): string {
  const i = Math.min(4, Math.max(0, Math.round(score) - 1));
  return `var(--ramp-${i})`;
}

/** Small deterministic jitter so integer-scored bubbles don't stack exactly. */
function jitter(seed: string): number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) h = (h ^ seed.charCodeAt(i)) * 16777619;
  return (((h >>> 0) % 1000) / 1000 - 0.5) * 0.3;
}
const clamp = (v: number, lo = 1, hi = 5) => Math.min(hi, Math.max(lo, v));

interface Datum {
  x: number;
  y: number;
  z: number;
  feas: number;
  color: string;
  title: string;
  thesis: string;
  composite: number;
  rank: number;
  isTop: boolean;
  selected: boolean;
}

export default function OpportunityMap({ gaps, selectedTitle, onSelect }: Props) {
  const data: Datum[] = useMemo(
    () =>
      gaps.map((rg) => {
        const s = rg.gap.scores;
        return {
          x: clamp(s.competitive_openness + jitter(rg.gap.title + "x")),
          y: clamp(s.demand_strength + jitter(rg.gap.title + "y")),
          z: s.feasibility,
          feas: s.feasibility,
          color: tailwindColor(s.trend_tailwind),
          title: rg.gap.title,
          thesis: rg.gap.thesis,
          composite: rg.composite,
          rank: rg.rank,
          isTop: rg.rank === 1,
          selected: rg.gap.title === selectedTitle,
        };
      }),
    [gaps, selectedTitle]
  );

  /* Custom bubble: size ∝ feasibility, fill ∝ tailwind, ring for top/selected. */
  const Bubble = (props: any) => {
    const { cx, cy, payload } = props as { cx: number; cy: number; payload: Datum };
    if (cx == null || cy == null) return null;
    const r = 9 + payload.feas * 4.4; // ~13..31px
    // The winner is rendered in the accent so it reads instantly; every other
    // bubble is colored by its trend-tailwind ramp token.
    const fill = payload.isTop ? "var(--accent)" : payload.color;
    return (
      <g
        style={{ cursor: "pointer" }}
        onClick={() => onSelect(payload.title)}
        role="button"
        tabIndex={0}
        aria-label={`Gap ${payload.rank}: ${payload.title}`}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(payload.title)}
      >
        {payload.isTop && (
          <circle className="top-ring" cx={cx} cy={cy} r={r + 8} fill="none" stroke="var(--accent)" strokeWidth={1.6} strokeDasharray="3 4" />
        )}
        {payload.selected && (
          <circle cx={cx} cy={cy} r={r + 4} fill="none" stroke="var(--text)" strokeWidth={2} />
        )}
        <circle
          cx={cx}
          cy={cy}
          r={r}
          fill={fill}
          fillOpacity={payload.selected ? 0.95 : 0.82}
          stroke="var(--text)"
          strokeOpacity={0.18}
          strokeWidth={1}
          style={{ transition: "fill-opacity .15s ease" }}
        />
        {payload.isTop && (
          <text x={cx} y={cy + 4} textAnchor="middle" fontSize={13} fontWeight={800} fill="var(--text-on-accent)" pointerEvents="none">
            ★
          </text>
        )}
      </g>
    );
  };

  const CustomTooltip = ({ active, payload }: any) => {
    if (!active || !payload?.length) return null;
    const d: Datum = payload[0].payload;
    return (
      <div className="map-tooltip">
        <div className="mt-rank">{d.isTop ? "★ Top pick · " : ""}Rank #{d.rank}</div>
        <div className="mt-title">{d.title}</div>
        <div className="mt-thesis">{d.thesis}</div>
        <div className="mt-foot">
          <span>Composite <b>{d.composite.toFixed(2)}</b></span>
          <span>Feasibility <b>{d.feas}</b></span>
        </div>
      </div>
    );
  };

  return (
    <div className="card map-card">
      <div className="map-head">
        <div className="titles">
          <div className="eyebrow">Opportunity map</div>
          <h2 className="card-title">Where the openings are</h2>
          <p className="card-desc">
            X — competitive openness · Y — demand strength · bubble size — feasibility · color — trend tailwind
          </p>
        </div>
      </div>

      <div className="map-plot">
        {/* quadrant labels overlaid on the plot corners */}
        <div className="quadrant-label q-tr">
          <span className="qtag">Build now</span>
          <span className="qsub">high demand · wide open</span>
        </div>
        <div className="quadrant-label q-tl">
          <span className="qtag">Crowded but wanted</span>
          <span className="qsub">demand meets incumbents</span>
        </div>
        <div className="quadrant-label q-br">
          <span className="qtag">Open but quiet</span>
          <span className="qsub">space exists, demand thin</span>
        </div>
        <div className="quadrant-label q-bl">
          <span className="qtag">Skip</span>
          <span className="qsub">low demand · crowded</span>
        </div>

        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 16, right: 24, bottom: 34, left: 12 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="2 4" />
            <XAxis
              type="number"
              dataKey="x"
              domain={[0.5, 5.5]}
              ticks={[1, 2, 3, 4, 5]}
              tickLine={false}
              axisLine={{ stroke: "var(--border-strong)" }}
              label={{ value: "Competitive openness  →", position: "insideBottom", offset: -18, fill: "var(--text-faint)", fontSize: 12 }}
            />
            <YAxis
              type="number"
              dataKey="y"
              domain={[0.5, 5.5]}
              ticks={[1, 2, 3, 4, 5]}
              tickLine={false}
              axisLine={{ stroke: "var(--border-strong)" }}
              label={{ value: "Demand strength  →", angle: -90, position: "insideLeft", offset: 18, fill: "var(--text-faint)", fontSize: 12 }}
            />
            <ZAxis type="number" dataKey="z" range={[120, 900]} />
            <ReferenceLine x={3} stroke="var(--border-strong)" strokeDasharray="5 5" />
            <ReferenceLine y={3} stroke="var(--border-strong)" strokeDasharray="5 5" />
            <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: "3 3", stroke: "var(--border-strong)" }} />
            <Scatter data={data} shape={<Bubble />} isAnimationActive={false} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      <div className="legend">
        <div className="legend-item">
          <span>Trend tailwind</span>
          <span className="legend-grad" aria-hidden />
          <span style={{ color: "var(--text-ghost)" }}>low → high</span>
        </div>
        <div className="legend-item legend-sizes">
          <span>Feasibility</span>
          <span className="dot" style={{ width: 8, height: 8 }} />
          <span className="dot" style={{ width: 13, height: 13 }} />
          <span className="dot" style={{ width: 18, height: 18 }} />
        </div>
        <div className="legend-item">
          <span style={{ color: "var(--accent)" }}>★</span> Top pick
        </div>
      </div>
    </div>
  );
}
