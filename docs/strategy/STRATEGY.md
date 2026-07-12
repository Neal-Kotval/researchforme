# Gap Finder Strategy — From Grading Gaps to Finding Spaces

_2026-07-12. Synthesizes [idea-generation-research.md](idea-generation-research.md)
(verified external research), [feature-expansion-catalog.md](feature-expansion-catalog.md)
(18 grounded feature proposals), and the 2026-07-12 full app audit.
Owner's purpose statement, verbatim: **"an automated engine to help me look for ideas
for startups (whole companies) that I can take and look into. thus it should be more a
program about finding spaces."**_

## 1. Thesis

The engine today is a **grader**: the user supplies a domain, and it decomposes,
mines, red-teams, and scores what it finds inside that box — well. The purpose calls
for a **finder**: the engine should supply the domains, compare spaces against each
other and against the founder, and get better at both over time. The research says
this is the right ambition and tells us how the winners do it:

1. **Signal-mining is the proven industrial pattern** (SignalFire/Beacon, Specter,
   Harmonic). Our 5-adapter mining core is a miniature Beacon — the moat is adding the
   signal classes they use that we don't (job postings, hiring velocity, patents, web
   traffic proxies) and running them *continuously*, not per-request.
2. **LLM ideation's known failure is diversity, not quality** — and the fixes are
   prompting-level and cheap (CoT, ordinary-persona sampling, Verbalized Sampling).
   Our decomposition step is exactly where mode collapse bites (4–8 "MECE" children
   converge on consensus categories) and exactly where the fixes apply.
3. **The best human heuristics are biases-to-counteract, not scores-to-compute.**
   Schlep blindness and the Bessemer eBay pass both say: the engine's most valuable
   output is the idea that *sounds* wrong or tedious but has strong demand signals.
   An engine that only surfaces plausible-sounding ideas replicates the blindness it
   should exploit.
4. **Novelty is not the objective.** The ideation-execution gap warns that
   judged-novel ideas underperform when executed. Our pressure-test/viability layer is
   therefore the right architecture — the strategy is to *keep* adversarial grading
   as the gate while moving discovery upstream of it.
5. **"Noticed, not invented" translates to: ground every idea in a signal.** PG's
   organic-ideas doctrine is, for an engine, a grounding requirement: ideas must trace
   to observed pain (mined complaints, hiring, regulation), never to zero-shot
   brainstorming. We already enforce evidence-carrying gaps; Scout mode must inherit it.

## 2. Design principles (each traceable to a verified finding)

| # | Principle | Derived from |
|---|-----------|--------------|
| P1 | Every idea traces to a mined signal; no ungrounded brainstorming | PG organic ideas (F4) |
| P2 | Fight mode collapse structurally: persona-diverse decomposition, CoT, verbalized-sampling-style N-candidate prompts | F2, F3 |
| P3 | Never kill on surface plausibility alone; keep an explicit schlep/contrarian channel that *boosts* tedious-or-silly-sounding ideas with strong signals | Schlep blindness (F4), eBay pass (F5) |
| P4 | Grade with execution in mind: viability/pressure-testing stays the gate; novelty is an input, not the objective | Ideation-execution gap (F1) |
| P5 | Painkiller check: does the evidence show people *already* feeling the pain unprompted (complaints, workarounds, hiring), vs. needing convincing? | Vitamin/painkiller diagnostic (F7) |
| P6 | Widen signal classes toward the Beacon set: job postings, app-store reviews, regulatory changes, funding rounds, search-trend derivatives | F6 |
| P7 | Continuous > episodic: watch, diff, and alert; a space's *timing* is most of its value | F6 + why-now doctrine |
| P8 | The engine must fit the founder, not just the market: fit scoring + preference learning from pass/interested feedback | Founder-market fit (qualitative; open question 3) |
| P9 | Distrust vendor-style self-grading: our own heuristic-fallback lens verdicts must display as weaker than tool-corroborated verdicts | Refuted marketing claims; audit trust finding |

## 3. Where the engine already delivers

Mining 5 real sources with evidence-carrying gaps (P1 ✓). Adversarial pressure-testing
with live corroboration tools + `just_a_feature` lens (P4 partially ✓). Steering
context threaded through every stage (P8 seed ✓). Event-sourced tree + SSE (the
substrate P7 needs ✓). Usage governor (makes continuous operation affordable ✓).

## 4. The plan

Phases ordered by leverage; S/M/L per the catalog's effort estimates. Sensors ship
before their consumers.

### Phase 0 — Prompt-level upgrades (days; near-zero risk)
1. **Diversity pack in decomposition** (P2): CoT scaffold; assign each child-generation
   pass a distinct *ordinary* persona (a bookkeeper, a plant manager, a school
   administrator…); verbalized-sampling variant — ask for N candidate sub-areas *with
   probabilities*, keep the tail, not just the head. Seam: decompose prompt template.
2. **Schlep/contrarian channel** (P3): decomposition always emits 1–2 children flagged
   `contrarian` (mainstream says dead/boring/too tedious) with a priority bump; a
   pressure-test note when a kill is plausibility-only. Seam: prompt + `node_priority`.
