import { useCallback, useEffect, useState } from "react";
import { listProjects } from "../autonomous/api";
import type { Project } from "../autonomous/types";

export const ACTIVE_STATUSES = new Set([
  "running",
  "paused",
  "usage_paused",
  "milestone_paused",
]);

/**
 * The single projects poll for the platform (shell live-pill, Home, Explore).
 * One 10s interval at App level — views receive the same array instead of
 * each running their own fetch loop.
 */
export function useProjects(intervalMs = 10_000) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setProjects(await listProjects());
      setLoaded(true);
    } catch {
      /* transient — next poll retries; views handle the empty case */
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, intervalMs);
    return () => window.clearInterval(id);
  }, [refresh, intervalMs]);

  return { projects, loaded, refresh };
}

/** Most recently touched active run — the Explore screen's default target. */
export function latestActive(projects: Project[]): Project | null {
  const act = projects
    .filter((p) => ACTIVE_STATUSES.has(p.status))
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
  return act[0] ?? null;
}
