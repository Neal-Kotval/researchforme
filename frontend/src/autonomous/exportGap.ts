/**
 * Serialize a gap into a portable markdown brief for pasting into any chat
 * assistant (ChatGPT, Claude, Gemini, a doc, a colleague's inbox).
 *
 * Three rules make the output actually useful somewhere else:
 *
 * 1. SELF-CONTAINED. The receiving assistant has none of this app's context, so
 *    the brief states the domain, the founder's steering, and what the numbers
 *    mean inline. A bare "viability 33" is meaningless in a fresh chat.
 * 2. HONEST. Confidence, the unverified flag, fixture provenance, and the
 *    pressure-test kills are exported alongside the thesis — never just the
 *    optimistic half. Exporting the pitch without the red team would launder a
 *    weak idea into a confident-looking brief.
 * 3. NO LLM, NO NETWORK. Pure function over data already in the tree, so it is
 *    instant, free, and cannot fail (unlike the research pack, which is a
 *    strong-model call and honestly 503s when no backend can produce one).
 *
 * It closes with questions for the receiving assistant, so a paste lands as a
 * conversation starter rather than a wall of text.
 */
import type { Project, TreeNode } from "./types";

const KILL_LABEL: Record<string, string> = {
  kills: "KILLED",
  weakens: "weakened",
  survives: "survived",
};

function lensName(key: string): string {
  return key.replace(/_/g, " ");
}

/** Human label for the engine's score axes (1–5). */
const SCORE_LABEL: Record<string, string> = {
  demand_strength: "Demand strength",
  competitive_openness: "Competitive openness",
  trend_tailwind: "Trend tailwind",
  feasibility: "Feasibility",
  willingness_to_pay: "Willingness to pay",
};

