# PRODUCT.md — Market Gap Finder

## What it is
A research tool that surfaces enterable market gaps for founders/PMs/indie builders.
It ingests Reddit (demand/pain), arXiv (research momentum), and FRED (economic
trends), then has Claude synthesize a short, ranked list of "whitespace"
opportunities — each with grounded evidence, top-5 competitors, five 1–5 scores, a
wedge, and the riskiest assumption to validate.

## Register
**Product.** Design serves the task. The user is analyzing, comparing, and deciding —
not being marketed to. Earned familiarity over novelty; the interface should
disappear into the work. Reference bar: Linear, Stripe dashboard, Notion.

## Primary users & job
A builder enters a market area ("personal finance for freelancers"). The job: read
the ranked opportunities fast, understand *why* each is a gap, and judge which is
worth building. Speed of comprehension and trust in the evidence are everything.

## Core surfaces
- **Area input** (hero, empty state) — area + optional sub-segments + model picker.
- **Opportunity map** — the 2×2 hero: Demand (Y) × Competitive openness (X), bubble
  size = feasibility, color = trend tailwind. The winner is unmistakable.
- **Ranked table** — sortable by rank/composite and each of the five scores.
- **Gap detail drawer** — thesis, why-now, wedge, riskiest assumption, weakest link,
  top-5 competitors table, evidence with clickable source links.
- **Weight controls** — five sliders that re-rank live without re-fetching.
- **Source status strip** — which sources fired (live/mock/unavailable/empty) + freshness.

## Voice & tone
Plain, precise, analytical. No hype, no marketing buzzwords. Labels are verb+object.
Gaps are framed as hypotheses, not advice. Honesty over optimism: the UI shows when
data is mock, when a source failed, and when a gap is "empty for a reason".

## Brand
**CNC / blueprint** aesthetic. Single accent: **steel/blueprint blue `#3568c4`**.
Everything else is cool-neutral machined greys (cool off-white canvas, white
surfaces, near-black ink, hairline slate borders), squared/sharp radii. The blue is
reserved — primary actions, selection, links, the live state, and the headline
metric. Data reads as a **grey→blue→navy** density ramp (denser = stronger), never a
rainbow. One muted red is kept for genuine danger/heavy-usage/delete only.
Typography is technical: **Space Grotesk** (display/body) + **IBM Plex Mono** for all
numeric/metric readouts (viability, token counts, gauges, KPI values) so the UI reads
like a machine readout. See DESIGN.md / tokens.css for the full system.
