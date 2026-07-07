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

## 2026-07-07 02:10 — Live streaming visualization (owner's #1 steer)   [commit pending]
What: Built `LiveActivity.tsx` — a streaming-graph panel that *shows the exploration
happening* instead of a spinner. Folds the existing SSE stats stream into a rolling
time-series (added `history: Sample[]` to the ExplorerView store, fed on every event) and
renders, in pure inline SVG on the design tokens:
- a pulsing status line with live throughput ("21.7 nodes/min · 3.4k tok/min");
- three streaming KPI sparkline tiles (Tokens / Nodes+queued / Gaps+★);
- a primary "Cumulative spend" timeline area chart with the frontier depth as a faint
  backing band and a ● marker each time a new gap lands, plus a pulsing head dot.
No backend change, no chart lib, no external requests — all driven by data already on the
stream, so it can't interfere with running explorations.
Why: Owner asked to "visualize the process ongoing… streaming graphs etc, not just loading
screens."
Verification: `tsc --noEmit` clean; `vite build` succeeds. **Real browser (headless
Chromium via Playwright):** created a fresh exploration and watched it from t=0 — captured
the live SSE transition (frame 0: 0 tok/1 node → frame 5: 1.1k tok/8 nodes, rate 21.7
nodes/min, timeline path grew), confirming tiles + chart update live (tokensChanged=true,
nodesChanged=true). Screenshot: `verification/live-activity/frame-5.png`. Zero uncaught JS
errors (one benign favicon 404 on the dev server).
Follow-ups: gap-discovery markers (●) only populate once a run reaches the gap layer; deep
tool-using pressure tests are ~90s each so structural growth streams first. A global
persistent usage bar (feature F) is the natural next step.

## 2026-07-07 02:45 — Feature F: global usage bar + SSE teardown fix   [commit pending]
What:
1. **Global usage bar (SPEC §6/§10.3).** Added `UsageGovernor.snapshot()` (real measured
   spend/rate/mode/backoff/concurrency), a `GET /api/usage` endpoint, and a persistent
   `GlobalUsageBar.tsx` that polls it every 2s — a slim always-on strip showing combined
   24h spend, live token rate, shared cooperating mode, running count, and shared cap.
   Neutral by default; vermillion only in the rate-limited/backoff state. Driven by real
   governor numbers, not a client-side guess.
2. **SSE teardown bug fixed.** `_event_source`'s `finally` called `agen.aclose()` while the
   in-flight `pending = agen.__anext__()` task was still live, raising
   `RuntimeError: aclose(): asynchronous generator is already running` on every client
   disconnect. Now it awaits the cancelled task before closing, guarding both — teardown
   never raises. (This was spamming the log 3× per disconnect × 18 subscriptions.)
