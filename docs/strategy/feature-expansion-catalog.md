# Feature Expansion Catalog — Making Gap Finder Better at *Finding Spaces*

_2026-07-12. Grounded in PRODUCT.md, SPEC-AUTONOMOUS.md, MORNING-REPORT.md,
`backend/app/autonomous/` (engine, pressure, service, intake, governor, store,
frontier priority), `backend/app/sources/` (Source ABC + registry, 5 adapters,
never-raise contract, `ingest:*` cache), and `frontend/src/components/autonomous/`.
Key seams confirmed in code: the honest-no-op `_completeness_check`,
`steering_context_block(project)` injected into every LLM stage, the event-sourced
`TreeStore` (SQLite `ap_events` + SSE replay), the cheap heuristic `_priority` on
frontier push, the global `UsageGovernor`, and the intake question generator.
Prioritized sequencing lives in [STRATEGY.md](STRATEGY.md)._

Framing: **today the user supplies the domain and the engine grades what it finds
inside it. The purpose statement says the engine should supply the domains.** The
biggest leverage is upstream of the tree root and across trees, not deeper inside
one tree.

## Theme 1 — Discovery breadth (where does the root node come from?)

### 1.1 Scout Mode ("surprise me" runs) — M
A run type with no user-supplied domain. A cheap wide pass pulls trending clusters
from the existing 5 adapters *before any tree exists* (HN front-page velocity, GitHub
star-velocity bursts, newsletter narrative convergence, Reddit complaint clusters),
asks the LLM to name 10–15 candidate domains shaped like "spaces a founder could
own," filters them against the steering block (skills, constraints, hated spaces),
and presents them as one-click seeds — or auto-starts the top 3 at Quick-scan depth.
**Why:** the literal purpose statement — flips the engine from "grade my idea of a
space" to "propose spaces I'd never have typed."
**Seam:** new root-node factory in front of `service.start`; reuses `get_sources()` +
`steering_context_block` verbatim. Also the deferred "organic space suggestion (B)"
from the morning report — already sanctioned direction.

### 1.2 Whitespace-between-runs seeding — M
When starting a new run (or on demand), embed the titles/keywords of every
SUBAREA/SEGMENT node across *all* existing projects, cluster them, and ask: "given
everything already explored, what adjacent territories are conspicuously unexplored?"
Emit those as suggested domains, and — the sharper move — wire it into
`_completeness_check` so a draining frontier is refilled with cross-run blind spots
instead of declaring "exhausted."
**Why:** the corpus of past trees is a map of covered ground; the negative space of
that map is precisely "spaces." Nobody else has this data.
**Seam:** the stubbed `_completeness_check` + `TreeStore` cross-project queries.

### 1.3 Cross-domain analogy transfer — S/M
For any high-viability gap, a one-shot prompt: "this pattern (e.g. 'scheduling +
invoicing collapsed into one tool for wedding photographers') — which 5 other
verticals have the same structure but no equivalent?" Each answer becomes a pinned
GAP_CANDIDATE in a different branch or a new-run suggestion.
**Why:** the cheapest known space-finding heuristic (vertical-SaaS pattern-matching) —
turns one found space into a family of spaces.
**Seam:** new expansion strategy in the engine's per-kind dispatch; children pushed
with the existing `pinned` priority boost.

### 1.4 Anti-consensus lens on decomposition — S
Alongside the 4–8 obvious sub-areas, always emit 1–2 "contrarian" children — sub-areas
the mainstream considers dead, solved, or too boring — flagged `contrarian: true` with
a novelty bump in `_priority`.
**Why:** best-first search converges on consensus-legible branches; durable spaces
hide in branches everyone's priors prune.
**Seam:** decomposition prompt template + one keyword in the `_priority` novelty term.

## Theme 2 — Signal quality (sources that reveal *underservedness*, not just activity)

The adapter contract makes these bounded work: implement `Source.fetch` (never raise,
return `FetchResult` with a `SourceReport`, cache under `ingest:<name>`), register it.
The design cost is each one's *signal thesis*:

### 2.1 Job-postings adapter (hiring-pain proxy) — M
HN "Who is hiring" via the existing Algolia plumbing + RemoteOK/WeWorkRemotely RSS
(keyless). Thesis: when many *non-tech* companies in a vertical hire for the same
in-house role ("we need someone to manage our X"), no product exists for X. Weight =
posting recurrence across distinct companies.
**Why:** demand with money already attached — salaries paid for a job a product should
do is the strongest underserved-space evidence missing from the current mix.

