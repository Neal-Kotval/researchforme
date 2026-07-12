import { useCallback, useEffect, useState } from "react";
import { useHashRoute } from "./hooks/useHashRoute";
import ExplorerView from "./components/autonomous/ExplorerView";
import CommandPalette from "./components/CommandPalette";
import PlatformShell from "./components/platform/PlatformShell";
import PressureTestView from "./components/platform/PressureTestView";
import CompareView from "./components/platform/CompareView";
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
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [newExplorationSignal, setNewExplorationSignal] = useState(0);

  // The explorer engine (project store + SSE streams) stays mounted across all
  // views; the flat platform screens render beside it and it simply hides.
  const flatView =
    route.view === "pressure" || route.view === "compare" || route.view === "assistant"
      ? route.view
      : null;

  // "New exploration" opens the drawer inside the explorer — surface it first.
  const newExploration = useCallback(() => {
    if (window.location.hash !== "#/" && !window.location.hash.startsWith("#/e/")) navHome();
    setNewExplorationSignal((n) => n + 1);
  }, [navHome]);

  // ⌘K palette; bare N = new exploration; bare / = assistant (design handoff).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || typingInField() || paletteOpen) return;
      if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        newExploration();
      } else if (e.key === "/") {
        e.preventDefault();
        navView("assistant");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [newExploration, navView, paletteOpen]);

  return (
    <>
      <PlatformShell
        route={route}
        onNav={navView}
        onExplore={(latestActivePid) =>
          latestActivePid ? navProject(latestActivePid) : navHome()
        }
        onNewExploration={newExploration}
      >
        <main className={`pf-content${flatView ? " hidden" : ""}`}>
          <div className={`shell explorer-shell${route.view === "home" ? " on-home" : ""}`}>
            <ExplorerView
              route={route}
              navHome={navHome}
              navProject={navProject}
              navNode={navNode}
              newExplorationSignal={newExplorationSignal}
            />
          </div>
        </main>
        {flatView && (
          <main className="pf-content">
            {flatView === "pressure" && (
              <PressureTestView onOpenNode={navNode} onNewExploration={newExploration} />
            )}
            {flatView === "compare" && (
              <CompareView onOpenNode={navNode} onNewExploration={newExploration} />
            )}
            {flatView === "assistant" && (
              <AssistantView onNav={navView} onNewExploration={newExploration} />
            )}
          </main>
        )}
      </PlatformShell>

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        ctx={{
          jumpProject: (pid) => navProject(pid),
          newExploration,
        }}
      />
    </>
  );
}
