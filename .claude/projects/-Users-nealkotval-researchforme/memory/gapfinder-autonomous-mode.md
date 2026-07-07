---
name: gapfinder-autonomous-mode
description: Autonomous exploration mode feature for Market Gap Finder — design & decisions
metadata:
  type: project
---

**Autonomous Exploration Mode** (spec: `SPEC-AUTONOMOUS.md`) extends Market Gap Finder
from single-area request/response into a long-running, self-directed explorer.

Concept: spawn a **Project** per domain (own tab) that runs a best-first exploration
**tree** (Domain → SubArea → Segment → GapCandidate → Gap), reusing the existing
ingest→synthesize pipeline at the Segment level. Candidate gaps are **pressure-tested**
by adversarial "kill it" lenses (empty-for-a-reason, incumbent counter-move, demand
mirage, why-now fragility, moat) → **viability score 0–100 + confidence + test_rigor**;
high+corroborated ones get a **⭐**. Event-sourced tree in SQLite → live SSE + resumable.
A shared **usage governor** meters token spend + rate-limit signals and shifts between
sprint/curb/pause so multiple tabs cooperate on the Claude subscription.

**User decisions (locked 2026-07-06):**
- Tree UI: **indented collapsible rows first**, node-link graph later.
- Autonomy: **milestone check-ins** (pause every ~100k tokens for one-tap continue; opt-in).
- Model policy: **mixed — Haiku for decomposition, Opus for pressure-testing** (stretch subscription).
- Build **Phase 1 now**, via a **Workflow** (user asked to orchestrate the build).

Built on branch `build/market-gap-finder`. Backend module: `backend/app/autonomous/`.
See [[gapfinder-project]] and [[gapfinder-design-direction]].
