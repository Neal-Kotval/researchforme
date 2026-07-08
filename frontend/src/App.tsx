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
