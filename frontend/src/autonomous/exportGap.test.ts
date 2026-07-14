import { describe, expect, it } from "vitest";
import { gapToMarkdown } from "./exportGap";
import type { Project, TreeNode } from "./types";

const project = {
  id: "p1",
  domain: "AI hardware efficiency",
  steering: { brief: "solo technical founder", constraints: ["software only"] },
} as unknown as Project;

const node = {
  id: "n1",
  kind: "gap",
  title: "Kernel CI",
  viability: 33,
  fit: 88,
  confidence: "high",
  user_star: true,
  gap: {
    title: "The Kernel Equivalence Harness",
    thesis: "Nobody owns the layer that proves a fused kernel is still correct.",
    why_now: "Kernel authoring fragmented across tilelang and Rust->PTX.",
    wedge: "Free OSS kernel-diff runner for attention kernels.",
    scores: { demand_strength: 2, feasibility: 4 },
    riskiest_assumption: "That kernel teams pay rather than write bench.py.",
    weakest_link: "demand_strength — zero demand evidence in this run.",
    competitors: [
      { name: "Triton", positioning: "kernel DSL", weakness: "single-backend", url: "u" },
    ],
    evidence: [
      { source: "github", url: "https://github.com/x", quote: "tilelang kernels", live: true },
    ],
    tags: [],
  },
  pressure_test: {
    summary: "WEAKENED: 0 survived, 4 weakened, 0 killed",
    survived: 0,
    weakened: 4,
    killed: 0,
    self_critique: "33 encodes false precision on an untested hypothesis.",
    lenses: [
      { lens: "demand_mirage", verdict: "weakens", argument: "Demand is UNMEASURED.", evidence: [] },
    ],
  },
} as unknown as TreeNode;

describe("gapToMarkdown", () => {
  it("carries the thesis, wedge and why-now", () => {
    const md = gapToMarkdown(node, project);
    expect(md).toContain("The Kernel Equivalence Harness");
    expect(md).toContain("Nobody owns the layer");
    expect(md).toContain("Free OSS kernel-diff runner");
    expect(md).toContain("Kernel authoring fragmented");
  });

  it("is self-contained: states the domain and the founder's steering", () => {
    const md = gapToMarkdown(node, project);
    expect(md).toContain("AI hardware efficiency");
    expect(md).toContain("solo technical founder");
    expect(md).toContain("software only");
  });

  it("NEVER exports the pitch without the red team's criticism", () => {
    const md = gapToMarkdown(node, project);
    expect(md).toContain("demand mirage");
    expect(md).toContain("Demand is UNMEASURED.");
    expect(md).toContain("Riskiest assumption");
    expect(md).toContain("That kernel teams pay rather than write bench.py.");
    // The engine's critique of its own score must survive the export too.
    expect(md).toContain("false precision");
  });

  it("explains what the scores mean, since a fresh chat has no context", () => {
    const md = gapToMarkdown(node, project);
    expect(md).toContain("Viability 33/100");
    expect(md).toContain("Founder fit 88/100");
    expect(md).toMatch(/Viability = is this a real, enterable market gap/);
  });

  it("marks mock evidence as not live", () => {
    const mocked = {
      ...node,
      gap: {
        ...node.gap,
        evidence: [{ source: "reddit", url: "u", quote: "q", live: false }],
      },
    } as unknown as TreeNode;
    expect(gapToMarkdown(mocked, project)).toContain("mock/fixture");
  });

  it("flags fixture-provenance gaps loudly", () => {
    const fixture = {
      ...node,
      gap: { ...node.gap, tags: ["fixture"] },
    } as unknown as TreeNode;
    expect(gapToMarkdown(fixture, project)).toContain("Provenance warning");
  });

  it("ends with questions so the paste starts a conversation", () => {
    const md = gapToMarkdown(node, project);
    expect(md).toContain("What I'd like from you");
    expect(md).toContain("why now");
  });

  it("handles a structural node with no gap payload without inventing one", () => {
    const bare = { id: "n2", kind: "segment", title: "Some branch", gap: null } as unknown as TreeNode;
    const md = gapToMarkdown(bare, project);
    expect(md).toContain("Some branch");
    expect(md).toContain("not a scored gap");
    expect(md).not.toContain("Thesis");
  });
});
