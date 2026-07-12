import type { Project } from "../../autonomous/types";

type DotKind = "sprinting" | "curbing" | "paused" | "done" | "error";

/** Map a project's status + governor mode to a status-dot kind + word. */
export function statusMeta(p: Project): { dot: DotKind; word: string; live: boolean } {
  switch (p.status) {
    case "running":
      if (p.stats.mode === "sprinting") return { dot: "sprinting", word: "sprinting", live: true };
      if (p.stats.mode === "curbing") return { dot: "curbing", word: "curbing", live: true };
      return { dot: "sprinting", word: "running", live: true };
    case "paused":
      return { dot: "paused", word: "paused", live: false };
    case "usage_paused":
      return { dot: "curbing", word: "usage-paused", live: false };
    case "milestone_paused":
      return { dot: "curbing", word: "check-in", live: false };
    case "exhausted":
      return { dot: "done", word: "exhausted", live: false };
    case "budget_spent":
      return { dot: "done", word: "budget spent", live: false };
    case "time_limit":
      return { dot: "done", word: "time limit", live: false };
    case "errored":
      return { dot: "error", word: "errored", live: false };
    default:
      return { dot: "paused", word: p.status, live: false };
  }
}
