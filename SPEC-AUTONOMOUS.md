# SPEC — Autonomous Exploration Mode

> Status: design spec (not yet built). Extends the existing Market Gap Finder
> from a single-area, request/response tool into a **long-running, self-directed
> explorer** you can spawn on several domains at once and walk away from.

---

## 1. The vision in one paragraph

You type a domain — *"embedded AI"* — hit **Explore**, and close the laptop. A
background agent recursively decomposes the domain into sub-areas, mines the three
signal sources per branch, hypothesizes candidate gaps, **pressure-tests** the
promising ones with adversarial verification, assigns each a **viability score
(0–100)**, and keeps going — spending more effort on branches that are paying off
and pruning the ones that aren't. It watches your Claude subscription usage and
throttles itself: it sprints when you have headroom and idles when you're near a
limit. You come back to a **big, drill-in tree** with ⭐ on the genuinely strong
nodes. You can run *embedded AI*, *edge AI*, and *ML observability* as three
independent **project tabs** simultaneously.

---

## 2. Core concepts & vocabulary

| Term | Meaning |
|---|---|
| **Project** | One autonomous exploration of a root domain. Owns a tree, a budget, a status, and a tab in the UI. |
| **Node** | A point in the exploration tree. Has a `kind` (see below), a state, and (for gap nodes) a viability score. |
| **Frontier** | The set of expandable nodes, priority-ordered. The engine always works the highest-value frontier node next (best-first search). |
| **Expansion** | Turning one node into children (domain → sub-areas → sub-segments → candidate gaps). |
| **Pressure test** | Adversarial multi-lens verification of a candidate gap that produces its viability score + a confidence. |
| **Viability score** | 0–100, assigned *after* pressure testing. Drives ⭐ starring and frontier priority. |
| **Star (⭐)** | Auto-applied to nodes above a viability threshold; also user-pinnable manually. |
| **Budget** | A cap on tokens / time / node-count for the run. The engine plans against it. |

### Node kinds (the tree's shape)

```
Domain (root)                      "embedded AI"
└─ SubArea                         "on-device inference runtimes"
   └─ Segment                      "TinyML for battery-powered sensors"
      └─ GapCandidate              "OTA model-diffing for MCU firmware"   ← hypothesized
         └─ (becomes) Gap          same node, once pressure-tested + scored
```

- **Domain / SubArea / Segment** are *structural* nodes — the decomposition.
- **GapCandidate → Gap** is the payoff node. It carries the full `Gap` object we
  already produce today (thesis, evidence, competitors, 5 scores, wedge, riskiest
  assumption, weakest link, why-now) **plus** an autonomous-only overlay:
  `viability`, `confidence`, `pressure_test`, `star`, `explored_children`.

The existing single-area synthesis engine is reused verbatim at the Segment level —
autonomous mode is a *driver* around it, not a rewrite. This is why the source
adapters and `synthesize()` were built pluggable.

---

## 3. Node lifecycle (state machine)

```
 queued ─▶ expanding ─▶ children_ready ─┐
   │                                    ├─▶ (structural nodes rest here)
   │                                    │
   └─(gap candidate)─▶ synthesizing ─▶ pressure_testing ─▶ scored ─▶ [starred?]
                                                              │
                                          pruned ◀────────────┘ (below cutoff)
```

