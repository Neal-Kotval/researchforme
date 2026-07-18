import { useEffect, useMemo, useState } from "react";
import { getPortfolio, ApiError } from "../../autonomous/api";
import {
  STAGE_LABELS,
  viabilityRamp,
  type PortfolioItem,
} from "../../autonomous/types";
import { confidenceTrust, splitPortfolio, QUADRANT_LABELS } from "./portfolioPlot";
import ViabChip from "../autonomous/ViabChip";

interface Props {
  onOpenNode: (pid: string, nodeId: string) => void;
}

/* Plot geometry — one source of truth for the SVG and the HTML hover card. */
const W = 640;
const H = 400;
const PAD = 34;

function plotX(fit: number): number {
  return PAD + (fit / 100) * (W - 2 * PAD);
}
function plotY(viability: number): number {
  return H - PAD - (viability / 100) * (H - 2 * PAD);
}

/** One-line workflow state for the hover card (neutral facts, no hype). */
function workflowLine(i: PortfolioItem): string {
  const bits: string[] = [];
  if (i.star) bits.push("starred");
  if (i.triage) bits.push(i.triage === "passed" ? "you passed" : "you're interested");
  if (i.stage) bits.push(STAGE_LABELS[i.stage].toLowerCase());
  return bits.join(" · ");
}

/**
 * The H1 portfolio 2×2: every scored gap across every run, fit (x) ×
 * viability (y), in one SVG scatter. Trust rides the dot (memo §2): earned
 * confidence = solid ramp fill, provisional = outlined, unverified = dashed +
 * smaller. The fit ring marks a steering-scored gap; gaps with no fit are
 * NEVER faked onto the plot — they render in the strip below. Click a dot to
 * open its dossier in the explorer.
 */
