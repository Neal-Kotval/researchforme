# Autonomous-first spine rework — design

**Date:** 2026-07-07
**Status:** Approved (design), pending implementation plan
**Owner:** Neal

## Problem

The app is structured as two coequal modes toggled from the topbar:
`mode: "single" | "autonomous"`. This creates four compounding problems the
owner flagged:

1. **Two coequal modes** — the Single-area / Autonomous toggle reads as two
   apps bolted together. No single front door, no clear "main" experience.
2. **No real home base** — opening the app drops you into a form, not a place
   that shows ongoing explorations, usage, and a way to start a run.
3. **Autonomous is buried / rough** — the thing the owner actually cares about
   sits behind a toggle, and its flow is awkward.
4. **Navigation is confusing** — there are *two* stacked navigation systems
   (topbar mode toggle + an in-sidebar mode toggle), plus three competing
   back-affordances inside a project (Overview/Nodes/Idea tabs, a breadcrumb-ish
   "‹ Back" button, and implicit state resets). You lose your place.

The autonomous surface itself is already substantial and decent:
`HomeDashboard`, the tree/canvas (`GraphCanvas` / `ExplorationTree`),
`NodeInspector`, `RunControls`, and the usage gauges (`GlobalUsageBar`,
`UsageMeter`) all exist and work. They are simply trapped inside one of two
modes with doubled navigation on top. **This is a spine rework, not a rebuild.**

## Goal

Make autonomous exploration *the whole app*, with the dashboard as its front
door and one clean, linkable navigation spine. Fold the old single-area
analysis in as a fast "Quick scan" run that lands in the same tree/idea UI.

## Approach

**Chosen: A + lightweight hash routing.**

Dashboard-as-root with a single navigation spine, plus dependency-free hash
routing so the browser back/forward buttons and page refresh work and every
screen is linkable. Rejected alternatives: A-only (no routing — back button
dead, "where am I" unsolved); full react-router (heavier migration + new
dependency, unnecessary for three routes); minimal demotion (leaves the doubled
sidebar and tab/breadcrumb mess intact — fails the two problems the owner cares
most about).

## Design

### 1. Navigation spine — three levels, hash URLs

One hierarchy. Each level has a hash URL:

| Route                         | Screen                                              |
| ----------------------------- | --------------------------------------------------- |
| `#/` (or empty)               | **Dashboard** — usage gauge + exploration cards     |
| `#/e/:projectId`              | **Exploration** — Overview / Nodes tabs, tree/canvas |
| `#/e/:projectId/n/:nodeId`    | **Idea / node detail** — full-width                 |

A single `useHashRoute()` hook parses `window.location.hash` into a route
object and exposes navigation helpers:

```ts
type Route =
  | { view: "home" }
  | { view: "exploration"; projectId: string; nodeId: string | null };

function useHashRoute(): {
  route: Route;
  navHome: () => void;
  navProject: (projectId: string) => void;
  navNode: (projectId: string, nodeId: string) => void;
};
```

- Parses on mount and on every `hashchange` event.
- `navX` helpers set `window.location.hash` (which fires `hashchange`), so there
  is exactly **one** source of truth for "where am I": the URL.
- Unknown / malformed hashes fall back to `{ view: "home" }`.
- ~30 lines, no new dependency.

This hook **replaces** the scattered in-memory navigation state that currently
lives across `App.tsx` and `ExplorerView.tsx`: `mode`, `activeId`,
`selByProject`, and the `view` tab state all derive from `route` instead.

> Note: `selByProject` currently holds a *per-project* selected node so each tab
> remembers its selection. Under the single-spine model the selected node lives
> in the URL (`nodeId`), and only one exploration is "open" at a time, so the
> per-project map is no longer needed — selection is a function of the route.

### 2. App shell — kill the duality

`App.tsx` loses `mode` entirely:

- No topbar mode toggle. No two conditionally-rendered `<main>` trees.
- Boots to the Dashboard (`#/`).
- One persistent layout: **left rail + main area**. Main content switches on
  `route.view`.
- The single-area components — `AreaInput`, `OpportunityMap`, `GapTable`,
  `GapDetail`, `WeightControls`, plus the `analyze` / `rerank` calls and their
  `phase`/`report`/`weights` state — come **out of the flow**. Their value is
  folded into the Quick-scan run (§4). Files are left on disk (not deleted) but
  no longer imported by the shell, to keep this pass focused.
- The ⌘K `CommandPalette` stays, rewired so `jumpProject` / `newExploration`
  call the route helpers instead of setting `mode`.

The topbar shrinks to just the brand (the mode switch and single-area meta/
model/rerun controls are gone). Optionally the brand can move into the left
rail; keep a slim topbar for now to minimize churn.

### 3. The left rail — one nav, not two

`ExplorationSidebar` becomes the *only* navigation surface. It loses its
embedded `mode` / `setMode` single/autonomous toggle. Structure:

- Brand / product mark
- **Home** — active when `route.view === "home"`; navigates to `#/`
- **＋ New exploration** — opens the New-Exploration dialog
- **Exploration list** — each row links to `#/e/:id`; active row = current
  `projectId`; keep the existing inline-confirm delete
- `GlobalUsageBar` pinned at the bottom

