// Wave D1 exploration surfaces: the pure logic behind the sensor UI —
// digest normalization (H4) and the S1/S2 vocabularies mirrored from
// backend/app/autonomous/schemas.py.
import { describe, expect, it } from "vitest";
import {
  normalizeDigest,
  STAGES,
  STAGE_LABELS,
  TRIAGE_REASONS,
  TRIAGE_REASON_LABELS,
} from "./types";

describe("normalizeDigest", () => {
  it("passes a well-formed digest through untouched", () => {
    const d = normalizeDigest({
      top_spaces: [{ title: "A", why: "because" }],
      kill_pattern: "crowded everywhere",
      next_questions: ["what about EU?"],
      degraded: false,
    });
    expect(d).toEqual({
      top_spaces: [{ title: "A", why: "because" }],
      kill_pattern: "crowded everywhere",
      next_questions: ["what about EU?"],
      degraded: false,
    });
  });

  it("returns null for absent / non-object / empty digests (never invents)", () => {
    expect(normalizeDigest(null)).toBeNull();
    expect(normalizeDigest(undefined)).toBeNull();
    expect(normalizeDigest("nope")).toBeNull();
    expect(normalizeDigest([])).toBeNull();
    expect(normalizeDigest({})).toBeNull();
    expect(normalizeDigest({ top_spaces: [], kill_pattern: "", next_questions: [] })).toBeNull();
  });

  it("drops malformed entries instead of fabricating fields", () => {
    const d = normalizeDigest({
      top_spaces: [{ title: "Keep" }, { why: "no title" }, null, 42],
      next_questions: ["ok", 7, "", "also ok"],
      kill_pattern: 123,
    });
    expect(d).not.toBeNull();
    expect(d!.top_spaces).toEqual([{ title: "Keep", why: "" }]);
    expect(d!.next_questions).toEqual(["ok", "also ok"]);
    expect(d!.kill_pattern).toBe("");
  });

  it("flags the deterministic fallback only on an explicit degraded: true", () => {
    expect(normalizeDigest({ kill_pattern: "x", degraded: true })!.degraded).toBe(true);
    expect(normalizeDigest({ kill_pattern: "x" })!.degraded).toBe(false);
    expect(normalizeDigest({ kill_pattern: "x", degraded: "yes" })!.degraded).toBe(false);
  });
});

describe("S1/S2 vocabularies (backend parity)", () => {
  it("carries the contract's pass-reason taxonomy, each with a label", () => {
    expect(TRIAGE_REASONS).toEqual([
      "too_crowded",
      "not_my_skills",
      "too_small",
      "boring",
      "no_distribution",
      "other",
    ]);
    for (const r of TRIAGE_REASONS) {
      expect(TRIAGE_REASON_LABELS[r]).toBeTruthy();
    }
  });

  it("orders the look-into stages found → verdict, each with a label", () => {
    expect(STAGES).toEqual([
      "found",
      "interviewing",
      "smoke_testing",
      "verdict_build",
      "verdict_pass",
    ]);
    for (const s of STAGES) {
      expect(STAGE_LABELS[s]).toBeTruthy();
    }
  });
});
