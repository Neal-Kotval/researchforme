import { useCallback, useEffect, useState } from "react";
import { createProject } from "./autonomous/api";
import type { CreateProjectRequest, ScoutCandidate } from "./autonomous/types";
import { useHashRoute } from "./hooks/useHashRoute";
import { useProjects } from "./hooks/useProjects";
import CommandPalette from "./components/CommandPalette";
import ExplorerView from "./components/autonomous/ExplorerView";
import NewExplorationDialog, { type DialogPrefill } from "./components/autonomous/NewExplorationDialog";
import PlatformShell from "./components/platform/PlatformShell";
import HomeView from "./components/platform/HomeView";
import ExploreView from "./components/platform/ExploreView";
import PressureTestView from "./components/platform/PressureTestView";
import CompareView from "./components/platform/CompareView";
import GraveyardView from "./components/platform/GraveyardView";
import StarredView from "./components/platform/StarredView";
import LibraryView from "./components/platform/LibraryView";
import AssistantView from "./components/platform/AssistantView";

/** True while the user is typing somewhere a bare-key shortcut must not fire. */
function typingInField(): boolean {
  const el = document.activeElement as HTMLElement | null;
  if (!el) return false;
  return (
    el.tagName === "INPUT" ||
    el.tagName === "TEXTAREA" ||
    el.tagName === "SELECT" ||
    el.isContentEditable
  );
}

export default function App() {
  const { route, navHome, navProject, navNode, navView } = useHashRoute();
  const { projects, refresh } = useProjects();
  const [paletteOpen, setPaletteOpen] = useState(false);

  // The one New-exploration drawer, owned here so every screen launches the
  // same component (topbar button, N, ⌘K, scout prefills, empty-state CTAs).
  const [dialog, setDialog] = useState<{ prefill: DialogPrefill | null } | null>(null);
  const openNew = useCallback(() => setDialog({ prefill: null }), []);
  const openScoutPrefill = useCallback((c: ScoutCandidate) => {
    setDialog({
      prefill: { domain: c.domain, segs: c.suggested_sub_segments, brief: c.rationale },
    });
  }, []);
  const onCreate = async (req: CreateProjectRequest) => {
    const p = await createProject(req);
    setDialog(null);
    await refresh();
    navProject(p.id);
  };

  // ⌘K palette; bare N = new exploration; bare / = assistant (design handoff).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || typingInField() || paletteOpen || dialog) return;
      if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        openNew();
      } else if (e.key === "/") {
        e.preventDefault();
        navView("assistant");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openNew, navView, paletteOpen, dialog]);

  return (
    <>
      <PlatformShell route={route} projects={projects} onNav={navView} onNewExploration={openNew}>
        <main className="pf-content">
          {route.view === "home" && (
            <HomeView
              projects={projects}
              onOpenProject={navProject}
              onOpenNode={navNode}
              onNewExploration={openNew}
              onExploreCandidate={openScoutPrefill}
            />
          )}
          {route.view === "explore" && (
            <ExploreView
              projects={projects}
              onOpenProject={navProject}
              onOpenNode={navNode}
              onNewExploration={openNew}
            />
          )}
          {route.view === "exploration" && (
            <div className="shell explorer-shell">
              <ExplorerView
                route={route}
                navHome={navHome}
                navProject={navProject}
                navNode={navNode}
              />
            </div>
          )}
          {route.view === "pressure" && (
            <PressureTestView onOpenNode={navNode} onNewExploration={openNew} />
          )}
          {route.view === "compare" && (
            <CompareView onOpenNode={navNode} onNewExploration={openNew} />
          )}
          {route.view === "starred" && (
            <StarredView
              onOpenNode={navNode}
              onImported={(slug) => { window.location.hash = `#/library/${encodeURIComponent(slug)}`; }}
            />
          )}
          {route.view === "library" && (
            <LibraryView
              slug={route.slug}
              onOpenProject={(slug) => {
                window.location.hash = slug ? `#/library/${encodeURIComponent(slug)}` : "#/library";
              }}
            />
          )}
          {route.view === "graveyard" && (
            <GraveyardView onOpenNode={navNode} />
          )}
          {route.view === "assistant" && (
            <AssistantView onNav={navView} onActed={refresh} />
          )}
        </main>
      </PlatformShell>

      {dialog && (
        <NewExplorationDialog
          initialDepth="standard"
          prefill={dialog.prefill}
          onClose={() => setDialog(null)}
          onCreate={onCreate}
        />
      )}

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        ctx={{
          jumpProject: (pid) => navProject(pid),
          newExploration: openNew,
        }}
      />
    </>
  );
}