Every transition is an event on the project's stream (§7), so the UI tree animates
live. Nodes never block the whole run: a failed expansion marks the node `errored`
with a reason and the engine moves on (same degrade-don't-crash contract as today).

---

## 4. The expansion engine (best-first, budget-aware)

The engine is a loop over a **priority frontier**, not a blind breadth-first crawl —
that's what makes "come back to the good stuff" work instead of "come back to 4000
mediocre nodes."

```
while not done(project):
    if not usage.allows_work():          # §6 — throttle / pause
        await usage.backoff()
        continue
    node = frontier.pop_highest()         # best-first
    if node is None:                      # frontier empty
        node = completeness_critic()      # §4.3 — find what's missing, or stop
        if node is None: break
    children = await expand(node)         # decompose OR synthesize+pressure-test
    frontier.push_all(prioritize(children))
    persist(project)                      # resumable after every step
```

### 4.1 Frontier priority (what to explore next)

A node's priority blends:
- **Parent promise** — a segment under a high-scoring branch is worth more.
- **Novelty / uncrowdedness** — reward branches the tree hasn't covered.
- **Signal density** — segments where Reddit+arXiv+FRED already corroborate rank up.
- **Depth decay** — deeper nodes cost priority, so the tree widens before it tunnels.
- **User boosts** — a node the user pins/stars in the UI jumps the queue.

Priority is a cheap heuristic (no LLM) recomputed on push; a periodic LLM
"triage" pass (§4.3) re-scores the top of the frontier when budget is ample.

### 4.2 Expansion strategies per kind

- **Domain/SubArea/Segment** → an LLM decomposition call: "break this into 4–8
  distinct, non-overlapping sub-areas a founder could specialize in; for each give a
  one-line why-it-might-be-a-gap and 3 query keywords." Cheap, wide.
- **Segment → GapCandidates** → reuse today's `scope → ingest → extract →
  synthesize` pipeline. Each returned gap becomes a GapCandidate node.
- **GapCandidate → Gap** → the pressure test (§5), then score.

### 4.3 Never silently stop

When the frontier drains, a **completeness critic** runs: *"what's missing — an
unexplored modality, a sub-area we skipped, a high-score gap we never pressure-tested
twice?"* It either injects new frontier nodes or declares the run genuinely
**exhausted**. Stopping conditions are explicit and shown in the UI: `budget_spent`,
`exhausted`, `paused_by_user`, `usage_paused`, `time_limit`.

---

## 5. Pressure testing → viability score

This is the "pressure tested" the user asked for. A GapCandidate is attacked from
several independent lenses (the adversarial-verify pattern), each trying to *kill*
the gap, not confirm it:

| Lens | Question it tries to prove "yes" to |
|---|---|
| **Empty-for-a-reason** | Is this white space empty because it's a trap (regulatory, no WTP, structurally unserviceable)? |
| **Incumbent counter-move** | Could an incumbent close this in a weekend if it mattered? |
| **Demand mirage** | Is the "demand" just loud complaints with no willingness to pay? |
| **Why-now fragility** | Does the enabling shift actually exist, or is it hand-waved? |
| **Moat / defensibility** | If it works, can anyone copy it instantly? |

Each lens returns `{verdict: survives|kills|weakens, argument, evidence_delta}`.
Lenses may call the same `search_*` corroboration tools to pull fresh evidence.

**Viability score (0–100)** = a weighted combination of: the 5 base scores, how many
lenses the gap *survived*, penalty for any `kills` verdict, and evidence
corroboration count. **Confidence** reflects how much live (vs mock) evidence and how
many lenses ran. Both are shown on the node; ⭐ is applied when `viability ≥ threshold`
(default 75) **and** `confidence ≥ medium`. The threshold is a project setting.

> Depth is budget-scaled: with headroom, run all 5 lenses ×N independent samples and
> majority-vote; when curbing usage, run the 2 cheapest lenses once. The score
> records how hard it was tested (`test_rigor`) so a lightly-tested 80 is visibly
> distinct from a battle-tested 80.

---

## 6. Usage-aware orchestration

The engine treats your Claude subscription as a metered resource and self-governs so
it can run for hours without you babysitting it.

### 6.1 What it observes
- **Rate-limit signals** from the Agent SDK / API responses (429s, `retry-after`,
  remaining-request headers) — the authoritative live signal.
- **Rolling token spend** it meters itself (per call), against the project budget and
  an optional **daily cap**.
- **Time-of-day windows** (optional): e.g. "only sprint 1am–7am."
- **Backoff memory**: consecutive limit hits widen the backoff (exponential).

### 6.2 The policy (maximize vs curb)
```
headroom = f(remaining_budget, recent_429_rate, daily_cap_left, window)
if headroom == "ample":   concurrency↑ (up to cap), full pressure-test rigor
if headroom == "tight":   concurrency↓ to 1, cheapest lenses, structural-only passes
if headroom == "none":    pause, schedule wake-up, surface "usage-paused" in UI
```
Concurrency is a global semaphore shared across **all** project tabs, so three
autonomous runs cooperate instead of stampeding the limit. A visible **usage meter**
per project and a global one show spend, rate, and current mode ("sprinting" /
"curbing" / "paused — resumes ~3:10am").

### 6.3 Controls the user gets
- Global + per-project **budget** (tokens or $-equivalent) and **daily cap**.
- A **pace dial**: `eco` (subscription-safe, slow) · `balanced` · `sprint`.
- Pause/resume per project and globally; runs are **resumable** (§8) so pausing is free.

---

## 7. Backend architecture

```
FastAPI (existing)
├─ /api/projects            CRUD for autonomous projects (tabs)
├─ /api/projects/{id}/tree  full tree snapshot (paged/lazy for big trees)
├─ /api/projects/{id}/events  SSE/WebSocket live node events
├─ /api/projects/{id}/control  pause | resume | set-budget | set-pace | reprioritize
└─ existing /api/analyze etc. (single-area mode, unchanged)

ExplorerService (new)  — one asyncio task per running project
├─ Frontier (priority queue)          §4.1
├─ ExpansionEngine (reuses pipeline)  §4.2
├─ PressureTester (lenses → score)    §5
├─ UsageGovernor (shared semaphore)   §6
└─ TreeStore (persistence)            §8
```

- **One worker task per project**, all sharing the `UsageGovernor` semaphore.
- **Event-sourced tree**: every state transition is appended to an event log and
  folded into the tree; the SSE stream is just that log tailed to the client. This
  gives live UI updates, resumability, and an audit trail for free.
- Runs **in-process** for v1 (asyncio tasks survive as long as the server does). A
  later phase can move workers to a separate process / durable queue (Celery/RQ) for
  crash-survival, but the event log already makes that a drop-in.

---

## 8. Persistence & resumability

- Tree + event log persisted to SQLite (extends the existing cache DB) keyed by
  `project_id`. Node payloads (gaps, pressure tests) stored as JSON.
- The engine `persist()`s after every frontier step, so a server restart resumes each
  project from its last committed frontier — nothing re-computes that already
  completed (same principle as the build workflow's resume).
- Ingest results reuse the existing `ingest:*` cache, so re-visiting a nearby segment
  is cheap and doesn't re-hit sources.

---

## 9. Data model (new schemas, additive)

```python
class NodeKind(str, Enum):
    DOMAIN = "domain"; SUBAREA = "subarea"; SEGMENT = "segment"
    GAP_CANDIDATE = "gap_candidate"; GAP = "gap"

class NodeState(str, Enum):
    QUEUED="queued"; EXPANDING="expanding"; CHILDREN_READY="children_ready"
    SYNTHESIZING="synthesizing"; PRESSURE_TESTING="pressure_testing"
    SCORED="scored"; PRUNED="pruned"; ERRORED="errored"

class LensVerdict(BaseModel):
    lens: str; verdict: Literal["survives","weakens","kills"]
    argument: str; evidence: list[Evidence] = []

class PressureTest(BaseModel):
    lenses: list[LensVerdict]
    survived: int; killed: int
    test_rigor: Literal["light","standard","deep"]

class Node(BaseModel):
    id: str; parent_id: str | None; project_id: str
    kind: NodeKind; state: NodeState
    title: str; rationale: str = ""           # why this branch might matter
    keywords: list[str] = []
    priority: float = 0.0                       # frontier ordering
    gap: Gap | None = None                       # reuse today's Gap object
    viability: int | None = None                 # 0..100, post-pressure-test
    confidence: Literal["low","medium","high"] | None = None
    pressure_test: PressureTest | None = None
    star: bool = False
    depth: int = 0
    created_at: datetime; updated_at: datetime

class ProjectStatus(str, Enum):
    RUNNING="running"; PAUSED="paused"; USAGE_PAUSED="usage_paused"
    EXHAUSTED="exhausted"; BUDGET_SPENT="budget_spent"; ERRORED="errored"

class Budget(BaseModel):
    max_tokens: int | None = None; daily_cap_tokens: int | None = None
    max_nodes: int | None = 500; time_limit_minutes: int | None = None
    pace: Literal["eco","balanced","sprint"] = "balanced"
    star_threshold: int = 75

class Project(BaseModel):
    id: str; domain: str; sub_segments: list[str] = []
    model: str = "claude-opus-4-8"
    status: ProjectStatus; budget: Budget
    created_at: datetime
    stats: ProjectStats                          # nodes, gaps, stars, tokens_spent, mode
```

---

## 10. Frontend UX

### 10.1 Project tabs
A tab bar across the top: `⊕ New exploration` plus one tab per project
(`embedded AI · 42 gaps · 6★`, `edge AI · running…`, `ML observability · paused`).
Each tab shows a tiny live status dot (sprinting / curbing / paused) and a spend bar.
Tabs persist across reloads (projects are server-side).

### 10.2 The tree view (the centerpiece)
- **Left:** an interactive, collapsible **tree** (indented rows or a real node-link
  graph — see open decisions). Structural nodes are neutral; gap nodes show a compact
  **viability chip** (color from the neutral→vermillion ramp) and a ⭐ when starred.
  Live nodes pulse (expanding/pressure-testing). A ⭐ filter and a "viability ≥ N"
  slider prune the view. Sort branches by best-descendant viability so the strong
  paths float up.
- **Right:** the **node inspector** — for gaps it's the exact drawer we already built
  (thesis, why-now, wedge, riskiest assumption, weakest link, competitors, evidence)
  **plus** the pressure-test panel: each lens with its survives/kills verdict and
  argument, the viability score, confidence, and `test_rigor`.
- **Drill-in:** click any node to focus the subtree (breadcrumb back out). This is the
  "go in" the user asked for.

### 10.3 Run controls (per tab + global)
Pause/Resume · pace dial (eco/balanced/sprint) · budget + daily-cap editor · model
picker (reuses today's component) · star-threshold slider · a live **usage meter**
(tokens spent, rate, current mode, next-resume time). A global bar shows combined
spend across all tabs and the shared concurrency mode.

### 10.4 "Come back later" surface
A per-project **digest**: top N starred gaps, newest high-viability finds since you
last looked, and what the explorer is doing right now. This is the thing you actually
open when you return.

---

## 11. Cost, safety, honesty

- **No fabricated evidence** carries over from today: every node's evidence traces to
  a real signal/tool result; mock-sourced nodes are labeled and capped in confidence.
- **Hard budget ceilings** stop the run; the UI never implies "done/exhaustive" when a
  cap truncated it — it says which stop condition fired.
- **Dedup**: a content-hash on (segment, thesis) prevents the tree re-discovering the
  same gap on parallel branches; duplicates link to the canonical node.
- **Determinism where it matters**: frontier priority + dedup are pure code; only
  decomposition, synthesis, and pressure-testing use the LLM.

---

## 12. Phased build plan

1. **P1 — Data + engine core.** Node/Project schemas, TreeStore (SQLite + event log),
   the frontier loop expanding Domain→SubArea→Segment→GapCandidate reusing today's
   pipeline. No pressure test yet; viability = base composite. CLI-drivable.
2. **P2 — Pressure testing + scoring.** Lenses, viability, confidence, ⭐. Budget +
   `max_nodes` stop conditions.
3. **P3 — Live API + tree UI.** SSE event stream, `/projects` CRUD, the tree view +
   node inspector + one running project. Pause/resume.
4. **P4 — Usage governor + multi-project tabs.** Shared concurrency semaphore, pace
   dial, usage meter, daily cap, several tabs at once, per-project digest.
5. **P5 — Resilience.** Out-of-process durable workers, crash-resume, completeness
   critic tuning, richer prioritization triage.

---

## 13. Open decisions (need your call before P1)

1. **Tree visualization:** indented collapsible rows (fast, scales to thousands of
   nodes, easy) **vs** a zoomable node-link graph (prettier, more "giant tree" feel,
   heavier). Recommendation: ship rows in P3, add graph view in P4.
2. **Usage signal source:** rely on live rate-limit/`retry-after` responses + our own
   token metering (works today, no extra access) **vs** wiring a real usage/limits API
   if one is available to your subscription. Recommendation: start with the former.
3. **Autonomy ceiling:** should a run be allowed to spend real budget unattended up to
   the cap, or pause for a one-tap "keep going?" confirmation at budget milestones
   (e.g. every 100k tokens)? Recommendation: milestone check-ins, off by default.
4. **Model policy per stage:** cheap model (Haiku) for decomposition + a strong model
   (Opus) only for pressure-testing, to stretch the subscription — or one model
   throughout? Recommendation: mixed, Haiku-decompose / Opus-pressure-test.
```
