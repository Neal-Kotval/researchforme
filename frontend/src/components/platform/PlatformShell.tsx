import { useEffect, useMemo, useState, type ReactNode } from "react";
import { getUsage } from "../../autonomous/api";
import type { GlobalUsage, Project } from "../../autonomous/types";
import type { Route } from "../../hooks/useHashRoute";

/** Which sidebar item a route lights up. */
export type PlatformView = "home" | "explore" | "pressure" | "compare" | "assistant" | "graveyard" | "starred";

export function routeToPlatformView(route: Route): PlatformView {
  switch (route.view) {
    case "explore":
    case "exploration": return "explore";
    case "pressure": return "pressure";
    case "compare": return "compare";
    case "assistant": return "assistant";
    case "graveyard": return "graveyard";
    case "starred": return "starred";
    default: return "home";
  }
}

const VIEW_META: Record<PlatformView, { title: string; sub: string }> = {
  home: { title: "Home", sub: "Ideas that survived the red team, and what the engine is doing right now." },
  explore: { title: "Explore", sub: "The engine is hunting live across signal. Pause anytime — it resumes where it stopped." },
  pressure: { title: "Pressure-test", sub: "Six adversarial lenses attack every candidate before it reaches you." },
  compare: { title: "Compare", sub: "Weigh the survivors by fit × viability. Every score carries its provenance." },
  assistant: { title: "Assistant", sub: "Drive the whole platform in plain language, through its tool layer." },
  graveyard: { title: "Graveyard", sub: "Every space that was killed or passed — and why. Check here before re-chasing an idea." },
  starred: { title: "Starred ideas", sub: "Your shortlist across every exploration — what you starred, not what the engine scored highly. Select ideas to export into a project." },
};

function fmtTok(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return `${Math.round(n)}`;
}

