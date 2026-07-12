# Phases 2–4 Build Contract

_Binding technical contract for the one-shot Phases 2–4 build (2026-07-12). Read with
STRATEGY.md (intent) and feature-expansion-catalog.md (rationale). Rules that bind every
task: degrade-don't-crash; fixture-safe; no fabrication (null/absent over invented);
schemas.py ↔ frontend types.ts parity; every mutation through the event store; NO
unattended LLM spend — anything scheduled/background is opt-in and default OFF._

## Phase 2 — Sensors & memory

### S1. Triage (interested/pass)
- `Node.triage: Optional[Literal["interested","passed"]]`, `Node.triage_reason: str = ""`.
- Reason taxonomy (frontend picker; free text allowed): `too_crowded | not_my_skills |
  too_small | boring | no_distribution | other`.
- Control action `set_triage {node_id, triage|null, triage_reason}` → persists node,
  emits `node_updated`. Clearing triage = null.

### S2. Look-into checklist (stars)
- `Node.stage: Optional[Literal["found","interviewing","smoke_testing","verdict_build","verdict_pass"]]`,
  `Node.learnings: str = ""`.
- Control action `set_stage {node_id, stage|null, learnings}`.

### S3. Anti-portfolio graveyard
- `GET /api/graveyard?q=&limit=` → cross-project list of rejected gaps: killed
  (any lens kill OR viability ≤ 40) or `triage == "passed"`; each item {project_id,
  project_domain, node_id, title, thesis_first_line, viability, kill_lenses[],
  triage_reason, updated_at}. Store-level SQL over ap_nodes, no LLM.
- `graveyard_context_block(store, domain, cap=12)` — most-relevant rejected gaps
  (token overlap with domain) rendered as: "SPACES ALREADY REJECTED — do not re-propose;
  DO flag if the recorded kill reason has expired." Injected into the decomposition
  prompt beside steering_context_block.

### S4. Post-mortem corpus
- New source adapter `postmortems` in the registry ONLY IF the research task confirms a
  keyless, stable public source; otherwise ship a curated seed corpus
  (`sources/fixtures/postmortems.json`, ~30 real documented startup failures with
  segment keywords + kill reason + year + citation URL) exposed via the same Source ABC
  (live path = fixture path, honestly reported as MOCK until a live source exists).
- Consumed by: pressure test `empty_for_a_reason`/`crowded` lens payload (matching
  post-mortems appended as corroboration context) and the graveyard endpoint (merged,
  flagged `external: true`).

## Phase 3 — Continuous operation

### C1. New adapters (each: Source ABC, never-raise, fixture + tests, config caps, registry)
- `jobs` — HN "Who is hiring" via existing Algolia plumbing + RemoteOK / WeWorkRemotely
  RSS (feedparser, keyless). Signal thesis: recurring role across distinct companies.
- `appreviews` — iTunes RSS customer-review feeds (keyless, per-app); adapter searches
  the iTunes Search API for the segment's top apps then pulls 1–3★ reviews; item body =
  review text, weight = recurrence of complaint terms.
- `regulatory` — Federal Register API (keyless): documents matching segment keywords,
  type RULE/PRORULE; item body = abstract; carries effective/comment dates. Primary
  consumer: synthesis `why_now`.