export function gapToMarkdown(node: TreeNode, project?: Project | null): string {
  const g = node.gap;
  const L: string[] = [];
  const title = g?.title ?? node.title;

  L.push(`# Market gap: ${title}`);
  L.push("");
  L.push(
    "*Exported from Gap Finder, an autonomous market-gap explorer. It proposed this " +
      "gap from live sources, then red-teamed it. Both halves are below — please treat " +
      "the criticism as seriously as the pitch.*",
  );
  L.push("");

  if (project) {
    L.push(`**Domain explored:** ${project.domain}`);
    const brief = project.steering?.brief?.trim();
    if (brief) L.push(`**My context (steering the search):** ${brief}`);
    const cons = project.steering?.constraints ?? [];
    if (cons.length) L.push(`**My hard constraints:** ${cons.join("; ")}`);
    L.push("");
  }

  if (!g) {
    // A structural (non-gap) node: export what little is real, never invent.
    L.push(`**Node type:** ${node.kind} (not a scored gap — no thesis yet)`);
    if (node.rationale?.trim()) {
      L.push("");
      L.push(node.rationale.trim());
    }
    return L.join("\n");
  }

  /* ------------------------------------------------------------- scores -- */
  const scoreBits: string[] = [];
  if (node.viability != null) scoreBits.push(`**Viability ${node.viability}/100**`);
  if (node.fit != null) scoreBits.push(`**Founder fit ${node.fit}/100**`);
  if (node.confidence) scoreBits.push(`confidence: ${node.confidence}`);
  if (scoreBits.length) {
    L.push(scoreBits.join(" · "));
    L.push(
      "> Viability = is this a real, winnable market gap. Founder fit = is it a " +
        "good fit for *me* specifically. They are independent: a great gap can be a " +
        "bad fit, and vice versa.",
    );
    L.push("");
  }

  if (g.tags?.includes("fixture")) {
    L.push(
      "> ⚠️ **Provenance warning:** this gap was generated from canned fixture data, " +
        "not a live model run. Treat it as a placeholder, not a finding.",
    );
    L.push("");
  }

  /* ------------------------------------------------------------- thesis -- */
  L.push("## Thesis");
  L.push(g.thesis || "_(none stated)_");
  L.push("");

  /* Plain-language first: the brief gets pasted into docs and shown to people
     who do not live in this domain. Older gaps have no easy_explain — omit the
     section rather than printing an empty heading. */
  if (g.easy_explain?.trim()) {
    L.push("## In plain language");
    L.push(g.easy_explain.trim());
    L.push("");
  }

  if (g.why_now?.trim()) {
    L.push("## Why now");
    L.push(g.why_now.trim());
    L.push("");
  }

  if (g.wedge?.trim()) {
    L.push("## The wedge (how you'd enter)");
    L.push(g.wedge.trim());
    L.push("");
  }

  /* ------------------------------------------------------------ company -- */
  const c = g.company;
  if (c) {
    L.push("## Company shape");
    if (c.product) L.push(`- **Product:** ${c.product}`);
    if (c.icp) L.push(`- **Customer (ICP):** ${c.icp}`);
    if (c.business_model) L.push(`- **Business model:** ${c.business_model}`);
    if (c.expansion_path) L.push(`- **Expansion path:** ${c.expansion_path}`);
    if (c.moat) L.push(`- **Claimed moat:** ${c.moat}`);
    L.push("");
  }

  /* ------------------------------------------------------------- scores -- */
  if (g.scores) {
    const rows = Object.entries(g.scores).filter(([, v]) => typeof v === "number");
    if (rows.length) {
      L.push("## Scores (1–5, from the engine)");
      for (const [k, v] of rows) L.push(`- ${SCORE_LABEL[k] ?? k}: ${v}/5`);
      L.push("");
    }
  }

  /* --------------------------------------------------------- the doubts -- */
  if (g.riskiest_assumption?.trim()) {
    L.push("## Riskiest assumption");
    L.push(g.riskiest_assumption.trim());
    L.push("");
  }
  if (g.weakest_link?.trim()) {
    L.push("## Weakest link");
    L.push(g.weakest_link.trim());
    L.push("");
  }

  /* -------------------------------------------------------- competitors -- */
  if (g.competitors?.length) {
    L.push("## Competitors / the status quo");
    for (const comp of g.competitors) {
      const bits = [comp.positioning, comp.price_tier && `price: ${comp.price_tier}`]
        .filter(Boolean)
        .join(" · ");
      L.push(`- **${comp.name}**${bits ? ` — ${bits}` : ""}`);
      if (comp.weakness) L.push(`  - Their weakness: ${comp.weakness}`);
      if (comp.url) L.push(`  - ${comp.url}`);
    }
    L.push("");
  }

  /* ------------------------------------------------------ pressure test -- */
  const pt = node.pressure_test;
  if (pt?.lenses?.length) {
    L.push("## Red team (adversarial pressure test)");
    L.push(
      `Result: ${pt.summary || `${pt.survived} survived · ${pt.weakened} weakened · ${pt.killed} killed`}`,
    );
    L.push("");
    for (const lens of pt.lenses) {
      L.push(`### ${lensName(lens.lens)} — ${KILL_LABEL[lens.verdict] ?? lens.verdict}`);
      L.push(lens.argument);
      L.push("");
    }
    if (pt.self_critique?.trim()) {
      L.push("### The engine's critique of its own score");
      L.push(pt.self_critique.trim());
      L.push("");
    }
  }

  /* ----------------------------------------------------------- evidence -- */
  if (g.evidence?.length) {
    L.push("## Evidence (real sources the gap was built from)");
    for (const e of g.evidence) {
      const live = e.live === false ? " _(mock/fixture — not live)_" : "";
      L.push(`- [${e.source}]${live} ${e.quote}`);
      if (e.url) L.push(`  - ${e.url}`);
    }
    L.push("");
  }

  /* -------------------------------------------------------- the hand-off -- */
  L.push("---");
  L.push("## What I'd like from you");
  L.push(
    "Please pressure-test this with me. Specifically:",
  );
  L.push("1. Is the *why now* real, or would this have been equally true three years ago?");
  // NOT "is it narrow enough to ship solo — or a platform in disguise?". That
  // question assumed a solo, unfunded builder and treated platform scale as a
  // failure mode, so every reader was primed to argue the idea DOWN in scope.
  // Scale is the goal, not the smell. The real question — the one that caught a
  // rival already embedded in NVIDIA's reference design while this gap scored
  // 83 — is whether the ENTRY POINT is takeable, not whether it is small.
  L.push(
    "2. Is the entry point defensible — or is it the commoditized end of this " +
      "market, the part a platform vendor gives away free? If the wedge is weak " +
      "but the space is real, say where you'd start instead.",
  );
  L.push("3. What would you need to see to believe the demand is real? Name a concrete check I can run this week.");
  L.push("4. Who is already doing this, that the analysis above missed? Funded companies, design wins, and vendor partnerships — not just open-source projects.");

  return L.join("\n");
}