export default function PortfolioScatter({ onOpenNode }: Props) {
  const [items, setItems] = useState<PortfolioItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<PortfolioItem | null>(null);
  const [stripOpen, setStripOpen] = useState(false);

  const load = () => {
    setError(null);
    getPortfolio()
      .then(setItems)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load the portfolio."));
  };
  useEffect(load, []);

  const { plotted, unplotted } = useMemo(() => splitPortfolio(items ?? []), [items]);

  if (error) {
    return (
      <div className="pf-empty" role="alert">
        ⚠︎ {error}{" "}
        <button className="btn btn-sm" onClick={load} style={{ marginLeft: 8 }}>Retry</button>
      </div>
    );
  }
  if (items == null) {
    return <div className="pf-empty" aria-busy="true">Charting every scored gap…</div>;
  }
  if (items.length === 0) {
    return (
      <div className="pf-empty">
        No scored gaps yet. The 2×2 fills as runs score their candidates — start an
        exploration and the portfolio charts itself.
      </div>
    );
  }

  const stripShown = stripOpen ? unplotted : unplotted.slice(0, 6);

  return (
    <div>
      {plotted.length > 0 ? (
        <div className="pf-scatter-wrap">
          <svg
            className="pf-scatter"
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label={`Portfolio: ${plotted.length} gaps plotted by founder fit and viability`}
          >
            {/* frame + midlines */}
            <rect x={PAD} y={PAD} width={W - 2 * PAD} height={H - 2 * PAD} className="pf-sc-frame" />
            <line x1={W / 2} y1={PAD} x2={W / 2} y2={H - PAD} className="pf-sc-mid" />
            <line x1={PAD} y1={H / 2} x2={W - PAD} y2={H / 2} className="pf-sc-mid" />
            {/* quadrant labels */}
            <text x={W - PAD - 8} y={PAD + 16} textAnchor="end" className="pf-sc-quad strong">
              {QUADRANT_LABELS.investigate}
            </text>
            <text x={PAD + 8} y={PAD + 16} className="pf-sc-quad">
              {QUADRANT_LABELS.market_not_yours}
            </text>
            <text x={W - PAD - 8} y={H - PAD - 8} textAnchor="end" className="pf-sc-quad">
              {QUADRANT_LABELS.yours_weak_market}
            </text>
            <text x={PAD + 8} y={H - PAD - 8} className="pf-sc-quad">
              {QUADRANT_LABELS.skip}
            </text>
            {/* axes */}
            <text x={W / 2} y={H - 8} textAnchor="middle" className="pf-sc-axis">
              Founder fit →
            </text>
            <text
              x={12}
              y={H / 2}
              textAnchor="middle"
              transform={`rotate(-90 12 ${H / 2})`}
              className="pf-sc-axis"
            >
              Viability →
            </text>
            {/* dots — trust-encoded; the fit ring marks steering-scored gaps */}
            {plotted.map((i) => {
              const x = plotX(i.fit!);
              const y = plotY(i.viability!);
              const trust = confidenceTrust(i.confidence);
              const r = trust === "unverified" ? 4.5 : i.star ? 7 : 5.5;
              return (
                <g
                  key={`${i.project_id}:${i.node_id}`}
                  className={`pf-sc-dot trust-${trust}${i.triage === "passed" ? " passed" : ""}`}
                  role="button"
                  tabIndex={0}
                  aria-label={`${i.title} — viability ${i.viability}, fit ${i.fit}. Open dossier.`}
                  onClick={() => onOpenNode(i.project_id, i.node_id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onOpenNode(i.project_id, i.node_id);
                    }
                  }}
                  onMouseEnter={() => setHover(i)}
                  onMouseLeave={() => setHover((h) => (h === i ? null : h))}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover((h) => (h === i ? null : h))}
                >
                  <circle className="pf-sc-ring" cx={x} cy={y} r={r + 3.5} />
                  <circle
                    className="pf-sc-core"
                    cx={x}
                    cy={y}
                    r={r}
                    style={
                      trust === "earned"
                        ? { fill: viabilityRamp(i.viability) }
                        : { stroke: viabilityRamp(i.viability) }
                    }
                  />
                  {i.star && (
                    <text x={x} y={y - r - 6} textAnchor="middle" className="pf-sc-star">★</text>
                  )}
                </g>
              );
            })}
          </svg>
          {hover && (
            <div
              className="pf-sc-tip"
              style={{
                left: `${(plotX(hover.fit!) / W) * 100}%`,
                top: `${(plotY(hover.viability!) / H) * 100}%`,
              }}
            >
              <div className="pf-sc-tip-title">{hover.title}</div>
              <div className="pf-sc-tip-meta">
                {hover.domain ?? "unknown run"} · viab {hover.viability} · fit {hover.fit}
                {hover.confidence ? ` · ${hover.confidence} confidence` : " · unscored confidence"}
              </div>
              {workflowLine(hover) && (
                <div className="pf-sc-tip-flow">{workflowLine(hover)}</div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div style={{ fontSize: "var(--fs-secondary)", color: "var(--slate)", lineHeight: 1.5, maxWidth: "62ch" }}>
          Nothing is plottable yet — the 2×2 needs gaps scored under founder steering.
          Every scored gap below ran without steering, so it has no fit.
        </div>
      )}

      {unplotted.length > 0 && (
        <div className="pf-nofit-strip">
          <div className="pf-nofit-head">
            <span className="pf-nofit-title">No steering — fit unscored</span>
            <span className="pf-nofit-sub">
              These ran without founder steering, so they carry no fit and stay off the
              plot — nothing is faked onto it. Re-run with steering to place them.
            </span>
          </div>
          <div className="pf-nofit-rows">
            {stripShown.map((i) => (
              <div className="pf-nofit-row" key={`${i.project_id}:${i.node_id}`}>
                <ViabChip value={i.viability} trust={confidenceTrust(i.confidence)} star={i.star} />
                <span className="pf-nofit-name">
                  {i.title}
                  <small> · {i.domain ?? "unknown run"}</small>
                </span>
                <button className="btn btn-sm" onClick={() => onOpenNode(i.project_id, i.node_id)}>
                  Open
                </button>
              </div>
            ))}
          </div>
          {unplotted.length > 6 && (
            <button className="btn btn-sm pf-nofit-more" onClick={() => setStripOpen((v) => !v)}>
              {stripOpen ? "Show fewer" : `Show all ${unplotted.length}`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
