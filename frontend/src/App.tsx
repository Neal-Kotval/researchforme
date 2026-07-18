import { useCallback, useEffect, useState } from "react";
import { createProject } from "./autonomous/api";
import type { CreateProjectRequest, ScoutCandidate } from "./autonomous/types";
import { useHashRoute } from "./hooks/useHashRoute";
import { useProjects } from "./hooks/useProjects";
import { Mounted } from "./motion";
import CommandPalette from "./components/CommandPalette";
import ExplorerView from "./components/autonomous/ExplorerView";
import NewExplorationDialog, { type DialogPrefill } from "./components/autonomous/NewExplorationDialog";
import PlatformShell, { routeToPlatformView } from "./components/platform/PlatformShell";
import HomeView from "./components/platform/HomeView";
import ExploreWorkspace, { type ExploreTab } from "./components/platform/ExploreWorkspace";
import LibraryWorkspace from "./components/platform/LibraryWorkspace";

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
  const { route, navHome, navProject, navNode, navMode, navView } = useHashRoute();
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

  // Consolidated routing: every hash resolves into Home / Explore / Library.
  const section = routeToPlatformView(route);
  const exploreTab: ExploreTab =
    route.view === "autonomous" ? "autonomous"
    : route.view === "pressure" ? "pressure"
    : route.view === "compare" ? "compare"
    : "live";
  const homeMode = route.view === "assistant" ? "ask" : "explore";
  const librarySlug = route.view === "library" ? route.slug : null;
  const openLibrary = (slug: string | null) => {
    window.location.hash = slug ? `#/library/${encodeURIComponent(slug)}` : "#/library";
  };

  return (
    <>
      <PlatformShell route={route} projects={projects} onNav={navView}
        onAsk={() => navView("assistant")} onNewExploration={openNew}>
        <main className="pf-content">
          <Mounted k={section} className="pf-view-fade">
            {section === "home" && (
              <HomeView
                projects={projects}
                mode={homeMode}
                onOpenProject={navProject}
                onOpenNode={navNode}
                onNewExploration={openNew}
                onExploreCandidate={openScoutPrefill}
                onNav={navView}
                onActed={refresh}
              />
            )}
            {section === "explore" && route.view === "exploration" && (
              <div className="shell explorer-shell">
                <ExplorerView route={route} navHome={navHome} navProject={navProject} navNode={navNode} navMode={navMode} />
              </div>
            )}
            {section === "explore" && route.view !== "exploration" && (
              <ExploreWorkspace
                projects={projects}
                tab={exploreTab}
                onTab={(t) => navView(t === "live" ? "explore" : t)}
                onOpenProject={navProject}
                onOpenNode={navNode}
                onNewExploration={openNew}
              />
            )}
            {section === "library" && (
              <LibraryWorkspace
                slug={librarySlug}
                onOpenProject={openLibrary}
                onOpenNode={navNode}
                onImported={(slug) => openLibrary(slug)}
              />
            )}
          </Mounted>
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
