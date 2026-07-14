import { describe, it, expect } from "vitest";
import {
  confidenceTrust,
  quadrantOf,
  QUADRANT_LABELS,
  splitPortfolio,
} from "./portfolioPlot";
import type { PortfolioItem } from "../../autonomous/types";

function item(over: Partial<PortfolioItem>): PortfolioItem {
  return {
    project_id: "p1",
    domain: "d",
    node_id: "n",
    title: "t",
    viability: 70,
    fit: 60,
    confidence: "high",
    star: false,
    user_star: false,
    kind: "gap",
    triage: null,
    stage: null,
    updated_at: null,
    ...over,
  };
}

describe("splitPortfolio", () => {
  it("plots only gaps that carry BOTH viability and fit — a null fit is never faked onto the 2×2", () => {
    const both = item({ node_id: "a" });
    const noFit = item({ node_id: "b", fit: null });
    const noViab = item({ node_id: "c", viability: null });
    const { plotted, unplotted } = splitPortfolio([both, noFit, noViab]);
    expect(plotted.map((i) => i.node_id)).toEqual(["a"]);
    expect(unplotted.map((i) => i.node_id)).toEqual(["b", "c"]);
  });
  it("orders the no-steering strip by viability desc, unscored last", () => {
    const { unplotted } = splitPortfolio([
      item({ node_id: "low", fit: null, viability: 20 }),
      item({ node_id: "none", fit: null, viability: null }),
      item({ node_id: "high", fit: null, viability: 90 }),
    ]);
    expect(unplotted.map((i) => i.node_id)).toEqual(["high", "low", "none"]);
  });
});

describe("quadrantOf", () => {
  it("puts high fit × high viability in 'Investigate now' (50 counts as high)", () => {
    expect(quadrantOf(50, 50)).toBe("investigate");
    expect(QUADRANT_LABELS.investigate).toMatch(/investigate now/i);
  });
  it("separates the off-diagonal quadrants", () => {
    expect(quadrantOf(20, 80)).toBe("market_not_yours"); // strong market, weak fit
    expect(quadrantOf(80, 20)).toBe("yours_weak_market"); // fits you, weak market
    expect(quadrantOf(20, 20)).toBe("skip");
  });
  it("labels every quadrant", () => {
    for (const label of Object.values(QUADRANT_LABELS)) {
      expect(label.trim()).not.toBe("");
    }
  });
});

describe("confidenceTrust", () => {
  it("maps confidence to the memo §2 trust ladder — unknown is unverified, never inflated", () => {
    expect(confidenceTrust("high")).toBe("earned");
    expect(confidenceTrust("medium")).toBe("provisional");
    expect(confidenceTrust("low")).toBe("unverified");
    expect(confidenceTrust(null)).toBe("unverified");
  });
});
