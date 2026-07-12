# UI Evolution — Design Direction

_The binding design memo for the 2026-07 UI evolution. Extends DESIGN.md/tokens.css
(Langfuse-matched: warm paper `--bg #edede8`, warm near-black ink, BLACK primary
buttons, BLUE accent `#1863dc`, sharp radii, Inter). Nothing here replaces that
foundation — this adds the layers it lacks: semantic color, trust encoding,
instructional voice, and a dashboard that answers questions._

## 1. The two-hue semantic system — "blue is the market, vermillion is you"

Color must carry meaning, never decoration:

- **Blue (`--accent` family)** = the market: viability, demand density, market
  signals, links, selection. The existing grey→blue→navy ramp stays the only
  data ramp for market strength.
- **Vermillion (NEW `--fit` family: `--fit: #d24317`, `--fit-strong: #b03410`,
  `--fit-tint: #fbe9e2`)** = the founder: founder-fit score, steering-derived
  UI (advantages matched, constraints violated), "for you" markers. Used
  sparingly — fit chips, fit ring on portfolio bubbles, steering highlights.
- **Black** stays actions. **Muted red `--danger`** stays destruction/heavy-usage
  only. Never a rainbow; two meanings, two hues.

## 2. Trust encoding — confidence is visible, not a label

A number's visual weight must match how much it deserves belief:

- **High confidence + tool-corroborated**: solid heavy numeral, solid border,
  full-saturation ramp color. Earned.
- **Medium**: outline treatment, normal weight.
- **Low / uncorroborated / light rigor**: dashed border, desaturated, smaller,
  with an explicit inline word ("unverified"). A light-rigor 93 must *look*
  weaker than a deep-rigor 61.
- **Heuristic-fallback lens verdicts**: grey + dashed + "not evaluated —
  heuristic fallback" wording. Never the same pill as a real red-team verdict.
- **Mock/canned provenance**: amber `--mock: #9a6b00` badge ("fixture data") on
  any evidence item with `live: false` and on gaps tagged `fixture`. Amber is
  reserved for provenance warnings only.

## 3. Instructional voice — the UI teaches the machine

Every module answers "what am I looking at, and what should I do" in one line:

- Module subtitles: quiet one-liners under headings ("Ideas that survived the
  red team, ranked by how much they deserve your attention").
- Empty states instruct with the next action, never apologize ("No spaces
  starred yet. Runs star ideas that score ≥75 with earned confidence — start
  one, or lower the star threshold.").
- Key metrics get hover/inline definitions ("Viability — market strength after
  adversarial testing. Not a promise; a prioritized hypothesis.").
- First-run dashboard renders the pipeline as a teaching diagram:
  **Brief → Explore → Pressure-test → Compare → Take away**, each step one line.
- Voice: plain, verb+object, no hype. Numbers framed as hypotheses.

## 4. Dashboard IA — from filing cabinet to instrument

Order of the home screen (the question each block answers):

1. **Worth your attention** (hero) — top ideas across ALL projects by
   fit×viability, with trust-encoded scores and "why now" one-liners.
   Answers: *what did the machine find?*
2. **Active exploration** — running/paused projects with live counters and a
   spend sparkline. Answers: *what is it doing right now?*
3. **Suggested spaces** (Scout) — domains the engine proposes, each with its
   triggering signals and a one-click launch. Answers: *where should I look next?*
4. **Recent movers** — viability deltas, new stars, expired kill reasons (as
   these features land). Answers: *what changed?*
5. Usage stays demoted to the existing footer bar. Cost is never the hero.

## 5. Component quality bar

- Every interactive element: hover, focus-visible, active, disabled states.
- Motion: one orchestrated entrance per view (staggered 40ms fade/rise on
  cards), micro-transitions ≤140ms elsewhere, `prefers-reduced-motion` honored.
- Empty/loading/error triads designed for every data module (skeletons match
  final layout; errors say what failed and the retry action).
- Density: dashboards read at a glance — numbers in `--font-mono` readouts,
  labels 11px uppercase `--text-faint`, generous card padding (20px+).
- No horizontal scroll at any viewport; tables/diagrams scroll inside their
  own container.

## 6. Attack protocol (standing)

After every build iteration: Playwright pass at 1440px (and 768px spot-checks) on
every affected route — screenshot, then critique against this memo: semantic-color
violations, trust-encoding gaps, missing instructional lines, dead states,
console errors. File findings; fix; re-shoot. A change ships only after its
attack pass.
