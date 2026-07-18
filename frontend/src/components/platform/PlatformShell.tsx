import { useMemo, type ReactNode } from "react";
import type { Project } from "../../autonomous/types";
import type { Route } from "../../hooks/useHashRoute";
import TokenPaceFooter from "./TokenPaceFooter";

/** The consolidated nav: three destinations. Everything else folds into these. */
export type PlatformView = "home" | "explore" | "library";

export function routeToPlatformView(route: Route): PlatformView {
  switch (route.view) {
    case "explore":
    case "exploration":
    case "autonomous":
    case "pressure":
    case "compare": return "explore";
    case "library":
    case "starred":
    case "graveyard": return "library";
    case "assistant":
    default: return "home"; // assistant folds into Home's composer (Ask mode)
  }
}

const VIEW_META: Record<PlatformView, { title: string; sub: string }> = {
  home: { title: "Home", sub: "" },
  explore: { title: "Explore", sub: "Hunt live, run autonomously, red-team, and compare — all in one place." },
  library: { title: "Library", sub: "Your ideas and the graveyard — everything the engine surfaced or buried." },
};

/* --------------------------------------------------------------- icons -- */
const ic = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
const IconHome = () => (<svg {...ic}><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /></svg>);
const IconExplore = () => (<svg {...ic}><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3" /><circle cx="12" cy="12" r="2" /></svg>);
const IconLibrary = () => (<svg {...ic}><path d="M4 5h6v14H4zM14 5h6v14h-6" /><path d="M4 9h6M14 9h6" /></svg>);
const IconPlus = () => (<svg {...ic} width={14} height={14} strokeWidth={2.1}><path d="M12 5v14M5 12h14" /></svg>);
const IconChatSm = () => (<svg {...ic} width={14} height={14} strokeWidth={1.8}><path d="M4 5h16v11H8l-4 4V5Z" /></svg>);

/** Gap Finder mark — the bold north-east arrow (from the brand logo), ink tile. */
function LogoMark() {
  return (
    <div className="pf-logo-mark" aria-hidden>
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none"
        stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M7 17 17 7" /><path d="M8.5 7H17v8.5" />
      </svg>
    </div>
  );
}

interface Props {
  route: Route;
  projects: Project[];
  onNav: (view: PlatformView) => void;
  onAsk: () => void;
  onNewExploration: () => void;
  children: ReactNode;
}

/**
 * The persistent shell: a Claude-style sidebar with three destinations
 * (Home · Explore · Library) and the token-pace footer. Home is a full-bleed
 * hero (no top bar); Explore and Library carry a light title bar with the two
 * global actions.
 */
export default function PlatformShell({ route, projects, onNav, onAsk, onNewExploration, children }: Props) {
  const view = routeToPlatformView(route);
  const meta = VIEW_META[view];
  const fullscreen = route.view === "library" && !!route.slug;
  const bareHero = view === "home";
  const anyRunning = useMemo(() => projects.some((p) => p.status === "running"), [projects]);

  const navItem = (key: PlatformView, label: string, icon: ReactNode, extra?: ReactNode) => (
    <button
      className={`pf-nav-item${view === key ? " active" : ""}`}
      onClick={() => onNav(key)}
      title={label}
      aria-current={view === key ? "page" : undefined}
    >
      {icon}
      <span className="pf-txt">{label}</span>
      {extra}
    </button>
  );

  return (
    <div className="pf-app">
      <aside className="pf-sidebar">
        <div className="pf-logo">
          <LogoMark />
          <div>
            <div className="pf-logo-name">Gap Finder</div>
            <div className="pf-logo-sub">v3 · light</div>
          </div>
        </div>

        <div className="pf-side-scroll">
          <nav className="pf-nav">
            {navItem("home", "Home", <IconHome />)}
            {navItem("explore", "Explore", <IconExplore />,
              anyRunning ? <span className="pf-nav-live">live</span> : undefined)}
            {navItem("library", "Library", <IconLibrary />)}
          </nav>
        </div>

        <TokenPaceFooter />
      </aside>

      <div className={`pf-main${fullscreen ? " pf-fullscreen" : ""}`}>
        {!fullscreen && !bareHero && (
          <header className="pf-topbar">
            <div style={{ minWidth: 0 }}>
              <div className="pf-topbar-titlerow">
                <h1>{meta.title}</h1>
                {anyRunning && (
                  <span className="pf-live-pill"><span className="pf-dot pulse" />agent live</span>
                )}
              </div>
              {meta.sub && <div className="pf-view-sub">{meta.sub}</div>}
            </div>
            <div className="pf-topbar-actions">
              <button className="btn" onClick={onAsk}>
                <IconChatSm />Ask<span className="pf-kbd">/</span>
              </button>
              <button className="btn btn-primary" onClick={onNewExploration}>
                <IconPlus />New exploration<span className="pf-kbd-dark">N</span>
              </button>
            </div>
          </header>
        )}
        {children}
      </div>
    </div>
  );
}