### 2.2 App-store review miner (1–3★ complaints at category level) — M
iTunes RSS review feeds are keyless per-app; aggregate 1–3★ reviews across a
category's top ~20 apps and cluster the shared complaint. A complaint every incumbent
earns is a structural gap, not a bug.
**Why:** Reddit gives "I wish X existed"; reviews give "X exists 20 times and all fail
at Y" — a different, more monetizable shape of gap.

### 2.3 Regulatory/policy-change adapter — M
Federal Register API (keyless, excellent) + EU equivalents, filtered by segment
keywords. A new rule with a compliance deadline mints a time-boxed market where every
affected business must buy something. Feeds the gap's **why-now** field directly.
**Why:** regulation is the single most reliable why-now generator; also the better
"official data" replacement for the long-planned FRED removal.

### 2.4 Funding-round radar — M
Extraction pass over the existing newsletter/TechCrunch feeds pulling
(company, round, space) triples. Dual-edged, and the extractor should say which:
seed rounds in a space = demand validation; a mega-round = crowding evidence for the
`crowded` kill-lens.
**Seam:** extraction layer over the newsletters adapter + the corroboration seam in
`pressure.py`.

### 2.5 Funded-company outcomes adapter (YC directory + funding data) — M
Queried at pressure-test time: "how many funded companies attacked this segment, and
what happened to them?" Funded corpses = the strongest `empty_for_a_reason` evidence;
thriving incumbents = crowding evidence for the `crowded` lens. Also supplies
reference-class few-shots for the pressure prompt ("sounded dumb, won" / "sounded
great, died"). Explicitly NOT training data — outcome labels confound idea with team,
arrive 5–10 years late, and pivots make idea→outcome labels wrong. Retrieval +
eval-set only (see STRATEGY.md §5 market-validation backtest).

### 2.6 Dead-startup post-mortem corpus — S/M
Failure autopsies (CB Insights-style post-mortems, shutdown lists) keyed by segment,
feeding the anti-portfolio graveyard (4.3) and Space Watch (3.1): a documented kill
reason that has since expired (missing API now exists, regulation changed) is a
first-class discovery signal.

### 2.7 Search-demand trend curves — S/M
Google Trends via pytrends (keyless, rate-limit-fragile — survivable under the
governor + mock-fallback contract). Attach a 12-month slope to segment keywords;
slope feeds `_priority` signal density and the trend score.
**Why:** the one source class that gives a *derivative*, not a level. Rising-but-small
search demand is the canonical early-space signature.

## Theme 3 — Continuous / ambient operation

### 3.1 Watchlists + signal-shift alerts (Space Watch) — M
"Watch" any node (or killed gap). A scheduled cheap re-fetch (sources only, no LLM
tree work) diffs new items against the cached `ingest:*` snapshot; a material shift —
complaint volume ×3, a new fast-star repo, a regulatory match — raises an alert: "the
space you passed on in May just changed: [evidence]."
**Why:** spaces aren't static; the *timing* of a space is most of its value. Converts
a one-shot finder into a standing radar.
**Seam:** `ingest:*` cache keyed by (area, query_terms) makes diffing nearly free;
alerts are new `EventType`s through the existing event store + SSE.

### 3.2 Scheduled re-runs with tree diffing — M
"Re-explore monthly" per project: replay the same domain + steering, then diff the
event-sourced snapshots — nodes new since last run, viability deltas, gaps that
flipped killed→viable (why-now arrived) or viable→killed (someone shipped it).
**Why:** a single tree is a photo; the diff of two trees is a *velocity field* showing
which sub-spaces are opening.

### 3.3 Idle-headroom scavenger — S
When the governor reports "ample" and no run is active, spend the surplus: deepen the
shallowest ⭐ branch, upgrade light-rigor pressure tests to deep, or run a Scout pass.
**Seam:** `UsageGovernor` headroom signal + the frontier — push work instead of merely
gating it.

## Theme 4 — Synthesis & comparison across runs

### 4.1 Portfolio view: the cross-run opportunity map — M
A home-screen 2×2 where each bubble is a *space* (top gap or branch rollup) from
**every** project, with a founder-fit ring (4.2). The missing "so across everything,
where should I look?" surface.

### 4.2 Founder-fit score (steering-aware, orthogonal to viability) — S
A second 0–100 per gap: viability says "is this a real space," fit says "is it a real
space *for you*" — scored against the steering block's advantages/constraints/skills
in one cheap LLM pass at scoring time. Portfolio plots fit × viability; the top-right
quadrant is the answer.
**Why:** "ideas *I* can take" — a 90-viability HIPAA-infrastructure play is a 15-fit
for a solo indie founder. Without fit, the ranking optimizes the wrong objective.

### 4.3 Anti-portfolio ledger — S
A searchable archive of every killed/passed gap with kill-lens verdict and the user's
pass-reason (6.1), clustered by kill cause. Browsable "graveyard" page + injected into
decomposition as "SPACES ALREADY REJECTED (don't re-propose; flag if the kill reason
has expired)."
**Why:** prevents re-finding dead spaces as runs accumulate; expired kill reasons are
themselves a discovery channel (watchable via 3.1). The deferred "searchable history (G)."

