# Overnight Autonomous Build — Market Gap Finder

> Paste this whole file as the opening instruction to an autonomous agent (e.g. `claude`
> running headless overnight) from the repo root. It is written to be run **unattended for
> hours**. Do not wait for human input — make the best decision, log it, and keep going.

---

## 0. Who you are tonight

You are a senior full-stack engineer with the whole night alone on this repo. Your job is
to make **Market Gap Finder** — and especially its **autonomous exploration mode** —
materially better by morning: deeper, more organic, better verified, and more useful to a
founder who spawns an explorer and walks away.

The owner does **not** care about token/API spend tonight. Spend freely on **real**
verification: real source fetches, real Claude synthesis and pressure-testing, real
end-to-end runs, and real browser testing. Correctness and depth beat frugality.

**Prime directive when unsure:** don't stop and ask. Choose the option most consistent
with the existing design (below), write one line in `WORKLOG.md` explaining the call, and
continue. Only truly irreversible or destructive actions warrant leaving the decision for
the morning — and those you simply *don't do*.

---

## 1. Orientation — read before you touch anything

Spend your first pass reading, not writing. The app is already substantially built; a
rewrite is a failure mode, not a goal. Read, in order:

- `README.md`, `PRODUCT.md`, `DESIGN.md`, `SPEC-AUTONOMOUS.md`
- `.claude/projects/*/memory/gapfinder-autonomous-mode.md` (locked design decisions)
- Backend single-area pipeline: `backend/app/pipeline.py`, `analysis/{scope,extract,synthesize,rank}.py`, `schemas.py`, `sources/*`, `llm/client.py`, `cache.py`, `config.py`
- Backend autonomous mode: `backend/app/autonomous/{schemas,engine,pressure,governor,service,store}.py`, `routers/projects.py`
- Frontend: `frontend/src/App.tsx`, `api.ts`, `autonomous/{api,types}.ts`, `components/autonomous/*`
- Tests: `backend/tests/{test_pipeline,test_autonomous}.py`

**Contracts you must preserve** (these are load-bearing — violating them is a regression):

1. **Degrade-don't-crash.** No missing key, rate limit, blocked source, or unparseable LLM
   output may ever crash a run. Every layer falls back (no creds → mock fixtures; no LLM →
   deterministic fixtures/fallbacks). The worker task **never raises out**.
2. **No fabricated evidence.** Every gap's evidence traces to a real signal/tool result.
   Mock-sourced nodes are labeled and capped in confidence. Never invent URLs or quotes.
3. **Content-hash node ids** (`engine.make_node`) give free dedup — keep ids a pure
   function of `(project, parent, kind, title)`, never a timestamp/uuid.
4. **Event-sourced tree.** Every state change is an `ExplorerEvent` appended to the store
   and tailed to the SSE stream. New state must flow through the same event path so the
   live UI and resumability keep working.
5. **Shared, global usage governor.** One `UsageGovernor` singleton meters all projects so
   concurrent runs cooperate on the subscription. Don't add a second metering path.
6. **Frontend/​backend schema parity.** `frontend/src/autonomous/types.ts` mirrors
   `backend/app/autonomous/schemas.py`. Any schema change updates both in the same commit.
7. **Single-area mode still works.** `/api/analyze` and its UI are the original product —
   never break them while extending autonomous mode.

Write what you learn — current state, architecture, and every bug/smell you spot — into
`AUDIT.md` before you start changing things.

---

## 2. How to work overnight (non-negotiable process)

**Branch.** Create and stay on `overnight/autonomous-deepening`. Never commit to
`main`/`master`.

**Commit per feature.** Each self-contained improvement is its own commit with a clear,
conventional message (`feat:`, `fix:`, `refactor:`, `test:`, `chore:`). A commit must
leave the repo in a **working, verified** state — tests pass, typecheck passes, servers
boot. Never commit a red build. Small, frequent, green commits > one giant diff.

