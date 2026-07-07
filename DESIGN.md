# DESIGN.md — Market Gap Finder design system

Minimal, informative, light. Off-white canvas, white surfaces, near-black ink,
hairline neutral borders, generous whitespace. **One vermillion accent**, spent
only where it means something. Data stays neutral so the accent keeps its meaning.
Aesthetic reference: Langfuse / Linear calm.

All values live as **design tokens** in `frontend/src/tokens.css` (a single `:root`).
Components and `index.css` consume them via `var(--token)` — nothing hardcodes a raw
color, radius, or shadow. Change the system by changing tokens.

## Color

### Surfaces (warm-neutral, near-zero chroma — deliberately not "cream")
| Token | Value | Use |
|---|---|---|
| `--bg` | `#faf9f7` | app canvas |
| `--surface` | `#ffffff` | cards, drawer, primary content |
| `--surface-2` | `#f6f5f2` | table header, insets, secondary panels |
| `--surface-3` | `#efede8` | chips, tracks, tertiary fills |

### Ink (every body value clears 4.5:1 on `--surface`)
| Token | Value | Contrast | Use |
|---|---|---|---|
| `--text` | `#1c1b19` | ~15:1 | primary text, headings |
| `--text-dim` | `#52504a` | ~8:1 | secondary body |
| `--text-faint` | `#6f6d66` | ~5.3:1 | labels, captions |
| `--text-ghost` | `#9a978f` | — | decorative only, **never body copy** |

### Accent — vermillion
| Token | Value | Use |
|---|---|---|
| `--accent` | `#e34a2f` | top pick, primary buttons, selection, composite bar, brand mark |
| `--accent-bright` | `#f2542d` | hover |
| `--accent-strong` | `#bb3a20` | inline/link text on light (keeps 4.5:1) |
| `--accent-tint` / `--accent-tint-2` | `#fbeae5` / `#f6d8ce` | selected-row fill, soft borders |

**Accent budget:** the vermillion covers well under 10% of any screen. It is not
decoration. If you're reaching for it to "add color", stop — use neutral.

### Semantic states
`--live` (green), `--mock` (amber), `--unavailable` (red), `--empty` (gray), each with
a matching `-tint`. Used only on source badges, evidence dots, and warnings.

### Data ramp (the one sequential color moment: the 2×2 map)
`--ramp-0 … --ramp-4` run neutral → vermillion and encode trend tailwind (1–5). Score
bars in the table use a single `--data-neutral`; width already encodes magnitude, so
color there would be noise. The map's top pick overrides its ramp color with `--accent`.

## Typography
One family: **Inter** (`--font`), system fallbacks. Fixed rem-ish scale (`--fs-xs`…
`--fs-xl`), ratio ~1.2 — product density, not brand drama. Weights carry hierarchy
(`--fw-regular`…`--fw-bold` 680). Headings `letter-spacing: -0.02em`, `text-wrap:
balance`. Tabular numerals on all scores/metrics. No display font, no all-caps body,
no gradient text.

## Space, radius, elevation
- Spacing on a 4px base (`--sp-1`…`--sp-8`).
- Radius is tight and product-grade (`--r-xs` 5 → `--r-xl` 16).
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
