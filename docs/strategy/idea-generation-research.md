# How People Find Startup Ideas — Verified Research Report

_Deep-research run, 2026-07-12. Method: 6 search angles → 23 sources fetched → 89
falsifiable claims extracted → top 25 verified by independent 3-voter adversarial
panels (2/3 refutations kill a claim) → 19 confirmed, 6 refuted → synthesized.
106 agents (Sonnet 5), all stages completed. Companion docs: [STRATEGY.md](STRATEGY.md),
[feature-expansion-catalog.md](feature-expansion-catalog.md)._

## Executive summary

People find startup ideas through two complementary traditions. The **qualitative,
founder-centric tradition** (YC/Paul Graham) says ideas should be *noticed* by living
close to a problem rather than manufactured, with "schlep blindness" explaining why
tedious-but-valuable opportunities (Stripe) get systematically overlooked. The
**quantitative, data-driven tradition** (SignalFire's Beacon, Specter, Harmonic, and
productized personal workflows like Ideabrowser) mines GitHub, job postings, patents,
web traffic, and social signals to surface companies and predict investor interest
before it is public knowledge. Meanwhile the academic literature shows LLMs can
generate ideas judged **more novel than expert humans** in blind review (though weaker
on feasibility), but suffer a real **mode-collapse/low-diversity problem** with two
identified mechanisms — both of which have empirically validated, prompting-level
mitigations that can push LLM idea-set diversity **above** human baselines. VC
pattern-matching failures (Bessemer's eBay pass) and the vitamin/painkiller diagnostic
round out the judgment layer an automated engine must reproduce or route around.

## Confirmed findings

### 1. LLM ideas are judged more novel than expert-human ideas — but feasibility lags, and novelty ≠ execution success — HIGH
Stanford (Si, Yang, Hashimoto; ICLR 2025) ran the first statistically significant
blind head-to-head: 100+ NLP researchers wrote and blind-reviewed ideas. Novelty:
human 4.84 vs AI 5.64 vs AI+human-rerank 5.81 (p<0.05, three tests); feasibility
slightly weaker for AI. Their 2025 follow-up ("The Ideation-Execution Gap") found
human ideas score **higher once actually executed** — blind-judged novelty does not
guarantee real-world value. Verified 3-0. — [arxiv.org/abs/2409.04109](https://arxiv.org/abs/2409.04109)

### 2. LLM ideation mode-collapses, via two distinct mechanisms — HIGH
Columbia Business School (Deng, Brucks, Toubia; Feb 2026, four studies with human
comparison conditions): (a) **collective**: LLMs aggregate knowledge into one unified
distribution instead of partitioning it across distinct "individuals" the way human
populations do; (b) **individual**: a *fixation* effect where early outputs anchor and
narrow later ideation within a session (diversity-accumulation slope β=1.013 default
vs 1.363 with CoT, p<.001). This is exactly why naively asking an LLM for "many
startup ideas" yields redundant output. Verified 3-0. — [arxiv.org/pdf/2602.20408](https://arxiv.org/pdf/2602.20408)

### 3. Validated mitigations exist — and their combination beats human diversity — HIGH
Three prompting-level, training-free interventions:
- **Chain-of-thought** reduces fixation (LLM-specific; doesn't help humans).
- **Diverse "ordinary" personas** (not iconic ones like Steve Jobs) improve knowledge
  partitioning by anchoring generation in distinct semantic regions.
- **Verbalized Sampling** — ask the model to output a *probability distribution over
  multiple candidates* in one prompt instead of one best answer.
CoT + ordinary personas combined produced idea-set diversity **exceeding human
baselines**. Verified 3-0 on both mechanism and combined result. —
[arxiv.org/pdf/2602.20408](https://arxiv.org/pdf/2602.20408), [arxiv.org/html/2510.01171v1](https://arxiv.org/html/2510.01171v1)

### 4. PG/YC: ideas are noticed, not invented; schlep blindness hides the valuable ones — HIGH
"The way to get startup ideas is not to try to think of startup ideas. It's to look
for problems, preferably problems you have yourself" (organic ideas: Apple, Dropbox).
Schlep blindness: "your unconscious won't even let you see ideas that involve painful
schleps" — Stripe as the canonical example: every hacker knew payments were painful,
nobody built the fix for a decade, partly because hackers avoid ideas requiring
talking to users, negotiating, or operational pain. All four sub-claims verified 3-0
against the primary essays. — [paulgraham.com/schlep.html](https://www.paulgraham.com/schlep.html),
[startupideas essay](https://libraryofllm.com/sources/pg-how-to-get-startup-ideas)

### 5. Even elite VCs misjudge category-defining ideas on surface plausibility — HIGH
Bessemer's own anti-portfolio quotes David Cowan on eBay: "'Stamps? Coins? Comic
books? You've GOT to be kidding. No-brainer pass.'" First-party admission,
independently corroborated. Lesson for an automated engine: "the market doesn't sound
credible" is an unreliable kill filter. Verified 3-0. — [bvp.com/anti-portfolio](https://www.bvp.com/anti-portfolio)

### 6. VC sourcing has industrialized signal-mining — HIGH
- **SignalFire "Beacon"**: ~2M data sources / 500B data points across 80M companies —
  GitHub, job postings, patent filings, web traffic, app-store telemetry — explicitly
  rejecting network/intuition sourcing.
- **Specter**: markets an "investor interest" signal claiming to forecast which VCs
  will fund/acquire a startup from partner-founder engagement signals (feature is
  real; predictive accuracy NOT verified — its headline coverage numbers were refuted
  0-3 in this run).
- **Harmonic**: natural-language criteria queries over an indexed company universe
  (sector, founding date, headcount growth, founder pedigree, social traction).
Verified 3-0 as descriptions of what the products do, not of their efficacy. —
[vcbeast.com/vc-firms/signalfire](https://vcbeast.com/vc-firms/signalfire), [tryspecter.com](https://www.tryspecter.com/), [TechCrunch on Harmonic](https://techcrunch.com/2022/11/07/harmonic-helps-investors-query-the-startup-searches-of-their-wildest-dreams/)

### 7. Vitamin vs painkiller has one concrete diagnostic — MEDIUM
"If you find yourself convincing the prospect that they've got a problem you can
solve… you probably created a vitamin." Prospects must already feel the pain
unprompted. Aligns with the Mom Test principle. 2-1 vote, single blog-quality source;
treat as heuristic, not law. — [brianrhea.com](https://brianrhea.com/early-stage-startup-vitamin-or-painkiller/)

### 8. Ideabrowser is one practitioner's workflow productized — MEDIUM
Greg Isenberg's own framing: "this productizes the flow I use to build/incubate/invest."
Useful as a competitive reference (personal ideation workflow → subscription tool),
not as an independently validated methodology. Verified 3-0 on the self-description. —
[Product Hunt launch](https://www.producthunt.com/products/ideabrowser-com)

## Refuted during verification (excluded from findings)

- Specter "tracks 55M+ companies / 2.75× competitor coverage" — 0-3 (marketing claim).
- Specter's "Revenue Signals has no equivalent among competitors" — 0-3.
- Harmonic founding story + "20M companies in 2025" claims — 0-3 (secondary blog).
- A claimed PG "Well test" heuristic — 1-2 (attributed to a secondary paraphrase site,
  not the primary essay).
- Evertrace "detects founders pre-announcement" positioning — 1-2 (vendor copy).
- "Schlep Blindness introduced in the startup-ideas essay" — 1-2 (wrong essay
  attribution; it's its own essay, corrected in finding 4).

## Caveats

- Sourcing-platform findings describe **claims, not verified efficacy**; Specter's
  prediction-accuracy claim was explicitly not verified.
- arXiv 2602.20408 (mode collapse) is a Feb-2026 single-group preprint, methodologically
  rigorous but not yet peer-reviewed/replicated.
- arXiv 2409.04109 studied **NLP research ideas**, not startup ideas — transfer is
  plausible but untested, and the ideation-execution gap warns against conflating
  "judged novel" with "good."
- Vitamin/painkiller and the eBay anecdote are illustrative heuristics, not
  statistically validated predictors.

## Open questions

1. Does the novelty-vs-feasibility tradeoff (and the ideation-execution gap) replicate
   for *startup* ideas taken through pressure-testing?
2. Do diversity interventions (CoT + personas, Verbalized Sampling) compose with
   retrieval-grounded pipelines like this engine, or mainly help zero-shot brainstorming?
3. Is there empirical (non-anecdotal) evidence linking founder-market fit or "why now"
   timing to outcomes?
4. Do the VC signal platforms publish any real track-record data that could calibrate
   how much weight to give signal-mining?
