# Overnight Worklog — Autonomous Deepening

Append-only narrative of the night. Branch `overnight/autonomous-deepening`.

---

## 2026-07-07 00:42 — Orientation & Phase 0 audit   [pre-commit]
What: Read the spec, memory, and full codebase (via a read-only audit subagent).
Established the green baseline and confirmed a real end-to-end run works.
Why: The prompt mandates read-before-write and a real E2E loop before adding anything.
Verification:
- `pytest -q` → 4 passed. `tsc --noEmit` → clean. `vite build` → succeeds.
- Backend booted on :8021 with `llm_backend: agent-sdk` (live Claude), 4/5 live sources.
- Real `POST /api/analyze` (live Haiku) → HTTP 200 in 225s, 6 ranked gaps. Live path works.
- Wrote `AUDIT.md` with architecture map, contract audit, and a severity-ranked bug list.
Findings: 3 HIGH bugs — (1) pressure-test corroboration tools never wired [the seam the
prompt flagged], (2) `note_rate_limit` never called (dead governor backoff), (3) "daily
cap" is a process-lifetime cap that never resets. Plus several MED/LOW.
Follow-ups: Fix the 3 HIGH first (each its own `fix:` + regression test), then build the
requested features. User added a steer: **live streaming visualization** of the ongoing
exploration (animated tree + activity/spend/throughput graphs, not spinners) — folding
this into the live-tree + usage-bar workstream as a first-class concern.

## 2026-07-07 01:15 — Phase 0: three HIGH bug fixes   [commit pending]
What: Fixed the three HIGH findings from the audit, each with a regression test.
1. **Corroboration seam wired (SPEC §5).** Exposed `build_corroboration_tools(area,
   sub_segments)` in `synthesize.py`; added `corroboration_tools_for(...)` to the
   service and passed `tools=` into `pressure_test`, gated by rigor (standard/deep arm
   the five live `search_*` tools; light stays tool-free and cheap). The red team can
   now pull fresh Reddit/HN/arXiv/GitHub/newsletter evidence mid-test.
2. **Rate-limit signal wired (SPEC §6.1).** Added an observer hook + rate-limit
   detection (`_looks_rate_limited`, `_retry_after_of`, `_notify_rate_limit`) to the LLM
   client; the governor registers `note_rate_limit` on first creation. 429/overload
   errors now drive real backoff instead of being dead code with zero callers.
3. **Daily cap is now a rolling 24h window.** Added a bucketed daily meter to the
   governor; `_remaining_fraction` measures `daily_cap_tokens` against trailing-24h spend,
   not the process-lifetime total, so a long-lived server can't falsely exhaust it.
Why: These are the load-bearing fixes several requested features depend on (esp. #1).
Verification: `pytest -q` → 9 passed (was 4; +5 regression tests). `ruff check app` → clean.
Real E2E corroboration confirmation in progress on a stable :8022 backend.
Gotcha logged: running uvicorn with `--reload` kills the in-process worker task when a
file changes mid-run — orphaned a real run at 9 nodes. Verification runs must happen on a
non-reloading server (or after edits are done).
Follow-ups: MED/LOW audit items (fabricated token metering, intra-project concurrency,
cross-branch dedup docs) deferred; will revisit if a feature needs them.

## 2026-07-07 01:35 — Corroboration seam verified on live Claude   [commit f03d6dd]
What: Ran a targeted real-LLM verification of the pressure-test corroboration seam
(`/tmp/verify_corroboration.py`): built the deep-rigor tools via `corroboration_tools_for`,
instrumented each handler to count calls, and ran a real `pressure_test` on a plausible
podcast-tools gap.
Result (live agent-sdk / Haiku): the red team made **3 real tool calls** —
`search_hackernews`, `search_reddit`, `search_github` — with sensible queries, pulled back
**4 lens-fetched evidence items**, and used them to kill the gap (0 survived / 2 weakened /
3 killed → viability 0). GitHub evidence was live (`github.com/tobi/qmd`); Reddit was mock
(keyless-mock in this env) and honestly labeled. The seam is live: lenses now corroborate
against real sources mid-test, exactly as SPEC §5 intends.
Verification: real Claude tool-call counter = 3; lens evidence = 4. Conclusive.

## 2026-07-07 01:40 — Switch to main + push (per owner)   [commit pending]
What: Per the owner's mid-run instruction, fast-forward-merged `overnight/autonomous-deepening`
into `main`, pushed to `origin/main`, and switched to working directly on `main` with
continuous push/sync. Also stopped tracking `frontend/tsconfig.tsbuildinfo` (build artifact).
Why: Owner explicitly authorized working on main and pushing continuously (overrides the
prompt's "never touch main" guardrail — they own the repo).
Verification: `git merge --ff-only` clean; `git push` → 9f7c44f..f03d6dd.
