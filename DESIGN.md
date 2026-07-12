# DESIGN.md — Market Gap Finder design system

Synced to the "Gap Finder — Platform (light)" design handoff. Near-white
warm-green-grey canvas with a dotted grid, white surfaces, near-black ink,
hairline borders, generous whitespace. **One green accent used as a
highlighter** — a fill behind dark text (chips, pills, the live tag), never
colored text on white; the rare accent text/dot/sparkline use is "accent-ink"
(near-black). Charcoal primary buttons with kbd badges. Inter Tight carries
the UI; JetBrains Mono carries every number, ID, and metadata line.
Aesthetic reference: Langfuse / Linear calm.

All values live as **design tokens** in `frontend/src/tokens.css` (a single `:root`).
Components and `index.css` consume them via `var(--token)` — nothing hardcodes a raw
color, radius, or shadow. Change the system by changing tokens.

## Color

### Surfaces (warm-green-grey neutrals, near-white)
| Token | Value | Use |
|---|---|---|
| `--bg` | `#f6f7f5` | app canvas (dotted grid dots ride `--border`) |
| `--surface` | `#ffffff` | cards, drawer, primary content |
| `--surface-side` | `#fbfcfb` | the fixed platform sidebar |
| `--surface-2` | `#f5f7f4` | table header, insets, command blocks |
| `--surface-3` | `#eef1ee` | chips, tracks, tertiary fills |

### Ink (every body value clears 4.5:1 on `--surface`)
| Token | Value | Use |
|---|---|---|
| `--text` | `#1c1e1b` | primary text, headings |
| `--text-dim` | `#5b5e54` | secondary body |
| `--text-faint` | `#8a8d81` | labels, captions |
| `--text-ghost` | `#9ea196` | decorative only, **never body copy** |

### Accent — green highlighter
| Token | Value | Use |
|---|---|---|
| `--accent` / `--highlight` | `#54b56a` | highlighter fills behind dark text: live tag, agent-live pill, sprinting, selection, meters |
| `--highlight-border` | `#2f9550` | hairline on highlighter chips/pills |
| `--accent-strong` | `#26261f` | "accent-ink" — the rare accent **text**/dot/sparkline is near-black, never green text |
| `--accent-tint` / `--highlight-tint` | `#e9f4ec` / `#eaf4ec` | selected rows, the Compare lead-row wash |

**Accent rule (handoff, hard):** the green is only ever a fill behind dark
text. If you're reaching for green text on white, use accent-ink instead.

### Semantic states
`--live` (green), `--mock` (amber), `--unavailable` (red), `--empty` (gray), each with
a matching `-tint`. Used only on source badges, evidence dots, and warnings.

### Data ramp
`--ramp-0 … --ramp-4` run warm grey → accent green; a viability chip's fill weight
matches earned trust (memo §2). Score bars use a single `--data-neutral`; width
already encodes magnitude, so color there would be noise.

## Typography
**Inter Tight** (`--font`) carries the UI; **JetBrains Mono** (`--mono`) carries
every number, ID, count, timestamp, and command. Fixed scale (`--fs-xs`…`--fs-xl`).
Weights carry hierarchy (`--fw-regular`…`--fw-bold` 680). Headings
`letter-spacing: -0.01 to -0.02em`. Uppercase micro-labels 9.5–11px/600 with
`.04–.08em` tracking. Tabular numerals on all scores/metrics. No gradient text.

## Space, radius, elevation
- Spacing on a 4px base (`--sp-1`…`--sp-8`).
- Radius per the handoff: cards/buttons 7–8px (`--r-lg`/`--r-xl`), chips/badges
  3–4px (`--r-xs`/`--r-sm`). The Explore run summary is intentionally radius 0
  with 9×9px crop-mark corner ticks.
- **Borders carry structure, not shadows.** Shadows (`--shadow-sm/md/lg`) are barely
  there — a hairline `--border` defines most edges. `--shadow-lg` is reserved for the
  drawer and the input card.

## Motion
`--dur-fast` 140ms / `--dur-mid` 240ms, `--ease-out` (no bounce). Motion conveys state
only: hover, selection, the reweighting spinner, the drawer slide, skeleton shimmer.
`prefers-reduced-motion` collapses all of it. No page-load choreography.

## Components (conventions)
- **Buttons** — `.btn` (neutral) / `.btn-primary` (vermillion) / `.btn-ghost`; every
  one has hover/active/disabled. Labels are verb+object.
- **Cards** — `.card`, white on the off-white canvas, hairline border, `--shadow-sm`.
  No nested cards.
- **Table** — sticky neutral header, hairline row separators, selected row = accent
  tint + a 2px inset accent bar (the one permitted side-indicator, and only for the
  active row).
- **Drawer** — right-side panel with scrim; Esc / scrim-click closes. Detail lives
  here, not in a modal.
- **Segmented control** (`.seg-control`) — the model picker; active option raised to
  `--surface` with `--shadow-sm`.
- **States** — skeletons for loading (not spinners mid-content), a teaching empty
  state, an error state with retry, and per-source loading in the status strip.

## Bans (project-specific, on top of impeccable's shared bans)
- No rainbow/multi-hue data ramps. Neutral data + the single vermillion sequence only.
- No gradient text, no glassmorphism, no decorative side-stripes (the selected-row bar
  is the one functional exception).
- No tiny uppercase tracked eyebrow on every section — `.eyebrow` is a quiet muted
  label, not a kicker.
- No color on inactive states.

## Accessibility
Body text ≥ 4.5:1 (verified above). Focus-visible ring on every interactive element
(`--ring`). Table rows and map bubbles are keyboard-operable (Enter/Space). Labels on
all inputs and the sliders.