/* --------------------------------------------------------------- icons -- */
const ic = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
const IconHome = () => (<svg {...ic}><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /></svg>);
const IconExplore = () => (<svg {...ic}><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3" /><circle cx="12" cy="12" r="2" /></svg>);
const IconShield = () => (<svg {...ic}><path d="M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6l-7-3Z" /></svg>);
const IconBars = () => (<svg {...ic}><path d="M5 21V10M12 21V4M19 21v-7" /></svg>);
const IconChat = () => (<svg {...ic}><path d="M4 5h16v11H8l-4 4V5Z" /></svg>);
/** Ghost — the graveyard's mark: a domed body with a wavy hem and two eyes. */
const IconGhost = () => (
  <svg {...ic}>
    <path d="M5 20v-9a7 7 0 0 1 14 0v9l-2.3-1.8L14.4 20l-2.4-1.8L9.6 20l-2.3-1.8L5 20Z" />
    <path d="M9.5 10.5h.01" />
    <path d="M14.5 10.5h.01" />
  </svg>
);
const IconStar = () => (<svg {...ic}><path d="m12 4 2.5 5 5.5.8-4 3.9.9 5.5-4.9-2.6-4.9 2.6.9-5.5-4-3.9 5.5-.8L12 4Z" /></svg>);
const IconPlus = () => (<svg {...ic} width={14} height={14} strokeWidth={2.1}><path d="M12 5v14M5 12h14" /></svg>);
const IconChatSm = () => (<svg {...ic} width={14} height={14} strokeWidth={1.8}><path d="M4 5h16v11H8l-4 4V5Z" /></svg>);

/** The rising-signal logo mark (handoff spec). */
function LogoMark() {
  return (
    <div className="pf-logo-mark" aria-hidden>
      <span className="pf-logo-base" />
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
        <path d="M4 16 L10 9 L14 13 L20 5" stroke="var(--highlight)" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="4" cy="16" r="1.5" fill="var(--text-ghost)" />
        <circle cx="10" cy="9" r="1.5" fill="var(--text-ghost)" />
        <circle cx="14" cy="13" r="1.5" fill="var(--text-ghost)" />
        <circle cx="20" cy="5" r="2.6" fill="var(--highlight)" />
      </svg>
    </div>
  );
}

interface Props {
  route: Route;
  /** The App-level projects poll — drives the LIVE tag and agent-live pill. */
  projects: Project[];
  onNav: (view: PlatformView) => void;
  onNewExploration: () => void;
  children: ReactNode;
}

/**
 * The persistent platform shell (design handoff): fixed left sidebar with the
 * FLOW / WORKSPACE nav + usage meter, a per-view top bar with the "agent live"
 * pill and the two global actions, and the dotted-grid content region.
 */
export default function PlatformShell({ route, projects, onNav, onNewExploration, children }: Props) {
  const view = routeToPlatformView(route);
  const meta = VIEW_META[view];

  // The shared governor snapshot for the sidebar usage meter.
  const [usage, setUsage] = useState<GlobalUsage | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = () => getUsage().then((u) => alive && setUsage(u)).catch(() => {});
    tick();
    const id = window.setInterval(tick, 15_000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  const anyRunning = useMemo(() => projects.some((p) => p.status === "running"), [projects]);
  const ratio = usage?.usage_ratio;
  const pct = ratio != null ? Math.min(100, Math.round(ratio * 100)) : null;

  const navItem = (
    key: PlatformView,
    label: string,
    icon: ReactNode,
    extra?: ReactNode
  ) => (
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

        <div className="pf-navlabel">Flow</div>
        <nav className="pf-nav">
          {navItem("home", "Home", <IconHome />)}
          {navItem("explore", "Explore", <IconExplore />,
            anyRunning ? <span className="pf-nav-live">live</span> : undefined)}
          {navItem("pressure", "Pressure-test", <IconShield />)}
          {navItem("compare", "Compare", <IconBars />)}
        </nav>

        <div className="pf-navlabel">Workspace</div>
        <nav className="pf-nav">
          {navItem("starred", "Starred", <IconStar />)}
          {navItem("graveyard", "Graveyard", <IconGhost />)}
          {navItem("assistant", "Assistant", <IconChat />, <span className="pf-kbd">/</span>)}
        </nav>

        <div className="pf-side-spacer" />

        <div className="pf-usage">
          <div className="pf-usage-row">
            <span className="pf-usage-label">Today's usage</span>
            <span className="pf-usage-pct">{pct != null ? `${pct}%` : "—"}</span>
          </div>
          <div className="pf-usage-track">
            <span
              className={`pf-usage-fill${usage && (usage.usage_level === "high" || usage.usage_level === "heavy") ? " hot" : ""}`}
              style={{ width: `${pct ?? 0}%` }}
            />
          </div>
          <div className="pf-usage-cap">
            {usage
              ? `${fmtTok(usage.daily_spent)}${usage.effective_cap ? ` / ${fmtTok(usage.effective_cap)}` : ""} tok · today`
              : "usage unavailable"}
          </div>
        </div>

        <div className="pf-user">
          <div className="pf-avatar">A</div>
          <div>
            <div className="pf-user-name">Solo founder</div>
            <div className="pf-user-sub">1 seat · sprint plan</div>
          </div>
        </div>
      </aside>

      <div className="pf-main">
        <header className="pf-topbar">
          <div style={{ minWidth: 0 }}>
            <div className="pf-topbar-titlerow">
              <h1>{meta.title}</h1>
              {anyRunning && (
                <span className="pf-live-pill">
                  <span className="pf-dot pulse" />agent live
                </span>
              )}
            </div>
            <div className="pf-view-sub">{meta.sub}</div>
          </div>
          <div className="pf-topbar-actions">
            <button className="btn" onClick={() => onNav("assistant")}>
              <IconChatSm />Ask<span className="pf-kbd">/</span>
            </button>
            <button className="btn btn-primary" onClick={onNewExploration}>
              <IconPlus />New exploration<span className="pf-kbd-dark">N</span>
            </button>
          </div>
        </header>
        {children}
      </div>
    </div>
  );
}
