import { describe, expect, it } from "vitest";
import { statusMeta } from "./statusMeta";
import type { Project } from "../../autonomous/types";

function proj(over: Partial<Project["stats"]>, status: Project["status"] = "running"): Project {
  return {
    id: "p", domain: "d", status,
    stats: { nodes: 1, gaps: 0, candidates: 0, stars: 0, tokens_spent: 0,
             max_viability: 0, frontier_size: 1, mode: "sprinting", ...over },
  } as unknown as Project;
}

describe("statusMeta — fetch-phase honesty", () => {
  it("a running run with 0 tokens reads as 'researching', not 'sprinting'", () => {
    // The pre-LLM source-fetch phase spends no tokens; 'sprinting · 0 tok' looked hung.
    const m = statusMeta(proj({ tokens_spent: 0 }));
    expect(m.word).toBe("researching");
    expect(m.live).toBe(true);
  });

  it("once tokens flow, it reads as 'sprinting'", () => {
    expect(statusMeta(proj({ tokens_spent: 5000, mode: "sprinting" })).word).toBe("sprinting");
  });

  it("a paused run is never 'researching' even at 0 tokens", () => {
    expect(statusMeta(proj({ tokens_spent: 0 }, "paused")).word).toBe("paused");
  });
});