**`WORKLOG.md` — append-only narrative.** Update it continuously so the morning review
reads like a story. For every unit of work append a block:

```
## <timestamp> — <short title>   [commit <sha>]
What: <what changed, 1–3 sentences>
Why: <the decision / the bug it fixes>
Verification: <exact commands run + result: tests, typecheck, live run, screenshot path>
Follow-ups: <anything deferred, with reasoning>
```

**Verification gate after every feature (this is the point of tonight):**

1. `cd backend && python -m pytest -q` → green.
2. `cd frontend && npx tsc --noEmit` → clean. `npm run build` → succeeds.
3. **Real end-to-end run.** Boot the backend with real Claude (agent-sdk via the local
   Claude Code login — no API key needed) and keyless live sources. Spawn a real
   autonomous project on a real domain and let it produce scored, occasionally-starred
   gaps. Confirm the feature you just built actually fires on real data, not just fixtures.
4. **Browser check.** Playwright is available (`.playwright-mcp/` exists; use the Playwright
   MCP or `npx playwright`). Drive the real UI: create an exploration, watch the live tree
   grow, open a node inspector, exercise your new UI. Save a screenshot into
   `verification/<feature>/` and link it from `WORKLOG.md`. Read the browser console — zero
   uncaught errors.
5. Only then commit and move on.

Prefer running real explorations at `pace=sprint` with generous budgets so you exercise the
deep paths. Keep a couple of long-running real explorations going in the background across
the night and periodically inspect their trees for quality regressions.

**Recovery.** If a change wedges the build and you can't fix it quickly, `git revert` to
the last green commit, log why in `WORKLOG.md`, and take a different approach. Never leave
the tree broken.

---

## 3. What to build — priority order

The owner's #1 priority is **depth of the autonomous engine**. Do these roughly in order;
finish and verify each before starting the next. If you run out of night, it is far better
to have A–D done excellently than all of A–H done shakily.

### Phase 0 — Audit & stabilize (do this first, always)

Run the full real end-to-end loop today, before adding anything. Catalog in `AUDIT.md`:
every bug, race, silent failure, weak fallback, missing test, schema drift between
`schemas.py` and `types.ts`, and any place the degrade-don't-crash or no-fabrication
contract is actually violated. Fix the clear bugs now (each its own `fix:` commit with a
regression test). Note the corroboration seam: `pressure.py`'s lenses are described in the
spec as able to call `search_*` tools to pull fresh evidence — verify whether that's truly
wired to live tools or stubbed, because several features below depend on it.

### A. Start-of-search intake questions (organic clarification)

Today a project starts from a bare `domain` string (`CreateProjectRequest`). Add an
**intake step**: when a user begins an exploration, Claude asks a short, sharp set of
clarifying questions *first* — who the builder is, constraints (regulatory, platform,
b2b/b2c), what "a win" looks like, sub-segments to favor or exclude, time/skill horizon.
The answers steer scope (`scope_area`), frontier priority, and the star threshold.

- Backend: a `POST /api/projects/intake` that takes a raw domain and returns 3–6 generated
  questions (cheap model), each with suggested multiple-choice answers plus free-text.
  Persist answers on the `Project` (new `intake: dict`/typed model) and thread them into
  `root_node` rationale, decomposition prompts, and priority so they actually change the
  tree — not decoration.
- Frontend: an intake panel before the tree view. Skippable (defaults preserve today's
  behavior). Keep the DESIGN.md system — vermillion reserved, hairline borders, no hype.
- Verify: run two real explorations of the same domain with different intake answers and
  confirm the trees measurably diverge. Screenshot both.

### B. Fully organic space generation → autonomous

Add a mode where the user gives **little or nothing** (a vibe, an industry, or literally
"surprise me") and the system **organically proposes candidate spaces to explore**, ranks
them by rough opportunity, and then spins up autonomous projects on the chosen ones.