Active states are derived from `route`, not from a separate `activeId` prop that
can drift out of sync with the URL.

### 4. Fold single-area in as "Quick scan"

The New-Exploration dialog (`NewExplorationDialog` in `ExplorerView.tsx`) gains a
**Depth** segmented control at the top that drives budget/pace presets:

| Depth        | Preset (starting point — tune during implementation)                   |
| ------------ | ---------------------------------------------------------------------- |
| **Quick scan** | shallow: low `max_nodes`, low `max_tokens`, `sprint` pace, intake skipped, autostart on |
| **Standard**   | today's default budget                                               |
| **Deep**       | higher/uncapped budget                                               |

- **Quick scan** *is* the reframed "analyze one area": type a domain, it runs a
  fast shallow pass and lands in the same tree/idea UI. No separate mode.
- Selecting a Depth updates the budget fields (which stay editable — the
  segmented control seeds a preset, it doesn't lock the fields).
- The Dashboard shows two CTAs: **＋ New exploration** (opens dialog at
  Standard) and a lighter **⚡ Quick scan** (opens dialog pre-set to Quick).

### 5. Within-exploration navigation cleanup

Three back-affordances collapse to one:

- A single **breadcrumb** at the top of the main area is the authoritative
  "where am I / go back": `Dashboard › {domain}` on the exploration screen, and
  `Dashboard › {domain} › Idea` on a node detail. Each segment navigates via the
  route helpers.
- **Overview / Nodes** remain as tabs on the exploration screen (they are two
  views of one project, not navigation levels — a tab strip is correct here).
- Selecting a node navigates to `#/e/:id/n/:nodeId`. The old dedicated "Idea"
  tab-button and the "‹ Back to nodes/overview" button are **removed** — the
  breadcrumb replaces both.

## Components touched

| File                                    | Change                                                             |
| --------------------------------------- | ------------------------------------------------------------------ |
| `frontend/src/hooks/useHashRoute.ts`    | **New** — the routing hook                                         |
| `frontend/src/App.tsx`                  | Remove `mode` + single-area flow; render shell driven by `route`   |
| `frontend/src/components/autonomous/ExplorerView.tsx` | Drive from `route` (not `activeId`/`view`/`selByProject`); breadcrumb; remove per-project tab/back buttons; Depth control in dialog |
| `frontend/src/components/autonomous/ExplorationSidebar.tsx` | Remove mode toggle; Home/New/list driven by route |
| `frontend/src/components/autonomous/HomeDashboard.tsx` | Add ⚡ Quick scan CTA; open via route |
| `frontend/src/components/CommandPalette.tsx` | Rewire `jumpProject` / `newExploration` to route helpers    |
| `frontend/src/index.css`                | Breadcrumb styles; remove now-dead single-area / mode-switch rules where safe |

Single-area component files (`AreaInput`, `OpportunityMap`, `GapTable`,
`GapDetail`, `WeightControls`) are left on disk but unimported. Backend
`analyze` / `rerank` endpoints are untouched.

## Data flow

1. `App` calls `useHashRoute()` → `route`.
2. `App` renders left rail (always) + main:
   - `route.view === "home"` → `HomeDashboard`
   - `route.view === "exploration"` → `ExplorerView`, passed `projectId` +
     `nodeId` from the route.
3. `ExplorerView` keeps its existing SSE store (`reduce` + `subscribeEvents`)
   but subscribes to `route.projectId` (instead of `activeId`) and derives the
   selected node from `route.nodeId`.
4. Navigation actions anywhere (sidebar, dashboard card, tree node click,
   breadcrumb, ⌘K) call the route helpers → set the hash → re-render. No
   parallel navigation state.

## Error / edge handling

- **Malformed or stale hash** (e.g. `#/e/<deleted-id>`): the project won't be in
  the store after `listProjects`; show the exploration screen's existing empty/
  error affordance, or redirect to `#/`. Deleting the open project navigates
  home.
- **Refresh mid-exploration**: hash persists → `ExplorerView` re-subscribes to
  the same `projectId` and re-hydrates from the SSE snapshot (already supported).
- **Node id in URL but node not yet streamed in**: render the tree/overview
  until the node arrives, then the detail; don't crash on a missing node
  (`nodes[nodeId] ?? null`, as today).

## Testing / verification

- **Playwright walkthrough** (per repo rule — no UI claim without it): boot to
  Dashboard; open an exploration from a card and from the sidebar; open a node;
  use the breadcrumb to step back up; browser back/forward traverses
  Dashboard → Exploration → Idea correctly; refresh on each route restores it;
  a Quick-scan creates a shallow run that lands in the tree; ⌘K jump/new drive
  the same routes.
- Verify no console errors and no horizontal overflow at 375 / 768 / 1440.
- Confirm the old mode toggle is gone and there is exactly one navigation
  system.

## Out of scope (YAGNI)

- Deleting single-area component files or the `analyze` / `rerank` backend.
- Reintroducing the opportunity-map / ranked-table as a surface (explicitly
  dropped — Quick-scan lands in the tree UI).
- Full react-router migration.
- Any redesign of the tree, node inspector, or run controls internals beyond
  the navigation wiring described here.