- `outcomes` — YC public directory via the yc-oss static API (research task confirms
  URL) + optional curated funding fixtures. Queried at pressure-test time by the
  `crowded` + `empty_for_a_reason` lenses ("funded companies in this segment and their
  status"). NOT in the default 5-source demand mix — registered with
  `pressure_only: true` (extend registry to mark adapters excluded from _fetch_all).
- Funding-round extraction (NOT a new adapter): extraction pass in extract.py over
  newsletter items matching funding patterns → `SupplySignal`-like crowding hints
  threaded to the pressure payload.

### C2. Space Watch
- `Node.watched: bool = False`; control actions `watch_node` / `unwatch_node`.
- `WatchService` (new module `autonomous/watch.py`): `sweep()` re-fetches sources for
  each watched node's keywords (source fetch only, NO LLM), diffs vs the `ingest:*`
  cache snapshot (new items count, weight delta, any regulatory/outcomes hits), and on
  material shift (≥3 new items or new regulatory match) emits event type `watch_alert`
  {node_id, summary, evidence[]} + project log line.
- Trigger: `POST /api/watch/sweep` (manual, and what tests use). A background periodic
  sweep exists but is **opt-in**: `Settings.watch_sweep_hours: Optional[float] = None`
  (env `WATCH_SWEEP_HOURS`); None = no background task ever starts.
- `GET /api/watch` → watched nodes + last alert per node (dashboard movers block).

### C3. Re-run & diff
- `POST /api/projects/{pid}/rerun` → new project, same domain/steering/budget,
  `parent_project_id: Optional[str]` set; autostart per request flag.
- `GET /api/projects/{pid}/diff?against={other}` → node-level diff by normalized gap
  title match: {new: [...], gone: [...], moved: [{title, viability_from, viability_to,
  fit_from, fit_to}]}. Pure store computation, no LLM.

### C4. Idle-headroom scavenger — opt-in only
- `Budget.allow_idle_deepening: bool = False` per project. When True AND governor
  reports ample AND project is terminal-exhausted with unexpanded starred branches, a
  `continue_deepening` control becomes available (manual button; NO automatic trigger
  in this build — the seam is the control action + a `scavenger_candidates` helper).

## Phase 4 — Synthesis & hand-off

### H1. Portfolio
- `GET /api/portfolio` → every scored gap across projects: {project_id, domain, node_id,
  title, viability, fit, confidence, star, triage, stage, updated_at}. Store-level.
- Frontend route `#/portfolio`: SVG 2×2 scatter fit (x) × viability (y), trust-encoded
  dots (vermillion ring = fit present), quadrant labels ("Investigate now" top-right),
  hover card, click → deep link. Gaps with null fit render in a separate "no steering"
  strip below the chart, never faked onto the plot.

### H2. Research Pack
- `POST /api/projects/{pid}/nodes/{nid}/research-pack` → ONE strong-model call over the
  gap payload → markdown: (1) 5 interview scripts quoting actual evidence items as
  openers (only live evidence; mock quotes excluded), (2) landing-page smoke-test plan,
  (3) bottoms-up sizing sketch from the demand signals, (4) riskiest-assumption →
  cheapest-falsification map. Cached on the node (`Node.research_pack: str = ""`),
  regenerate with `?refresh=1`. Degrade: 503 with honest detail (never a canned pack).
- UI: button on gap inspector (starred emphasized) → modal render + download .md.

### H3. Preference distillation — reviewable before applied
- `POST /api/preferences/distill` → cheap-model pass over accumulated triage events
  (reasons + titles) → proposed `learned_preferences: str` stored with
  `status: "pending"` in a new `ap_preferences` table (single active row).
- `GET /api/preferences` / `POST /api/preferences` {learned_preferences, status:
  "active"|"dismissed"} — user reviews/edits/confirms.
- ONLY `status=="active"` text is appended to `steering_context_block` output (clearly
  headed "LEARNED PREFERENCES (user-confirmed)"). Pending text is never injected.
- UI: quiet dashboard card when ≥8 triage events and no active prefs: "Distill what
  your passes say" → review/edit/confirm flow.

### H4. End-of-run digest
- On terminal transition (exhausted/budget_spent/time_limit), service makes ONE
  cheap-model call → `Project.digest: Optional[dict]` {top_spaces: [3 titles+why],
  kill_pattern: str, next_questions: [2-3]}. Degrade: deterministic digest (top by
  viability, most-common kill lens) flagged `degraded: true`. Emitted via
  project_updated.
- UI: digest card at top of the Overview tab; next_questions offer "start a follow-up
  run" prefill.

## Frontend integration map (types.ts parity for ALL new fields)
Exploration surfaces: triage I/P buttons + reason picker on gap inspector (and
keyboard i/p when inspector focused); stage select + learnings on starred gaps; watch
toggle on gap/segment inspector; research-pack button/modal; digest card. New routes:
`#/portfolio`, `#/graveyard` (searchable list, kill-reason chips, "kill reason expired?"
watch shortcut). Dashboard: "Recent signals" block (watch alerts via GET /api/watch),
preference-distill card. All new UI follows docs/design/ui-evolution.md (two-hue
semantics, trust encoding, instructional one-liners, empty/loading/error triads).

## Sequencing (conflict map)
Wave A parallel: [sensors S1+S2 (schemas/service/routers/store)] ∥ [adapters C1
(sources/*, config, registry)] ∥ [funding extraction (extract.py)].
Wave B sequential (share service/store/routers): S3 graveyard (+S4 corpus) → C2 watch →
H4 digest + H2 research-pack → H3 preferences → C3 rerun/diff + C4 scavenger seam.
Wave D frontend sequential: d1 exploration surfaces → d2 new routes + dashboard blocks.
Wave E: verify (suite, fixture E2E, parity check) → Playwright attack-and-fix.
