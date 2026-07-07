# Market Gap Finder — Overnight Audit

_Branch `overnight/autonomous-deepening`. Phase 0 baseline established 2026-07-07._

## Baseline (verified before any change)

- `pytest -q` → **4 passed** (fixture LLM + mock sources).
- `npx tsc --noEmit` → **clean**.
- `npx vite build` → **succeeds** (590 kB bundle; chunk-size warning only).
- Backend boots on a clean port; `/api/health` reports `llm_backend: agent-sdk`
  (live Claude subscription) with 4/5 sources live (reddit → mock in this env).
- **Real end-to-end analysis confirmed:** `POST /api/analyze` with live Claude
  (Haiku) returned HTTP 200 in 225s with 6 ranked gaps
  (`{gap, composite, rank}` shape), `synthesis_model: claude-haiku-4-5-20251001`.
  The live LLM + live-source path works.

## 1. Architecture map

Two subsystems share one FastAPI app (`app/main.py:37-39`, both routers under `/api`).

**Single-area pipeline** (`app/pipeline.py`): `run_analysis` (`pipeline.py:135`) =
scope (`analysis/scope.py:189`) → ingest 5 adapters in parallel (`pipeline.py:79`) →
extract 3 normalized streams (`analysis/extract.py:511`) → synthesize with strict
Gap-array contract + `search_*` tools (`analysis/synthesize.py:488`) → cache → rank
(`analysis/rank.py:31`). `rerank_cached` re-weights with no re-fetch/LLM.

**LLM client** (`app/llm/client.py`): `ClaudeClient.complete` (`client.py:49`) cascades
`api → agent-sdk → cli → fixture` (`_fallback_order`), each degrading on exception.
Only agent-sdk/api support tool calling. Lazy singleton `get_client()`.

**Autonomous mode**: `schemas.py` (Node/Project/Budget/PressureTest/ExplorerEvent) ·
`engine.py` (content-hash `make_node`, `Frontier` max-PQ, `expand_structural`,
`expand_segment` reuses the pipeline) · `pressure.py` (5 kill-lenses → viability) ·
`governor.py` (global `UsageGovernor` singleton) · `store.py` (SQLite event-sourced,
per-project monotonic seq, SSE fan-out) · `service.py` (`_run` best-first frontier loop) ·
`routers/projects.py` (CRUD + `/control` + SSE `/events`).

## 2. Contract audit

| Contract | Verdict | Notes |
|---|---|---|
| (a) Degrade-don't-crash | **HOLDS** (one asymmetry) | Every adapter, synthesize, pressure_test, and `_run` guard + fall back. But `pipeline._fetch_all` (`pipeline.py:91`) omits `return_exceptions=True` (engine's has it) — relies on adapter no-raise contract. |
| (b) No fabricated evidence | **HOLDS as soft contract** | Prompts forbid it; Evidence is schema-validated; mock nodes labeled + confidence-capped. But **nothing programmatically verifies a cited URL appears in mined signals** — a well-formed hallucinated URL passes. |
| (c) Content-hash node ids | **HOLDS as written** | `make_node` id = sha1(project|parent|kind|title)[:16], no timestamp/uuid. But `parent_id` in seed ⇒ documented *cross-branch* dedup does NOT happen (only sibling-level). |
| (d) Event-sourced tree | **HOLDS** | Every mutation → `append_event` (monotonic seq) → persist + SSE fan-out. Snapshot + `events_since` = resumable. |
| (e) Single UsageGovernor | **HOLDS** | Double-checked singleton; service binds once. |
| (f) schemas.py ↔ types.ts parity | **HOLDS** | No field drift found. Cosmetic: `api.ts HealthInfo.llm_mode` vs backend `llm_backend`. |
| (g) Single-area intact | **HOLDS** | Autonomous mode purely additive. |

## 3. CRITICAL: pressure-test corroboration seam is NOT wired

The kill-lenses **cannot** pull fresh evidence mid-test. `pressure_test` accepts a
`tools` param but its **only caller** (`service.py:437`) omits it, so `tools=None`.
`_build_tools` (`synthesize.py:247`) is only ever called inside `synthesize`, never on
the pressure path. The `_PRESSURE_SYSTEM` prompt tells the red-team model it may call
`search_reddit/arxiv/...` (`pressure.py:139`), but those tools are never registered for
pressure calls. Result: the model reasons only over the static gap payload; the live
`evidence_delta` / confidence bonus for freshly-fetched corroboration is unreachable.
**This is the #1 fix** — several requested features depend on it.

## 4. Bugs & smells (severity-ranked)

- **HIGH — Pressure-test corroboration tools never wired.** `service.py:437` / `pressure.py:381`. §3. → build & pass `tools=`.
- **HIGH — `note_rate_limit` never called; governor rate-limit backoff is dead code.** `governor.py:133` has zero callers. Client swallows 429s (`client.py:73`). `headroom` can only ever hit `none` via budget, never real rate-limiting; `CURBING`/`USAGE_PAUSED` unreachable. → client detects 429/`retry-after` → `note_rate_limit`.
- **HIGH — "Daily cap" is a process-lifetime cap, never resets.** `governor._spent_total` accumulates forever (`governor.py:126`); no 24h rollover. Long-lived server falsely exhausts. → rolling 24h window.
- **MED — Token metering fabricated.** `_est(base, len)` guesses (`service.py:685`); every budget/cap/milestone gates on invented numbers.
- **MED — Intra-project concurrency doesn't exist.** `concurrency_for` (`governor.py:101`) has zero callers; `_run` expands one node per iteration. "sprint = full concurrency" not delivered.
- **MED — Cross-branch dedup doesn't work as documented.** `parent_id` in `make_node` seed (`engine.py:67`). Fix docs or drop parent_id.
- **MED — Gap-candidate id collisions silently overwrite** via `INSERT OR REPLACE` (`store.py:119`) when a segment returns two same-title gaps.
- **MED — Milestone re-fire skips multiples** when spend jumps past two thresholds in one step (`service.py:384`).
- **LOW — Keyless Reddit ignores `allow_mock` on empty** (`reddit.py:353`).
- **LOW — SSE registration relies on `asyncio.sleep(0)` timing** (`projects.py:205`).
- **LOW — `pipeline._fetch_all` lacks `return_exceptions=True`** (`pipeline.py:91`).
- **LOW — `HealthInfo.llm_mode` misnamed** vs `llm_backend` (`api.ts:79`).
- **LOW — `Gap.competitors` `max_length=8` dead** (validator slices to 5, `schemas.py:152`).

## 5. Test coverage gaps

Only 2 backend test files; frontend has none. Untested: pressure-tools wiring,
governor rate-limiting / headroom transitions / backoff / daily-cap, intra-project
concurrency, source degrade paths (live→UNAVAILABLE/EMPTY/429), synthesize parser
robustness, cross-branch/collision dedup, SSE snapshot+replay+disconnect, router
error branches, schema-parity check, resume/`_rebuild_frontier`.

## Phase 0 fix plan (this session)

1. **fix:** wire live corroboration tools into `pressure_test` (+ regression test).
2. **fix:** surface 429/`retry-after` from the client → `note_rate_limit` (+ test).
3. **fix:** make the daily cap a rolling 24h window (+ test).
4. Defer MED/LOW that don't block the requested features; log deferrals in WORKLOG.
