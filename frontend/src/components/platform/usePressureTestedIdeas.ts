import { useEffect, useMemo, useState } from "react";
import { getTree, listProjects } from "../../autonomous/api";
import { fitViabScore, type Project, type TreeNode } from "../../autonomous/types";

export interface TestedIdea {
  node: TreeNode;
  pid: string;
  domain: string;
}

/**
 * Every pressure-tested gap across all projects, ranked by fit × viability.
 * One-shot REST snapshots (no SSE) — mirrors the home dashboard's hero fetch,
 * shared by the Pressure-test and Compare screens.
 */
export function usePressureTestedIdeas() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [trees, setTrees] = useState<Record<string, TreeNode[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const ps = await listProjects();
        if (!alive) return;
        setProjects(ps);
        const withGaps = ps
          .filter((p) => p.stats.gaps > 0 || p.stats.candidates > 0)
          .sort((a, b) => b.stats.max_viability - a.stats.max_viability)
          .slice(0, 6);
        const entries = await Promise.all(
          withGaps.map(async (p) => {
            try {
              const snap = await getTree(p.id);
              return [p.id, snap.nodes] as const;
            } catch {
              return null;
            }
          })
        );
        if (!alive) return;
        const next: Record<string, TreeNode[]> = {};
        for (const e of entries) if (e) next[e[0]] = e[1];
        setTrees(next);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "Could not load projects.");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  const ideas: TestedIdea[] = useMemo(() => {
    const out: TestedIdea[] = [];
    const domainOf = new Map(projects.map((p) => [p.id, p.domain]));
    for (const [pid, nodes] of Object.entries(trees)) {
      for (const n of nodes) {
        if (
          (n.kind === "gap" || n.kind === "gap_candidate") &&
          n.pressure_test != null &&
          n.pressure_test.lenses.length > 0
        ) {
          out.push({ node: n, pid, domain: domainOf.get(pid) ?? "" });
        }
      }
    }
    // One row per distinct idea; prefer the instance that knows more (has fit),
    // then the stronger one — same dedupe rule as the home dashboard hero.
    const byTitle = new Map<string, TestedIdea>();
    for (const h of out) {
      const key = (h.node.gap?.title ?? h.node.title).toLowerCase();
      const prev = byTitle.get(key);
      if (
        !prev ||
        (h.node.fit != null && prev.node.fit == null) ||
        (Boolean(h.node.fit != null) === Boolean(prev.node.fit != null) &&
          fitViabScore(h.node) > fitViabScore(prev.node))
      ) {
        byTitle.set(key, h);
      }
    }
    return [...byTitle.values()].sort((a, b) => fitViabScore(b.node) - fitViabScore(a.node));
  }, [trees, projects]);

  return { ideas, loading, error };
}