3. **Painkiller lens** (P5): new pressure lens — "does the evidence show unprompted
   pain (complaints, workarounds, hiring) or would a founder have to convince
   prospects?" Kill/weaken on vitamin-shaped evidence. Seam: `pressure.py` lens list.
4. **Trust rendering** (P9): heuristic-fallback lens verdicts and light-rigor scores
   display visually degraded (audit's top trust finding).

### Phase 1 — Become a finder (the purpose phase)
5. **Scout mode** (M): domainless runs — cheap wide pass over the adapters for
   trending complaint/velocity clusters → 10–15 founder-filtered candidate domains as
   one-click seeds (P1: each seed carries its triggering signals). Seam: root-node
   factory before `service.start`; sanctioned deferred item (B).
6. **Founder-fit score** (S): second 0–100 per gap scored against the steering block;
   portfolio answer = fit × viability, not viability alone (P8).
7. **Cross-run whitespace** (M): embed + cluster all explored branch titles; feed
   "conspicuously unexplored adjacent territory" into the stubbed
   `_completeness_check` and into Scout suggestions.

### Phase 2 — Sensors and memory
8. **Interested/Pass triage with reason taxonomy** (S) — the feedback sensor.
9. **Anti-portfolio graveyard** (S): searchable killed/passed archive; injected into
   decomposition as "already rejected — don't re-propose; flag if the kill reason
   expired" (P3: kills whose reason expired are themselves a discovery channel).
10. **Look-into checklist on stars** (S): Found → Interviewing → Smoke-testing →
    Verdict; makes real-world outcomes machine-readable.

### Phase 3 — Continuous operation (P7)
11. **Space Watch** (M): watch any node/killed gap; scheduled source-only re-fetch
    diffs vs the `ingest:*` cache; alert on material shifts (complaints ×3, fast-star
    repo, regulatory match). Watches the graveyard for expired kills.
12. **Scheduled re-runs + tree diffing** (M): the diff of two trees is a velocity
    field over sub-spaces — the purest "finding spaces" information the system can emit.
13. **New adapters toward the Beacon set** (P6, M each): job postings (HN who's-hiring
    via existing Algolia plumbing + RemoteOK/WWR RSS; hiring = salary-backed demand),
    app-store 1–3★ review clustering across a category's top apps (shared complaint =
    structural gap), regulatory/policy changes (Federal Register API — the most
    reliable why-now generator), funding-round extraction from existing newsletter
    feeds (seed = validation; mega-round = crowding evidence for the kill-lens).
14. **Idle-headroom scavenger** (S): governor "ample" + no active run → deepen
    shallowest ⭐ branch, upgrade light tests to deep, or run a Scout pass.

### Phase 4 — Synthesis & hand-off
15. **Portfolio 2×2 across runs** (M): every space from every project on fit ×
    viability; the top-right quadrant is the answer to "where should I look?"
16. **Research Pack** (M): one click on a star → interview scripts quoting the actual
    mined complaints, landing-page smoke-test plan, bottoms-up sizing sketch,
    riskiest-assumption → cheapest-falsification map (P4: execution-oriented output).
17. **Preference distillation** (M): every N triage events, distill durable
    preferences into a reviewable learned-preferences section of the steering block +
    keyword boosts in `node_priority` (P8: the engine gets better at finding spaces
    *for this founder*).
18. **End-of-run digest** (S): 3 strongest spaces, the pattern across kills, 2–3
    re-steering questions feeding the next run's intake.

## 5. Measuring the engine itself

- **Diversity**: embedding dispersion of sibling sub-areas and of gap theses per run
  (the mode-collapse literature's own metric); should rise after Phase 0.
- **Calibration**: track lens rigor + corroboration per verdict; % of viability scores
  backed by tool-corroborated (not fallback) lenses.
- **Ground truth loop**: triage decisions and look-into verdicts vs the engine's
  fit/viability — the only real score, available once Phase 2 sensors exist.
- **Freshness**: median age of evidence behind starred ideas; Space Watch should push
  it down.

## 6. What NOT to build

- Zero-shot "idea generator" modes with no signal grounding (violates P1; the
  ideation-execution gap says judged-novel ungrounded ideas are the trap).
- Trusting any single plausibility score as a kill switch (P3).
- Vendor-style headline metrics for ourselves (55M-companies syndrome): the usage bar
  and stats must stay measured, not estimated-and-rounded-up — includes replacing
  estimated token metering with real counts when the client can surface them.
- Auto-resume-on-boot or any unattended spend the user didn't schedule (kept
  deliberately out of the restart-reconciliation fix).

## 7. Known open risks

- Diversity interventions are validated for zero-shot ideation; their composition with
  retrieval-grounded pipelines is untested (research open question 2) — measure §5
  diversity before/after Phase 0 to check transfer.
- The mode-collapse paper is a single-group preprint; treat P2 gains as hypotheses to
  confirm on our own diversity metric.
- No published efficacy data for the VC signal platforms we're emulating — copy the
  signal *classes*, not the marketing claims.