Why: Make usage first-class and always-visible (owner's visibility steer); stop the SSE
stream from erroring on disconnect (degrade-don't-crash).
Verification: `pytest -q` → 10 passed (+1 snapshot test); `ruff` clean; `tsc`/`vite build`
clean. **Live:** `/api/usage` returns measured numbers; the bar rendered "Sprinting · 1.2k
tokens today · 0 tok/min · 15 running · shared cap 8×" in headless Chromium (screenshot
`verification/global-usage-bar/bar.png`), 0 page errors. SSE fix confirmed: **0 `aclose`
errors** after the fix reload despite the browser opening/closing 18 SSE connections
(all prior errors predate the reload). Real governor spend observed climbing (0 → 1203).
Follow-ups: many stale "running" projects from prior sessions clutter the tab bar (dead
workers, benign) — a boot-time reconciliation to mark orphaned runs as paused would tidy
the UI; deferred (touches user data).

## 2026-07-07 03:05 — Feature C: adversarial self-critique on each scored gap   [commit pending]
What: After a gap is pressure-tested and scored, a cheap single-turn meta-pass
(`adversarial_self_critique`) names the SINGLE strongest reason the viability score is
wrong (too high or too low), recorded on `PressureTest.self_critique` and shown in the
NodeInspector pressure panel (vermillion-accented block). Gated to standard/deep rigor so
light/curbing stays cheap. Degrades to "" on any failure and rejects non-prose output.
(`test_rigor` + confidence were already surfaced in the inspector — verified.)
Why: The owner's #1 priority is engine depth; a score you can't second-guess is a weaker
signal than one that carries its own strongest objection.
Verification: `pytest -q` → 11 passed (+1 degrade/format test); ruff/tsc/vite clean.
**Live Claude:** on a solo-therapist-notes gap scored 21/100, the critique argued the score
is *too low* — "HIPAA compliance is a solvable cost-of-entry… not a structural void… market
pull is severely under-weighted" — correctly catching an *under*-score, not just piling on.
The backend/data path is verified on real Claude; the inspector render typechecks + builds.
Follow-ups: a full in-browser screenshot of the critique in the inspector needs a run that
reaches a scored gap (~minutes on the deep path) — deferred; render is a simple conditional
block already covered by tsc + the verified data shape.

## 2026-07-07 08:10 — Explorer UX overhaul: sidebar + search, fix delete + tree   [commit pending]
What: Reworked the autonomous screen from a cramped horizontal tab bar (only ~5 of 17
projects visible) into a proper **app shell** — a left **`ExplorationSidebar`** with search
+ a scrollable, ranked list of every exploration (status dot, gaps/★, spend), a scrolling
main column, and the global usage bar pinned at the bottom.
Fixes for the owner's report:
1. **Delete now works** — the backend DELETE was fine (200→404); the frontend used a
   blocking `window.confirm` and a whole-view delete button. Replaced with a reliable
   inline-confirm 🗑 on each sidebar row (id-based `onDelete`, optimistic removal, falls
   through to the next exploration). Verified in-browser: 17→16, 0 errors.
2. **Tree view no longer "messed up"** — the tree overflowed its card and collided *behind*
   the sticky global bar. Root cause: the layout had no bounded scroll region. New app-shell
   makes `.exp-main` the single internal scroll region (grid row `minmax(0,1fr)` so it
   actually constrains) and moves the global bar outside the scroll area — verified the main
   column is now clipped above the bar (mainBottom 903 ≤ barTop 913, mainScrolls true).
3. **More intuitive** — sidebar + live search (typing "note" → 17→9 rows), scales past a
   handful of runs.
4. **Immediate-then-grow** confirmed: creating an exploration shows its root node at once
   (1 node), then the decompose grows it — verified the new project is created running with
   1 node and switches in.
Also: **stopped opening an SSE stream for every project at once** (17 open EventSources meant
the network never idled and didn't scale) — now only the ACTIVE exploration streams live;
the sidebar stays fresh via a light 4s `listProjects` poll.
Why: direct owner feedback — delete broken, tree messed up, wants sidebar/search.
Verification: `tsc`/`vite build` clean. Headless-Chromium checks above; screenshot
`verification/ui-review/redesign.png`. 0 page errors throughout.
Follow-ups: `ProjectTabs.tsx` is now only used for its `statusMeta` export (component
unused) — could be trimmed. A formal Impeccable critique pass would be the next polish step.

## 2026-07-07 08:30–11:10 — Owner feature sprint (batch)   [commits de39420 … b00a3c8]
Ten owner-requested features, each committed + pushed to `main` and verified live in a
real (headless Chromium) browser and/or on live Claude. In order:
- **Parallelize agents** (`de39420`): the frontier loop now fans out on the expensive
  gap-yielding work — consecutive top-priority SEGMENT nodes synthesize + pressure-test
  concurrently in shared governor slots (`concurrency_for(pace)`), cheap structural
  decomposition stays serial. `AP_MAX_CONCURRENCY`-configurable (default ~2× cores).
  Verified: fixture e2e now yields 20 gaps scored in parallel.
- **tmux persistence** (`6f5f7a4`): `scripts/dev-tmux.sh` runs backend (no `--reload`) +
  frontend in a persistent session so runs don't die on a file save or a closed terminal.
- **Dynamic usage limit** (`6f5f7a4`): `governor.set_policy(daily_cap, limit_pct)` +
  `POST /api/usage/policy`; spend auto-curbs as it nears cap×limit% then pauses. Snapshot
  exposes level (low/med/high/heavy), ratio, projected_24h. Verified live on :8030.
- **Viability scale unified** (`6e12e82`): macro/structural nodes show the ⌀average
  viability of their descendant sub-ideas (0..100), not a bare child-count. Verified:
  8 ⌀ chips (⌀41..⌀54) alongside gap chips.
- **Avg-viability-over-time** (`6e12e82`): 4th LiveActivity KPI tile + sparkline.
- **Usage gauge** (`6e12e82`): GlobalUsageBar → claude-usage-style level + ratio bar +
  projected + a ⚙ popover to set the percent limit.
- **⌘K command palette** (`d55b2bb`): jump to any exploration / run quick actions; verified
  open→filter→jump.
- **Exploration tabs** (`f9e1f03`): Overview / Nodes / full-page Idea detail — a single
  idea opens like the single-area page; a Nodes tab holds the tree. Verified.
- **Home dashboard** (`4090f3e`): autonomous mode lands on a dashboard — usage gauge +
  a card per exploration; ⌂ Home in the sidebar. Verified.
- **Interactive intake** (`b00a3c8`): `POST /api/projects/intake` generates 3-5 steering
  questions (degrades to a static set); answers persist on `Project.intake` and thread
  into decomposition + root rationale. New-exploration dialog gains the intake step.
  Verified live: real questions generated, answers persisted on the created project.
Also fixed earlier the same session: **delete** (backend was fine; frontend used a blocking
confirm → inline-confirm sidebar rows), the **tree overflow/collision** (app-shell single
scroll region), and the **17-simultaneous-SSE-streams** problem (active-only stream + 4s
list poll).
Verification gate held throughout: `pytest` 14 passed, `ruff`/`tsc --noEmit` clean,
`vite build` succeeds, both servers boot. Screenshots in `verification/ui-review/`.
Follow-ups: prove intake divergence with two full real runs; trim unused `ProjectTabs`
component; a formal Impeccable critique pass.