### 4.4 End-of-run digest with steering questions — S
On finish: the 3 strongest spaces, the pattern across kills ("everything under X died
on `just_a_feature` — the space is features, not companies"), the most-corroborated
branch, and 2–3 questions whose answers would re-steer a follow-up run (feeds
`intake.py`). Deferred item (D).

## Theme 5 — Validation hand-off

### 5.1 Research Pack generator for ⭐ spaces — M
One click on a starred gap emits: 5 interview scripts targeted at the personas in the
evidence (quoting the actual mined complaints as openers), a landing-page smoke-test
plan (headline = thesis, audience = where the evidence came from), a bottoms-up sizing
sketch from the demand signals, and a riskiest-assumption → cheapest-falsification map.
Markdown, downloadable.
**Why:** the purpose ends "…that I can take and look into" — without this every star
dead-ends in a copy-paste to another tool. Deferred item (E) sharpened into a
validation kit.
**Seam:** one strong-model prompt over the already-rich Gap object + a template.

### 5.2 "Look-into" checklist state on starred gaps — S
Minimal per-star kanban: Found → Interviewing → Smoke-testing → Verdict(build/pass),
with a free-text learnings field. The verdict + learnings feed 6.1.
**Why:** cheap, but it's the *sensor* that makes real-world validation outcomes
machine-readable.

## Theme 6 — Learning loop

### 6.1 Interested / Pass triage with reason capture — S
Two-key triage on every scored gap (I/P) with a one-tap reason (too crowded / not my
skills / too small / boring / no distribution / other+text). Persisted as events.
**Why:** the only feedback channel today is ⭐ (positive-only, no why). Structured
pass-reasons are the raw material for all downstream personalization.

### 6.2 Preference distillation into steering — M
Every N triage events, a cheap pass distills durable preferences ("passes on anything
B2C; consistently interested in boring-vertical workflow tools with regulatory
why-nows") into a reviewable, editable **learned-preferences** section appended to
`steering_context_block` — plus learned keyword boosts/penalties in `_priority`.
**Why:** makes it an engine that *gets better at finding spaces for this founder*.
Reviewable-before-applied avoids preference drift.

## Top 5 by leverage (purpose impact ÷ effort)

| # | Idea | Effort | Why it wins |
|---|------|--------|-------------|
| 1 | 1.1 Scout Mode | M | Directly delivers the purpose statement; reuses adapters + steering wholesale; changes what a run *is*. |
| 2 | 4.2 Founder-fit score | S | One prompt + one field re-aims the entire ranking at "spaces *I* can take"; prerequisite for the portfolio view. |
| 3 | 6.1 + 4.3 Triage + anti-portfolio | S+S | Two small sensors that compound forever; earliest-shipped wins most. |
| 4 | 3.1 Space Watch | M | Standing radar; timing is the highest-value information and the `ingest:*` diff makes it cheap. |
| 5 | 5.1 Research Pack | M | Turns the product's output from a screen into an artifact you act on the same day. |

**Sequencing:** ship the S-sized sensors (6.1, 5.2) before their M-sized consumers
(6.2, outcome-aware 4.4). And 1.2's `_completeness_check` refill is the tactically
satisfying one — the codebase has an explicitly stubbed hook whose docstring admits
"the honest answer here is nothing"; cross-run whitespace is the something it was
waiting for.