- Backend: `POST /api/spaces/suggest` — Claude proposes N candidate domains/spaces (each
  with a one-line why-now and a rough pre-score), grounded in a light source sniff so
  suggestions aren't hallucinated. A one-tap "explore this" creates a full project (reusing
  the create + intake flow). Optionally let it auto-spawn several projects at once
  (multi-tab), governed by the shared usage governor.
- Frontend: an empty-state "organic" entry point alongside the existing area input —
  suggested spaces as selectable cards; picking one (or several) launches explorations.
- Verify: from an empty prompt, generate real spaces, launch a real autonomous run on one,
  and confirm end-to-end it reaches scored gaps. Screenshot the suggestion cards + resulting tree.

### C. Deeper, live-corroborated pressure testing

Pressure testing (`pressure.py`, five kill-lenses → viability + confidence + `test_rigor`)
is the heart of "is this gap real." Deepen it:

- Ensure each lens can actually call **live corroboration tools** (`search_*`) to pull
  fresh Reddit/HN/arXiv/GitHub evidence mid-test, and that `evidence_delta` and confidence
  reflect *live* vs mock corroboration honestly (spend is fine — do the real fetches).
- Implement genuine **rigor scaling**: `deep` runs all lenses ×N independent samples and
  majority-votes; `light` runs the two cheapest once. Record how hard it was tested so a
  battle-tested 80 is visibly distinct from a lightly-tested 80 (the schema already has
  `test_rigor` — surface it in the UI).
- Add a small **adversarial self-critique**: after scoring, a pass that tries to find the
  single strongest reason the score is wrong, recorded on the node.
- Verify with real Claude on real gaps; spot-check that `kills` verdicts actually suppress
  viability and that starred gaps survived real scrutiny. Add unit tests for `score_viability`
  edge cases (all-kill, all-survive, mixed, low-confidence-caps-star).

### D. End-of-run questions + "come back to this" digest

When a run reaches a stop condition (`exhausted`/`budget_spent`/`time_limit`) or a milestone
check-in, Claude should **ask the user forward-looking questions** — "want me to go deeper
on branch X?", "should I widen into adjacent space Y?", "pursue the #1 starred gap into a
proposal?" — turning each into a one-tap action (inject frontier nodes, spawn an adjacent
project, or trigger the proposal writer in E).

- Backend: an end-of-run summarizer that reads the tree and emits ranked next-step
  questions with concrete actions attached; wire the actions to existing controls
  (`/projects/{id}/control`, frontier injection, project create).
- Frontend: extend `ProjectDigest.tsx` into the real "come back later" surface — top starred
  gaps, newest high-viability finds since last look, current activity, and the next-step
  question cards.
- Verify: let a real run finish, confirm the questions are grounded in *that* tree, and that
  tapping an action actually changes the run. Screenshot the digest.

### E. Proposal-writing skill (idea → viable build proposal)

Add the ability to turn a strong gap into a **compelling, viability-scored build proposal** —
this is the payoff the owner wants. Given a starred `Gap` node, produce a proposal:
problem/why-now, the wedge, target user, top-5 competitor landscape (reuse existing
competitor data), riskiest assumption + a cheap test to validate it, a rough GTM, and an
explicit **viability verdict** carried over from the pressure test (with its confidence and
`test_rigor` shown honestly).

- Prefer implementing this as a reusable **skill** the app invokes (see `skill-creator`)
  so the proposal format is consistent and improvable, plus a `POST /api/projects/{id}/nodes/{node_id}/proposal`
  endpoint. Offer export as Markdown (and a `.docx` via the docx skill if quick).
- Every claim in the proposal must trace to the node's real evidence — no new fabricated
  facts. If evidence is thin, the proposal says so and lowers the verdict.
- Verify: generate a real proposal from a real starred gap; confirm claims map to evidence
  and the viability verdict matches the node. Save an example proposal into `verification/`.

