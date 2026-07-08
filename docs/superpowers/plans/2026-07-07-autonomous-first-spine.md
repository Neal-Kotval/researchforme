# Autonomous-first Spine Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make autonomous exploration the whole app — dashboard as the front door, one hash-routed navigation spine (Dashboard → Exploration → Idea), single-area folded in as a "Quick scan" depth preset.

**Architecture:** A dependency-free `useHashRoute()` hook makes `window.location.hash` the single source of truth for navigation. `App.tsx` loses its `mode` toggle and renders one persistent shell (topbar brand + `ExplorerView`) driven by the route. `ExplorerView` and the sidebar stop owning navigation state and derive everything from the route; a breadcrumb replaces the three competing in-project back-affordances. The single-area analysis flow is removed from the UI and reframed as a shallow "Quick scan" run through the existing autonomous backend.

**Tech Stack:** React 19 + TypeScript, Vite, `vitest` (added for the routing hook's pure logic). No react-router.

## Global Constraints

- **No new runtime dependency** — hash routing is hand-rolled; `vitest` is dev-only and must not affect `vite build`.
- **Build must stay green** — after every task, `npm run build` (`tsc -b && vite build`) passes with no type errors.
- **No UI claim without Playwright** (repo rule) — the final task is a Playwright walkthrough; no task may assert the UI works from typecheck alone.
- **Do not delete** the single-area component files (`AreaInput`, `OpportunityMap`, `GapTable`, `GapDetail`, `WeightControls`) or the backend `analyze` / `rerank` endpoints — they go dormant, not away.
- **Topbar owns the brand**; the left rail does NOT duplicate it (the rail's `SideNav` brand block is removed along with the mode toggle).
- All work is under `frontend/`. Run npm commands from `frontend/`.

---

## File structure

| File | Responsibility |
| --- | --- |
| `frontend/src/hooks/useHashRoute.ts` | **New.** Pure `parseHash` / `routeToHash` + the `useHashRoute()` hook. The one source of navigation truth. |
| `frontend/src/hooks/useHashRoute.test.ts` | **New.** Unit tests for the pure parse/format functions. |
| `frontend/src/App.tsx` | Rewritten: no `mode`, no single-area flow. Renders topbar brand + `ExplorerView`, wires ⌘K. |
| `frontend/src/components/autonomous/ExplorerView.tsx` | Route-driven (no `activeId` / `selByProject` state); breadcrumb; Depth control in the new-exploration dialog. |
| `frontend/src/components/autonomous/ExplorationSidebar.tsx` | Mode toggle removed; Home/New/list driven by route-derived props. |
| `frontend/src/components/autonomous/HomeDashboard.tsx` | Adds the ⚡ Quick scan CTA. |
| `frontend/src/components/CommandPalette.tsx` | `PaletteCtx` loses `mode`/`setMode`; mode commands removed. |
| `frontend/src/index.css` | Breadcrumb styles. |
| `frontend/package.json` | Adds `vitest` dev-dep + `test` script. |

---

## Task 1: Hash routing hook + test infrastructure

**Files:**
- Create: `frontend/src/hooks/useHashRoute.ts`
- Create: `frontend/src/hooks/useHashRoute.test.ts`
- Modify: `frontend/package.json` (add `vitest`, add `test` script)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `type Route = { view: "home" } | { view: "exploration"; projectId: string; nodeId: string | null }`
  - `function parseHash(hash: string): Route`
  - `function routeToHash(route: Route): string`
  - `function useHashRoute(): { route: Route; navHome: () => void; navProject: (projectId: string) => void; navNode: (projectId: string, nodeId: string) => void }`

- [ ] **Step 1: Add vitest as a dev dependency**

Run (from `frontend/`):
```bash
npm install -D vitest
```
Expected: `vitest` appears under `devDependencies` in `package.json`; no changes to `dependencies`.

- [ ] **Step 2: Add the `test` script**

In `frontend/package.json`, add a `test` script alongside the existing ones so the `"scripts"` block reads:
```json
"scripts": {
  "dev": "vite",
  "build": "tsc -b && vite build",
  "preview": "vite preview",
  "test": "vitest run"
}
```

- [ ] **Step 3: Write the failing test**

Create `frontend/src/hooks/useHashRoute.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { parseHash, routeToHash, type Route } from "./useHashRoute";

describe("parseHash", () => {
  it("treats an empty hash as home", () => {
    expect(parseHash("")).toEqual({ view: "home" });
  });
  it("treats '#/' as home", () => {
    expect(parseHash("#/")).toEqual({ view: "home" });
  });
  it("parses a project route", () => {
    expect(parseHash("#/e/abc")).toEqual({
      view: "exploration",
      projectId: "abc",
      nodeId: null,
    });
  });
  it("parses a node route", () => {
    expect(parseHash("#/e/abc/n/xyz")).toEqual({
      view: "exploration",
      projectId: "abc",
      nodeId: "xyz",
    });
  });
  it("falls back to home on a malformed hash", () => {
    expect(parseHash("#/garbage/here")).toEqual({ view: "home" });
  });
  it("decodes percent-encoded ids", () => {
    expect(parseHash("#/e/a%20b/n/n%2F1")).toEqual({
      view: "exploration",
      projectId: "a b",
      nodeId: "n/1",
    });
  });
});

describe("routeToHash + parseHash round-trip", () => {
  it("round-trips a node route with awkward characters", () => {
    const r: Route = { view: "exploration", projectId: "a b", nodeId: "n 1" };
    expect(parseHash(routeToHash(r))).toEqual(r);
  });
  it("formats home as '#/'", () => {
    expect(routeToHash({ view: "home" })).toBe("#/");
  });
});
```

- [ ] **Step 4: Run the test to verify it fails**

Run (from `frontend/`): `npm test`
Expected: FAIL — cannot resolve `./useHashRoute` (module does not exist yet).

- [ ] **Step 5: Implement the hook**

Create `frontend/src/hooks/useHashRoute.ts`:
```ts
import { useCallback, useEffect, useState } from "react";

export type Route =
  | { view: "home" }
  | { view: "exploration"; projectId: string; nodeId: string | null };

/** Parse `window.location.hash` into a Route. Unknown shapes fall back to home. */
export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, "").replace(/^\/+/, "");
  const parts = raw.split("/").filter(Boolean);
  // ["e", projectId] or ["e", projectId, "n", nodeId]
  if (parts[0] === "e" && parts[1]) {
    const projectId = decodeURIComponent(parts[1]);
    if (parts[2] === "n" && parts[3]) {
      return { view: "exploration", projectId, nodeId: decodeURIComponent(parts[3]) };
    }
    if (parts.length === 2) {
      return { view: "exploration", projectId, nodeId: null };
    }
  }
  return { view: "home" };
}

/** Format a Route back into a hash string. */
export function routeToHash(route: Route): string {
  if (route.view === "home") return "#/";
  const base = `#/e/${encodeURIComponent(route.projectId)}`;
  return route.nodeId ? `${base}/n/${encodeURIComponent(route.nodeId)}` : base;
}

/**
 * The single source of navigation truth. Reads the hash on mount and on every
 * `hashchange`; the nav helpers write the hash (which fires `hashchange`), so
 * there is exactly one place "where am I" is decided.
 */
export function useHashRoute() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    onHash(); // sync in case the hash changed before the listener attached
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const navHome = useCallback(() => {
    window.location.hash = routeToHash({ view: "home" });
  }, []);
  const navProject = useCallback((projectId: string) => {
    window.location.hash = routeToHash({ view: "exploration", projectId, nodeId: null });
  }, []);
  const navNode = useCallback((projectId: string, nodeId: string) => {
    window.location.hash = routeToHash({ view: "exploration", projectId, nodeId });
  }, []);

  return { route, navHome, navProject, navNode };
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run (from `frontend/`): `npm test`
Expected: PASS — all 8 assertions green.

- [ ] **Step 7: Verify the build still compiles**

Run (from `frontend/`): `npm run build`
Expected: succeeds (the hook isn't imported yet, but must typecheck).

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/hooks/useHashRoute.ts frontend/src/hooks/useHashRoute.test.ts
git commit -m "feat: add useHashRoute hook + vitest for the navigation spine"
```

---

## Task 2: Spine cutover — kill the mode duality, drive the app from the route

This is one atomic change: TypeScript will not compile in an intermediate state, so `App`, `ExplorerView`, `ExplorationSidebar`, and `CommandPalette` change together. Ends with a green build and a manual smoke test.

**Files:**
- Modify: `frontend/src/App.tsx` (full rewrite)
- Modify: `frontend/src/components/autonomous/ExplorerView.tsx`
- Modify: `frontend/src/components/autonomous/ExplorationSidebar.tsx`
- Modify: `frontend/src/components/CommandPalette.tsx`
- Modify: `frontend/src/index.css` (breadcrumb styles)

**Interfaces:**
- Consumes (from Task 1): `useHashRoute`, `Route`.
- Produces (for Task 3): `ExplorerView` renders `HomeDashboard` with `onOpen`/`onNew` and `NewExplorationDialog`; the `showNew` open-state + a module-scope `Depth` type are added there in Task 3. `PaletteCtx` is now `{ jumpProject: (pid: string) => void; newExploration: () => void }`.

- [ ] **Step 1: Rewrite `App.tsx`**

Replace the **entire** contents of `frontend/src/App.tsx` with:
```tsx
import { useEffect, useState } from "react";
import { useHashRoute } from "./hooks/useHashRoute";
import ExplorerView from "./components/autonomous/ExplorerView";
import CommandPalette from "./components/CommandPalette";

export default function App() {
  const { route, navHome, navProject, navNode } = useHashRoute();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [newExplorationSignal, setNewExplorationSignal] = useState(0);

  // ⌘K toggles the command palette.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <div className="brand-mark" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m10.065 12.493-6.18 1.318a.934.934 0 0 1-1.108-.702l-.537-2.15a1.07 1.07 0 0 1 .691-1.265l13.504-4.44" />
                <path d="m13.56 11.747 4.332-.924" />
                <path d="m16 21-3.105-6.21" />
                <path d="M16.485 5.94a2 2 0 0 1 1.455-2.425l1.09-.272a1 1 0 0 1 1.212.727l1.515 6.06a1 1 0 0 1-.727 1.213l-1.09.272a2 2 0 0 1-2.425-1.455z" />
                <path d="m6.158 8.633 1.114 4.456" />
                <path d="m8 21 3.105-6.21" />
                <circle cx="12" cy="13" r="2" />
              </svg>
            </div>
            <div>
              <div className="brand-name">Market Gap Finder</div>
              <div className="brand-sub">Signal-driven opportunity discovery</div>
            </div>
          </div>
          <div className="topbar-spacer" />
        </div>
      </header>

      <main className="shell explorer-shell">
        <ExplorerView
          route={route}
          navHome={navHome}
          navProject={navProject}
          navNode={navNode}
          newExplorationSignal={newExplorationSignal}
        />
      </main>

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        ctx={{
          jumpProject: (pid) => navProject(pid),
          newExploration: () => setNewExplorationSignal((n) => n + 1),
        }}
      />
    </div>
  );
}
```

- [ ] **Step 2: Update `CommandPalette.tsx` — drop mode from the context**

In `frontend/src/components/CommandPalette.tsx`, replace the `PaletteCtx` interface:
```ts
export interface PaletteCtx {
  mode: "single" | "autonomous";
  setMode: (m: "single" | "autonomous") => void;
  jumpProject: (pid: string) => void;
  newExploration: () => void;
}
```
with:
```ts
export interface PaletteCtx {
  jumpProject: (pid: string) => void;
  newExploration: () => void;
}
```

Then replace the `base` commands array:
```ts
    const base: Cmd[] = [
      { id: "new", label: "New exploration", hint: "autonomous", icon: "＋", run: ctx.newExploration },
      { id: "single", label: "Single-area search", hint: "mode", icon: "◎", run: () => ctx.setMode("single") },
      { id: "auto", label: "Autonomous explorer", hint: "mode", icon: "✦", run: () => ctx.setMode("autonomous") },
    ];
```
with:
```ts
    const base: Cmd[] = [
      { id: "new", label: "New exploration", hint: "autonomous", icon: "＋", run: ctx.newExploration },
    ];
```

- [ ] **Step 3: Update `ExplorationSidebar.tsx` — remove the mode toggle**

In `frontend/src/components/autonomous/ExplorationSidebar.tsx`:

Remove the `SideNav` import (line 4): delete `import SideNav from "../SideNav";`

Replace the `Props` interface:
```ts
interface Props {
  projects: Project[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onHome: () => void;
  mode?: "single" | "autonomous";
  setMode?: (m: "single" | "autonomous") => void;
}
```
with:
```ts
interface Props {
  projects: Project[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onHome: () => void;
}
```

Replace the destructure line:
```ts
export default function ExplorationSidebar({
  projects, activeId, onSelect, onNew, onDelete, onHome, mode, setMode,
}: Props) {
```
with:
```ts
export default function ExplorationSidebar({
  projects, activeId, onSelect, onNew, onDelete, onHome,
}: Props) {
```

Remove the `SideNav` render line inside the `<aside>`:
```tsx
      {mode && setMode && <SideNav mode={mode} setMode={setMode} />}
```
(delete that line entirely).

- [ ] **Step 4: Convert `ExplorerView.tsx` to be route-driven**

In `frontend/src/components/autonomous/ExplorerView.tsx`, apply these edits.

**(4a) Add the Route import** near the other imports (after the `HomeDashboard`/`ProjectDigest` imports):
```ts
import type { Route } from "../../hooks/useHashRoute";
```

**(4b) Replace the props interface + signature.** Replace:
```ts
interface ExplorerViewProps {
  /** ⌘K "jump to exploration" — focus this project when it changes. */
  focusProjectId?: string | null;
  /** ⌘K "new exploration" — open the new-exploration dialog when this bumps. */
  newExplorationSignal?: number;
  /** App-shell mode nav (rendered at the top of the sidebar). */
  mode?: "single" | "autonomous";
  setMode?: (m: "single" | "autonomous") => void;
}

export default function ExplorerView({ focusProjectId, newExplorationSignal, mode, setMode }: ExplorerViewProps = {}) {
```
with:
```ts
interface ExplorerViewProps {
  route: Route;
  navHome: () => void;
  navProject: (projectId: string) => void;
  navNode: (projectId: string, nodeId: string) => void;
  /** ⌘K "new exploration" — open the new-exploration dialog when this bumps. */
  newExplorationSignal?: number;
}

export default function ExplorerView({ route, navHome, navProject, navNode, newExplorationSignal }: ExplorerViewProps) {
```

**(4c) Replace the navigation state.** Replace:
```ts
  const [state, dispatch] = useReducer(reduce, { byId: {}, order: [] });
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selByProject, setSelByProject] = useState<Record<string, string | null>>({});
  const [showNew, setShowNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<"overview" | "nodes">("nodes");
  const [treeMode, setTreeMode] = useState<"canvas" | "list">("canvas");
```
with:
```ts
  const [state, dispatch] = useReducer(reduce, { byId: {}, order: [] });
  const [loaded, setLoaded] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<"overview" | "nodes">("nodes");
  const [treeMode, setTreeMode] = useState<"canvas" | "list">("canvas");

  // Navigation is derived from the URL — never held as separate state.
  const activeId = route.view === "exploration" ? route.projectId : null;
  const selectedId = route.view === "exploration" ? route.nodeId : null;
```

**(4d) Mark the list as loaded after the first fetch.** Replace the `refresh` callback:
```ts
  const refresh = useCallback(async () => {
    try {
      const ps = await listProjects();
      dispatch({ type: "setProjects", projects: ps });
    } catch (e) {
      setErr(errMsg(e, "Could not load explorations."));
    }
  }, []);
```
with:
```ts
  const refresh = useCallback(async () => {
    try {
      const ps = await listProjects();
      dispatch({ type: "setProjects", projects: ps });
    } catch (e) {
      setErr(errMsg(e, "Could not load explorations."));
    } finally {
      setLoaded(true);
    }
  }, []);
```

**(4e) Simplify the project-switch reset effect.** Replace:
```ts
  // Opening a project always lands on the Nodes/tree with no stale idea selected
  // (a lingering per-project selection used to dump you straight into a full-page
  // idea detail). Clear the selection and reset the tab on every project switch.
  useEffect(() => {
    if (activeId) {
      setSelByProject((prev) => (prev[activeId] ? { ...prev, [activeId]: null } : prev));
      setView("nodes");
    }
  }, [activeId]);
```
with:
```ts
  // Opening a project always lands on the Nodes tab. Selection lives in the URL.
  useEffect(() => {
    if (activeId) setView("nodes");
  }, [activeId]);
```

**(4f) Remove the ⌘K focusProjectId effect.** Delete this block entirely:
```ts
  // ⌘K: jump to a specific exploration (refresh first if we don't have it yet).
  useEffect(() => {
    if (!focusProjectId) return;
    setActiveId(focusProjectId);
    if (!state.byId[focusProjectId]) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusProjectId]);
```
(The palette now calls `navProject` directly, so the URL — and this component — react automatically.)

**(4g) Redirect stale/bogus project URLs home.** Replace:
```ts
  // Default to the Home dashboard (activeId === null); only clear a selection
  // that points at a now-deleted project. Never auto-jump into a project, so the
  // landing surface is the "glance at everything" home.
  useEffect(() => {
    if (activeId && !state.byId[activeId]) setActiveId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderSig]);
```
with:
```ts
  // A URL that points at a project we don't have (deleted, or a bad deep link)
  // falls back home — but only once the list has actually loaded, so a valid
  // deep link isn't bounced before its project arrives.
  useEffect(() => {
    if (loaded && activeId && !state.byId[activeId]) navHome();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderSig, loaded, activeId]);
```

**(4h) Rewrite `selectNode` to navigate.** Replace:
```ts
  const selectedId = activeId ? selByProject[activeId] ?? null : null;
  const selectNode = useCallback(
    (id: string | null) => {
      if (!activeId) return;
      setSelByProject((prev) => ({ ...prev, [activeId]: id }));
    },
    [activeId]
  );
```
with:
```ts
  const selectNode = useCallback(
    (id: string | null) => {
      if (!activeId) return;
      if (id) navNode(activeId, id);
      else navProject(activeId);
    },
    [activeId, navNode, navProject]
  );
```
(Note: the `selectedId` const is now declared up in step 4c, so it is removed here.)

**(4i) Navigate on create.** Replace:
```ts
  const onCreate = useCallback(
    async (req: CreateProjectRequest) => {
      const p = await createProject(req);
      await refresh();
      setActiveId(p.id);
      setShowNew(false);
    },
    [refresh]
  );
```
with:
```ts
  const onCreate = useCallback(
    async (req: CreateProjectRequest) => {
      const p = await createProject(req);
      await refresh();
      navProject(p.id);
      setShowNew(false);
    },
    [refresh, navProject]
  );
```

**(4j) Navigate home when the open project is deleted.** Replace:
```ts
  const onDelete = useCallback(
    async (id: string) => {
      try {
        await deleteProject(id);
        dispatch({ type: "removeProject", id });
        setActiveId((cur) => (cur === id ? state.order.find((x) => x !== id) ?? null : cur));
      } catch (e) {
        setErr(errMsg(e, "Could not delete the exploration."));
        refresh();
      }
    },
    [state.order, refresh]
  );
```
with:
```ts
  const onDelete = useCallback(
    async (id: string) => {
      try {
        await deleteProject(id);
        dispatch({ type: "removeProject", id });
        if (activeId === id) navHome();
      } catch (e) {
        setErr(errMsg(e, "Could not delete the exploration."));
        refresh();
      }
    },
    [activeId, navHome, refresh]
  );
```

**(4k) Rewire the sidebar + dashboard props.** In the JSX, replace:
```tsx
        <ExplorationSidebar
          projects={allProjects}
          activeId={activeId}
          onSelect={setActiveId}
          onNew={() => setShowNew(true)}
          onDelete={onDelete}
          onHome={() => setActiveId(null)}
          mode={mode}
          setMode={setMode}
        />
```
with:
```tsx
        <ExplorationSidebar
          projects={allProjects}
          activeId={activeId}
          onSelect={navProject}
          onNew={() => setShowNew(true)}
          onDelete={onDelete}
          onHome={navHome}
        />
```

And replace:
```tsx
            <HomeDashboard
              projects={allProjects}
              onOpen={setActiveId}
              onNew={() => setShowNew(true)}
            />
```
with:
```tsx
            <HomeDashboard
              projects={allProjects}
              onOpen={navProject}
              onNew={() => setShowNew(true)}
            />
```

**(4l) Add the breadcrumb and remove the in-project back-affordances.** Replace the whole project view header region — from the `<div className="exp-topline">` opening through the end of the `exp-tabbar` block — i.e. replace:
```tsx
              <div className="exp-topline">
```
...that is, insert the breadcrumb immediately **before** `<div className="exp-topline">`:
```tsx
              <nav className="exp-crumbs" aria-label="Breadcrumb">
                <button className="crumb" onClick={navHome}>Dashboard</button>
                <span className="crumb-sep" aria-hidden>›</span>
                <button
                  className={`crumb${!selectedNode ? " current" : ""}`}
                  onClick={() => activeId && navProject(activeId)}
                  aria-current={!selectedNode ? "page" : undefined}
                >
                  {project.domain}
                </button>
                {selectedNode && (
                  <>
                    <span className="crumb-sep" aria-hidden>›</span>
                    <span className="crumb current" aria-current="page">
                      {selectedNode.kind === "gap" || selectedNode.kind === "gap_candidate"
                        ? "Idea" : titleKind(selectedNode.kind)}
                    </span>
                  </>
                )}
              </nav>

              <div className="exp-topline">
```

Then in the `exp-tabbar`, remove the conditional Idea tab button. Delete:
```tsx
                  {selectedNode && (
                    <button role="tab" aria-selected className="exp-tab active idea">
                      {selectedNode.kind === "gap" || selectedNode.kind === "gap_candidate"
                        ? "Idea" : titleKind(selectedNode.kind)}
                    </button>
                  )}
```

Then in the node-detail render, remove the `inspect-back` button. Replace:
```tsx
              {selectedNode ? (
                <div className="card exp-detail">
                  <button className="inspect-back" onClick={() => selectNode(null)}>
                    ‹ Back to {view === "overview" ? "overview" : "nodes"}
                  </button>
                  <NodeInspector
                    node={selectedNode}
                    childNodes={selectedChildren}
                    onSelectChild={selectNode}
                  />
                </div>
              ) : view === "nodes" ? (
```
with:
```tsx
              {selectedNode ? (
                <div className="card exp-detail">
                  <NodeInspector
                    node={selectedNode}
                    childNodes={selectedChildren}
                    onSelectChild={selectNode}
                  />
                </div>
              ) : view === "nodes" ? (
```

- [ ] **Step 5: Add breadcrumb styles**

Append to `frontend/src/index.css`:
```css
/* Exploration breadcrumb — the single authoritative "where am I / go back". */
.exp-crumbs {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  font-size: 13px;
}
.exp-crumbs .crumb {
  background: none;
  border: none;
  padding: 0;
  font: inherit;
  color: var(--text-faint);
  cursor: pointer;
  transition: color 0.12s ease;
}
.exp-crumbs .crumb:hover { color: var(--text); }
.exp-crumbs .crumb.current {
  color: var(--text);
  font-weight: 500;
  cursor: default;
}
.exp-crumbs .crumb-sep { color: var(--text-faint); opacity: 0.6; }
```

- [ ] **Step 6: Verify the build compiles**

Run (from `frontend/`): `npm run build`
Expected: succeeds with no type errors. (If `tsc` flags `SideNav` as unused-but-existing, that's fine — the file stays on disk, just unimported.)

- [ ] **Step 7: Smoke-test in the dev server**

Run (from `frontend/`): `npm run dev`, open the app.
Expected, by inspection:
- App boots to the dashboard; the URL hash is `#/` (or empty).
- No "Single area / Autonomous" toggle anywhere; the sidebar has no Explorer/Single-area mode buttons.
- Clicking an exploration card sets the hash to `#/e/<id>` and shows the tree; a breadcrumb `Dashboard › <domain>` appears.
- Clicking a node sets the hash to `#/e/<id>/n/<nodeId>` and the breadcrumb gains `› Idea`; there is no separate "‹ Back" button or "Idea" tab.
- Clicking `<domain>` in the breadcrumb returns to the tree; clicking `Dashboard` returns home.
- Browser Back/Forward walk this history correctly.

- [ ] **Step 8: Run unit tests + commit**

Run (from `frontend/`): `npm test` (still green).
```bash
git add frontend/src/App.tsx frontend/src/components/autonomous/ExplorerView.tsx frontend/src/components/autonomous/ExplorationSidebar.tsx frontend/src/components/CommandPalette.tsx frontend/src/index.css
git commit -m "feat: route-driven navigation spine; remove mode duality and doubled nav"
```

---

## Task 3: Fold single-area in as a "Quick scan" depth preset

**Files:**
- Modify: `frontend/src/components/autonomous/ExplorerView.tsx` (Depth control in `NewExplorationDialog`; pass an initial depth)
- Modify: `frontend/src/components/autonomous/HomeDashboard.tsx` (⚡ Quick scan CTA)

**Interfaces:**
- Consumes (from Task 2): `ExplorerView`'s `showNew` state; `NewExplorationDialog`; `HomeDashboard` with `onOpen`/`onNew`.
- Produces: module-scope `type Depth = "quick" | "standard" | "deep"` and `DEPTH_PRESETS` in `ExplorerView.tsx`; `HomeDashboard` gains an `onQuickNew: () => void` prop.

- [ ] **Step 1: Add the Depth type + presets at module scope in `ExplorerView.tsx`**

Near the top of `frontend/src/components/autonomous/ExplorerView.tsx`, after the imports and before the `HISTORY_CAP` constant, add:
```ts
/* -------------------------------------------------------------------- depth -- */
// "Quick scan" is the folded-in single-area analysis: a fast, shallow pass that
// lands in the same tree/idea UI. Standard/Deep widen the budget. The preset
// only *seeds* the budget fields — they stay editable.
export type Depth = "quick" | "standard" | "deep";

const DEPTH_PRESETS: Record<Depth, Pick<Budget, "max_nodes" | "max_tokens" | "pace">> = {
  quick:    { max_nodes: 40,   max_tokens: 150_000, pace: "sprint" },
  standard: { max_nodes: 400,  max_tokens: null,    pace: "balanced" },
  deep:     { max_nodes: 1200, max_tokens: null,    pace: "balanced" },
};
```

- [ ] **Step 2: Track the desired depth when opening the dialog**

In the `ExplorerView` component body, add a depth state next to `showNew` (from Task 2 step 4c region):
```ts
  const [newDepth, setNewDepth] = useState<Depth>("standard");
```

Add two open-helpers (place them near `onCreate`):
```ts
  const openNew = useCallback((depth: Depth) => {
    setNewDepth(depth);
    setShowNew(true);
  }, []);
```

Update the three places that open the dialog to go through `openNew`:
- Sidebar: `onNew={() => setShowNew(true)}` → `onNew={() => openNew("standard")}`
- HomeDashboard: `onNew={() => setShowNew(true)}` → `onNew={() => openNew("standard")}`, and add `onQuickNew={() => openNew("quick")}`
- The ⌘K new-exploration effect (opens on `newExplorationSignal`): find
```ts
  useEffect(() => {
    if (newExplorationSignal && newExplorationSignal > 0) setShowNew(true);
  }, [newExplorationSignal]);
```
and replace with:
```ts
  useEffect(() => {
    if (newExplorationSignal && newExplorationSignal > 0) openNew("standard");
  }, [newExplorationSignal, openNew]);
```

- [ ] **Step 3: Pass the initial depth into the dialog**

Update the dialog render at the bottom of `ExplorerView`. Replace:
```tsx
      {showNew && (
        <NewExplorationDialog onClose={() => setShowNew(false)} onCreate={onCreate} />
      )}
```
with:
```tsx
      {showNew && (
        <NewExplorationDialog initialDepth={newDepth} onClose={() => setShowNew(false)} onCreate={onCreate} />
      )}
```

- [ ] **Step 4: Add the Depth control to `NewExplorationDialog`**

Update the dialog's props type. Replace:
```ts
interface DialogProps {
  onClose: () => void;
  onCreate: (req: CreateProjectRequest) => Promise<void>;
}

function NewExplorationDialog({ onClose, onCreate }: DialogProps) {
```
with:
```ts
interface DialogProps {
  initialDepth: Depth;
  onClose: () => void;
  onCreate: (req: CreateProjectRequest) => Promise<void>;
}

function NewExplorationDialog({ initialDepth, onClose, onCreate }: DialogProps) {
```

Seed the initial budget from the depth. Replace:
```ts
  const [budget, setBudget] = useState<Budget>({ ...DEFAULT_BUDGET });
```
with:
```ts
  const [depth, setDepth] = useState<Depth>(initialDepth);
  const [budget, setBudget] = useState<Budget>({ ...DEFAULT_BUDGET, ...DEPTH_PRESETS[initialDepth] });

  // Selecting a depth re-seeds the budget preset (fields stay editable after).
  const applyDepth = (d: Depth) => {
    setDepth(d);
    setBudget((b) => ({ ...b, ...DEPTH_PRESETS[d] }));
  };
```

Add the Depth segmented control at the top of `nd-body`, immediately after the opening `<div className="nd-body">` and before the Domain `<label>`:
```tsx
          <label className="field-label">Depth</label>
          <div className="seg-control" role="radiogroup" aria-label="Depth">
            {(["quick", "standard", "deep"] as Depth[]).map((d) => (
              <button
                key={d}
                type="button"
                role="radio"
                aria-checked={depth === d}
                aria-pressed={depth === d}
                className="seg-option"
                onClick={() => applyDepth(d)}
              >
                <span className="so-label" style={{ textTransform: "capitalize" }}>
                  {d === "quick" ? "Quick scan" : d}
                </span>
              </button>
            ))}
          </div>
          <div className="nd-refine-hint" style={{ marginBottom: 6 }}>
            {depth === "quick"
              ? "A fast, shallow pass — great for a first read on a domain."
              : depth === "deep"
              ? "A long, wide exploration. Budget caps below still apply."
              : "A balanced run. Tune the budget below if you like."}
          </div>
```
(The `.seg-control`/`.seg-option`/`.so-label` classes already exist — they're used by the Pace control — so no new CSS is required.)

- [ ] **Step 5: Verify the build compiles**

Run (from `frontend/`): `npm run build`
Expected: succeeds. TypeScript confirms `HomeDashboard` now requires `onQuickNew` (added in the next step) — if it errors on a missing prop, complete Step 6 before re-running.

- [ ] **Step 6: Add the ⚡ Quick scan CTA to `HomeDashboard`**

In `frontend/src/components/autonomous/HomeDashboard.tsx`, update the props. Replace:
```ts
interface Props {
  projects: Project[];
  onOpen: (pid: string) => void;
  onNew: () => void;
}
```
with:
```ts
interface Props {
  projects: Project[];
  onOpen: (pid: string) => void;
  onNew: () => void;
  onQuickNew: () => void;
}
```

Replace the component signature:
```ts
export default function HomeDashboard({ projects, onOpen, onNew }: Props) {
```
with:
```ts
export default function HomeDashboard({ projects, onOpen, onNew, onQuickNew }: Props) {
```

Replace the header actions. Replace:
```tsx
        <button className="btn btn-primary" onClick={onNew}>＋ New exploration</button>
```
with:
```tsx
        <div className="home-head-actions">
          <button className="btn" onClick={onQuickNew}>⚡ Quick scan</button>
          <button className="btn btn-primary" onClick={onNew}>＋ New exploration</button>
        </div>
```

Append the small layout rule to `frontend/src/index.css`:
```css
.home-head-actions { display: flex; gap: 10px; align-items: center; }
```

- [ ] **Step 7: Verify build + smoke test**

Run (from `frontend/`): `npm run build` → succeeds.
Run `npm run dev`; on the dashboard:
- Two buttons show: `⚡ Quick scan` and `＋ New exploration`.
- `⚡ Quick scan` opens the dialog with **Depth = Quick scan** selected and the budget pre-seeded (max nodes 40, max tokens 150000, pace Sprint).
- `＋ New exploration` opens the dialog with **Depth = Standard**.
- Switching Depth in the dialog updates the budget fields; they remain editable.
- Creating a Quick-scan run lands in the same tree UI at `#/e/<id>`.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/autonomous/ExplorerView.tsx frontend/src/components/autonomous/HomeDashboard.tsx frontend/src/index.css
git commit -m "feat: fold single-area into a Quick-scan depth preset + dashboard CTA"
```

---

## Task 4: Full Playwright verification walkthrough

Per the repo rule, no UI claim stands without a Playwright pass. This task exercises the whole spine and the responsive checks; it produces no code beyond optional dead-CSS cleanup.

**Files:**
- Optional modify: `frontend/src/index.css` (remove now-dead `.mode-switch`, `.ms-option`, `.ms-spark`, `.sidenav*` rules only if confirmed unreferenced)

- [ ] **Step 1: Start the app**

Ensure backend + frontend are running (use the project's usual launcher; the frontend dev server is `npm run dev` from `frontend/`). Note the dev URL (typically `http://127.0.0.1:5173` — target `127.0.0.1`, not `localhost`, per the known IPv6/port-collision note).

- [ ] **Step 2: Drive the spine with Playwright**

Using the Playwright MCP, walk and assert:
1. Navigate to the app root → dashboard renders; usage gauge + exploration grid visible; no mode toggle in the DOM (`.mode-switch` absent).
2. Click a card (or create a Quick-scan run if none exist) → URL hash becomes `#/e/<id>`; breadcrumb `Dashboard › <domain>` present.
3. Click a tree node → hash becomes `#/e/<id>/n/<nodeId>`; breadcrumb shows `› Idea`; no `.inspect-back` button and no `.exp-tab.idea` in the DOM.
4. Click the `<domain>` crumb → back to the tree (hash `#/e/<id>`).
5. Click the `Dashboard` crumb → home (`#/`).
6. `browser_navigate_back` twice → traverses Idea → Exploration → Dashboard in order.
7. Reload on `#/e/<id>/n/<nodeId>` → the same node detail restores (SSE re-hydrates).
8. Open ⌘K, jump to an exploration → hash updates and the tree shows.

- [ ] **Step 3: Responsive + console checks**

At viewports 375, 768, 1440 (both themes): take screenshots of dashboard, exploration, and idea detail. Assert:
- No horizontal overflow (`document.documentElement.scrollWidth <= clientWidth`).
- No console errors (`browser_console_messages`).

- [ ] **Step 4: (Optional) remove confirmed-dead CSS**

Only if `grep` across `frontend/src` confirms zero references: remove `.mode-switch`, `.ms-option`, `.ms-spark` rules (old topbar toggle) and `.sidenav`, `.sidenav-*`, `.sni-ico` rules (unused `SideNav`) from `frontend/src/index.css`. Re-run `npm run build`. If any are still referenced, leave them — dead CSS is harmless and not worth a regression.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "test: verify autonomous-first spine end-to-end (Playwright) + css cleanup"
```

---

## Self-review notes

- **Spec §1 (hash spine):** Task 1 (hook + tests) + Task 2 (App/ExplorerView consume it). ✓
- **Spec §2 (kill duality):** Task 2 rewrites `App.tsx`, removes `mode`, drops single-area flow, keeps ⌘K. ✓
- **Spec §3 (one rail):** Task 2 step 3 removes `SideNav`/mode toggle from the sidebar; Home/New/list route-driven. Brand kept in topbar per Global Constraints (documented deviation from §3's brand-in-rail bullet, consistent with §2). ✓
- **Spec §4 (Quick scan fold-in):** Task 3 adds Depth presets + dashboard ⚡ CTA. ✓
- **Spec §5 (in-project nav cleanup):** Task 2 step 4l adds the breadcrumb, removes the Idea tab-button and inspect-back button; Overview/Nodes tabs kept. ✓
- **Spec testing plan:** Task 4 Playwright walkthrough + responsive/console. ✓
- **Type consistency:** `Route`, `parseHash`, `routeToHash`, `useHashRoute` names match across Task 1 → Task 2. `Depth`/`DEPTH_PRESETS` defined once (Task 3 step 1) and consumed in the same file. `onQuickNew` added to `HomeDashboard` props (Task 3 step 6) matches the call site wired in Task 3 step 2.
- **No placeholders:** every code step shows complete code or an exact before/after.
