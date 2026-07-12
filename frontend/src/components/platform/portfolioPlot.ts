// Pure helpers behind the Compare 2×2 portfolio scatter (H1). No fetch, no
// DOM — everything here is unit-tested. Codebase rule: a gap with no fit
// score is NEVER faked onto the plot; it lands in the "no steering" strip.
import type { Confidence, PortfolioItem, TrustLevel } from "../../autonomous/types";

export interface SplitPortfolio {
  /** Gaps carrying BOTH viability and fit — the only ones the 2×2 may plot. */
  plotted: PortfolioItem[];
  /** Everything else (null fit or null viability) — the strip below the plot. */
  unplotted: PortfolioItem[];
}

/** Split the portfolio into plottable dots and the no-steering strip. The
 *  strip is ordered by viability desc with unscored gaps last. */
export function splitPortfolio(items: PortfolioItem[]): SplitPortfolio {
  const plotted: PortfolioItem[] = [];
  const unplotted: PortfolioItem[] = [];
  for (const i of items) {
    (i.viability != null && i.fit != null ? plotted : unplotted).push(i);
  }
  unplotted.sort((a, b) => (b.viability ?? -1) - (a.viability ?? -1));
  return { plotted, unplotted };
}

export type Quadrant = "investigate" | "market_not_yours" | "yours_weak_market" | "skip";

/** Which quadrant a plotted gap sits in; the midline (50) counts as high. */
export function quadrantOf(fit: number, viability: number): Quadrant {
  const f = fit >= 50;
  const v = viability >= 50;
  if (f && v) return "investigate";
  if (!f && v) return "market_not_yours";
  if (f && !v) return "yours_weak_market";
  return "skip";
}

export const QUADRANT_LABELS: Record<Quadrant, string> = {
  investigate: "Investigate now",
  market_not_yours: "Strong market, weak fit",
  yours_weak_market: "Fits you, weak market",
  skip: "Skip",
};

/** Trust ladder for portfolio dots (memo §2). The portfolio rollup carries no
 *  test rigor, so confidence alone decides — unknown reads as unverified,
 *  never inflated to earned. */
export function confidenceTrust(confidence: Confidence | null): TrustLevel {
  if (confidence === "high") return "earned";
  if (confidence === "medium") return "provisional";
  return "unverified";
}