### F. Global live usage bar + dynamic adjustment polish

`UsageMeter.tsx` and `governor.py` exist. Make usage a **first-class, always-visible global
bar** that updates live as processes run: combined spend across all projects, current shared
mode (sprinting / curbing / paused — resumes ~time), rate, and a running total that ticks up
as real work happens. Make the dynamic adjustment visibly react — kick a run to `sprint`,
watch the bar and mode change; simulate/observe pressure toward a cap and watch it curb then
pause.

- Backend: ensure the governor exposes a clean global snapshot (spent, rate, mode, backoff,
  remaining) over an endpoint or the SSE stream so the bar is driven by real numbers, not a
  guess. Verify the shared semaphore genuinely serializes concurrent projects.
- Frontend: a slim persistent global usage bar (top or bottom), honoring DESIGN.md (neutral
  by default; vermillion only for the "near limit / paused" state). Per-project meters stay.
- Verify: run **two real projects at once**, watch the global bar aggregate both and the
  mode flip under load. Screenshot mid-sprint and near-a-cap.

### G. Searchable database of past searches & explorations

Everything persists to SQLite (`cache.db`, `TreeStore`) but there's no way to **browse and
search past work**. Add a durable, queryable history: past single-area analyses and past
autonomous projects/trees, searchable by domain, keyword, date, viability, star count.

- Backend: a history/search endpoint over the existing store (add indices/tables as needed
  — additive migrations only, never drop the existing cache). Include full-text-ish search
  over domain, gap thesis, and keywords. Let a user reopen an old project's tree.
- Frontend: a "History / Library" surface — searchable list of past explorations with their
  star counts and top gaps, one click to reopen.
- Verify: after running several real explorations, search the history and reopen an old
  tree; confirm it hydrates correctly from the store. Screenshot.

### H. Integration pass — make it all cohere

With A–G in place, do a deliberate **"how does this all work together"** pass. The intended
happy path: *organic space suggestion → intake questions → autonomous run with live-corroborated
pressure testing under a visible global usage bar → end-of-run questions → proposal on the
winner → it all lands in searchable history.* Walk that entire path end-to-end in the real
UI, fix every seam and awkward handoff, and record it as a single narrated flow (a short
screen-recorded GIF via the Playwright gif tool if feasible) in `verification/full-flow/`.

---

## 4. Guardrails — do NOT

- Break single-area mode (`/api/analyze` + its UI) or any existing passing test.
- Fabricate evidence, URLs, quotes, competitors, or metrics anywhere.
- Replace working modules wholesale — extend the pluggable seams (sources, lenses, engine
  strategies) the way the codebase already invites.
- Remove or weaken any degrade-don't-crash fallback, or make the worker task able to raise.
- Introduce a second usage-metering path or per-project governor.
- Let `schemas.py` and `types.ts` drift apart.
- Commit a red build, a failing typecheck, or an unverified feature.
- Push to `main`, force-push, rewrite history, or touch anything outside this repo.

---

## 5. Morning deliverable

By the time the owner wakes up, leave, on branch `overnight/autonomous-deepening`:

- `AUDIT.md` — state of the codebase and every bug found (with which you fixed).
- `WORKLOG.md` — the full narrated, timestamped, commit-linked story of the night.
- `MORNING-REPORT.md` — a crisp top-of-funnel summary: what shipped (per feature: what,
  where, how verified, screenshot links), what real explorations you ran and what they
  found, what you deliberately deferred and why, and the 3–5 things you'd do next.
- `verification/` — screenshots, example proposals, and the full-flow recording.
- A clean, green tree: `pytest` passing, `tsc --noEmit` clean, `npm run build` succeeding,
  both servers booting, one clear commit per feature.

Make tonight count. Depth over breadth, real verification over assumptions, and leave a
morning report the owner can read in five minutes and trust.
