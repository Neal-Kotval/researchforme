import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { analyze, rerank, ApiError } from "./api";
import {
  DEFAULT_WEIGHTS,
  DEFAULT_MODEL,
  modelLabel,
  type GapReport,
  type SourceName,
  type SourceStatusKind,
  type Weights,
} from "./types";
import AreaInput from "./components/AreaInput";
import SourceStatus from "./components/SourceStatus";
import OpportunityMap from "./components/OpportunityMap";
import GapTable from "./components/GapTable";
import GapDetail from "./components/GapDetail";
import WeightControls from "./components/WeightControls";
import ModelPicker from "./components/ModelPicker";
import ExplorerView from "./components/autonomous/ExplorerView";
import CommandPalette from "./components/CommandPalette";

type Phase = "idle" | "loading" | "ready" | "error";
type Mode = "single" | "autonomous";

interface Query {
  area: string;
  subSegments: string[];
}

export default function App() {
  const [mode, setMode] = useState<Mode>("single");
  const [phase, setPhase] = useState<Phase>("idle");
  const [report, setReport] = useState<GapReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState<Query | null>(null);
  const [weights, setWeights] = useState<Weights>({ ...DEFAULT_WEIGHTS });
  const [model, setModel] = useState<string>(DEFAULT_MODEL);
  const [selectedTitle, setSelectedTitle] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [reweighting, setReweighting] = useState(false);

  // Theme (light default, dark toggle) — persisted; applied to <html data-theme>.
  const [theme, setTheme] = useState<"light" | "dark">(
    () => (document.documentElement.getAttribute("data-theme") as "light" | "dark") || "light"
  );
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("mgf-theme", theme);
  }, [theme]);

  // ⌘K command palette + the signals it uses to drive the autonomous view.
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [focusProjectId, setFocusProjectId] = useState<string | null>(null);
  const [newExplorationSignal, setNewExplorationSignal] = useState(0);

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

  // Selecting a gap highlights it on the map + table AND opens the detail drawer.
  const selectGap = useCallback((title: string) => {
    setSelectedTitle(title);
    setDetailOpen(true);
  }, []);

  const rerankTimer = useRef<number | null>(null);
  const runSeq = useRef(0); // guards against out-of-order responses

  /* ------------------------------------------------------------- analyze -- */
  const runAnalyze = useCallback(
    async (area: string, subSegments: string[], w: Weights, refresh: boolean, m: string) => {
      const seq = ++runSeq.current;
      setPhase("loading");
      setError(null);
      setQuery({ area, subSegments });
      setSelectedTitle(null);
      if (!refresh) setReport(null);
      try {
        const rep = await analyze({ area, subSegments, weights: w, refresh, model: m });
        if (seq !== runSeq.current) return; // superseded
        setReport(rep);
        setWeights(rep.weights ?? w);
        // Pre-highlight the winner on the map, but don't cover it with the drawer.
        setSelectedTitle(rep.gaps.find((g) => g.rank === 1)?.gap.title ?? null);
        setDetailOpen(false);
        setPhase("ready");
      } catch (e) {
        if (seq !== runSeq.current) return;
        setError(e instanceof ApiError ? e.message : "Something went wrong while analyzing.");
        setPhase("error");
      }
    },
    []
  );

  const onSubmit = (area: string, subSegments: string[]) =>
    runAnalyze(area, subSegments, weights, false, model);

  const onRerun = () => {
    if (query) runAnalyze(query.area, query.subSegments, weights, true, model);
  };

  const onRetry = () => {
    if (query) runAnalyze(query.area, query.subSegments, weights, false, model);
  };

  const onNewSearch = () => {
    runSeq.current++;
    setPhase("idle");
    setReport(null);
    setQuery(null);
    setError(null);
    setSelectedTitle(null);
    setDetailOpen(false);
    setWeights({ ...DEFAULT_WEIGHTS });
  };

  /* ------------------------------------------------------- reweight (live) -- */
  const applyWeights = useCallback(
    (w: Weights) => {
      setWeights(w);
      if (!query || phase !== "ready") return;
      if (rerankTimer.current) window.clearTimeout(rerankTimer.current);
      rerankTimer.current = window.setTimeout(async () => {
        const seq = runSeq.current; // don't bump; reweight shouldn't cancel itself vs analyze
        setReweighting(true);
        try {
          const rep = await rerank(query.area, query.subSegments, w, model);
          if (seq !== runSeq.current) return;
          setReport(rep);
        } catch (e) {
          // No cached synthesis to reweight → fall back to a full (cached-ingest) run.
          if (e instanceof ApiError && (e.status === 404 || e.status === 400)) {
            try {
              const rep = await analyze({ area: query.area, subSegments: query.subSegments, weights: w, model });
              if (seq !== runSeq.current) return;
              setReport(rep);
            } catch {
              /* keep prior report; sliders still reflect intent */
            }
          }
        } finally {
          setReweighting(false);
        }
      }, 260);
    },
    [query, phase, model]
  );

  useEffect(() => () => {
    if (rerankTimer.current) window.clearTimeout(rerankTimer.current);
  }, []);

  /* --------------------------------------------------------------- derived -- */
  const sourceStatusMap = useMemo(() => {
    const m: Record<SourceName, SourceStatusKind | undefined> = {
      reddit: undefined,
      arxiv: undefined,
      hackernews: undefined,
      github: undefined,
      newsletter: undefined,
    };
    report?.sources.forEach((s) => (m[s.name] = s.status));
    return m;
  }, [report]);

  const selectedRanked = useMemo(
    () => report?.gaps.find((g) => g.gap.title === selectedTitle) ?? null,
    [report, selectedTitle]
  );

  const generatedAt = report ? new Date(report.generated_at) : null;
  const genLabel = generatedAt && !Number.isNaN(generatedAt.getTime())
    ? generatedAt.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : null;

  const showWorkspace = phase === "loading" || phase === "ready" || phase === "error";

  /* ------------------------------------------------------------------ view -- */
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
          <div className="mode-switch" role="tablist" aria-label="Mode">
            <button
              role="tab"
              aria-selected={mode === "single"}
              className={`ms-option${mode === "single" ? " active" : ""}`}
              onClick={() => setMode("single")}
            >
              Single area
            </button>
            <button
              role="tab"
              aria-selected={mode === "autonomous"}
              className={`ms-option${mode === "autonomous" ? " active" : ""}`}
              onClick={() => setMode("autonomous")}
            >
              <span className="ms-spark">✦</span> Autonomous
            </button>
          </div>
          <div className="topbar-spacer" />
          <button
            className="theme-toggle"
            onClick={() => setTheme((t) => (t === "light" ? "dark" : "light"))}
            title={theme === "light" ? "Switch to dark" : "Switch to light"}
            aria-label="Toggle theme"
          >
            {theme === "light" ? "◐" : "◑"}
          </button>
          {mode === "single" && report && (
            <div className="meta-row">
              <span className={`meta-pill ${report.cache_hit ? "hit" : "miss"}`}>
                <span className="dot" />
                {report.cache_hit ? "cache hit" : "fresh fetch"}
              </span>
              <span className="meta-pill" title={report.model || undefined}>
                <span className="dot" /> {report.model ? modelLabel(report.model) : "—"}
                <b style={{ fontWeight: 500, color: "var(--text-faint)" }}>· {report.llm_mode}</b>
              </span>
              {genLabel && <span className="meta-pill">{genLabel}</span>}
            </div>
          )}
          {mode === "single" && query && (
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <ModelPicker value={model} onChange={setModel} compact disabled={phase === "loading"} />
              <button className="btn btn-ghost btn-sm" onClick={onRerun} disabled={phase === "loading"} title="Re-run with this model and a fresh fetch">
                ↻ Re-run
              </button>
              <button className="btn btn-sm" onClick={onNewSearch}>
                New search
              </button>
            </div>
          )}
        </div>
      </header>

      {mode === "autonomous" && (
        <main className="shell explorer-shell">
          <ExplorerView focusProjectId={focusProjectId} newExplorationSignal={newExplorationSignal} />
        </main>
      )}

      {mode === "single" && (
      <main className="shell">
        {phase === "idle" && (
          <AreaInput onSubmit={onSubmit} loading={false} model={model} onModelChange={setModel} />
        )}

        {showWorkspace && (
          <>
            {/* query heading */}
            <div className="section-head" style={{ marginTop: 26 }}>
              <div className="titles">
                <div className="eyebrow">Analysis</div>
                <h2>
                  {query?.area}
                  {query?.subSegments.length ? (
                    <span style={{ color: "var(--text-faint)", fontWeight: 400, fontSize: 15 }}>
                      {" "}· {query.subSegments.join(", ")}
                    </span>
                  ) : null}
                </h2>
                <p>
                  {phase === "loading"
                    ? "Mining sources and synthesizing candidate gaps…"
                    : report
                    ? `${report.gaps.length} ranked openings across ${report.sources.length} sources`
                    : ""}
                </p>
              </div>
            </div>

            <SourceStatus reports={report?.sources ?? []} loading={phase === "loading"} />

            {report?.warnings?.length ? (
              <div className="warnings">
                <span className="w-ico">⚠︎</span>
                <span>{report.warnings.join(" · ")}</span>
              </div>
            ) : null}

            {/* ERROR */}
            {phase === "error" && (
              <div className="card" style={{ marginTop: 20 }}>
                <div className="state-wrap error">
                  <div className="state-ico">⚠︎</div>
                  <div className="state-title">Analysis failed</div>
                  <div className="state-desc">{error}</div>
                  <div className="state-actions">
                    <button className="btn btn-primary" onClick={onRetry}>
                      Try again
                    </button>
                    <button className="btn" onClick={onNewSearch}>
                      New search
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* LOADING */}
            {phase === "loading" && (
              <>
                <div className="section-head">
                  <div className="titles">
                    <div className="eyebrow">Opportunity map</div>
                    <h2>Where the openings are</h2>
                  </div>
                </div>
                <div className="card map-card">
                  <div className="loading-map">
                    <div className="orbit" />
                    <div className="lm-text">Plotting demand against competitive openness…</div>
                  </div>
                </div>
                <div className="lower-grid" style={{ marginTop: 20 }}>
                  <div className="card table-card">
                    <div className="skel-rows">
                      {Array.from({ length: 5 }).map((_, i) => (
                        <div className="skel skel-row" key={i} />
                      ))}
                    </div>
                  </div>
                  <div className="card weights-card">
                    <div className="skel" style={{ height: 18, width: "50%", marginBottom: 16 }} />
                    {Array.from({ length: 5 }).map((_, i) => (
                      <div className="skel" key={i} style={{ height: 28, marginBottom: 14 }} />
                    ))}
                  </div>
                </div>
              </>
            )}

            {/* READY */}
            {phase === "ready" && report && report.gaps.length > 0 && (
              <>
                <div style={{ marginTop: 22 }}>
                  <OpportunityMap gaps={report.gaps} selectedTitle={selectedTitle} onSelect={selectGap} />
                </div>

                <div className="section-head">
                  <div className="titles">
                    <div className="eyebrow">Ranked openings</div>
                    <h2>The shortlist</h2>
                    <p>Sort by any dimension · click a row for the full case</p>
                  </div>
                </div>

                <div className="lower-grid">
                  <GapTable
                    gaps={report.gaps}
                    selectedTitle={selectedTitle}
                    onSelect={selectGap}
                    sourceStatus={sourceStatusMap}
                  />
                  <WeightControls weights={weights} onChange={applyWeights} reweighting={reweighting} />
                </div>
              </>
            )}

            {phase === "ready" && report && report.gaps.length === 0 && (
              <div className="card" style={{ marginTop: 20 }}>
                <div className="state-wrap">
                  <div className="state-ico">🕳️</div>
                  <div className="state-title">No gaps surfaced</div>
                  <div className="state-desc">
                    The synthesis returned no candidate openings for this area. Try a broader area or a fresh re-run.
                  </div>
                  <div className="state-actions">
                    <button className="btn btn-primary" onClick={onRerun}>
                      ↻ Re-run
                    </button>
                    <button className="btn" onClick={onNewSearch}>
                      New search
                    </button>
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        <div className="footer">
          Market Gap Finder · signals from Reddit, Hacker News, arXiv, GitHub &amp; tech newsletters · gaps are hypotheses, not advice.
        </div>
      </main>
      )}

      {mode === "single" && (
        <GapDetail ranked={detailOpen ? selectedRanked : null} onClose={() => setDetailOpen(false)} />
      )}

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        ctx={{
          mode,
          setMode,
          jumpProject: (pid) => {
            setMode("autonomous");
            setFocusProjectId(pid);
          },
          newExploration: () => {
            setMode("autonomous");
            setNewExplorationSignal((n) => n + 1);
          },
        }}
      />
    </div>
  );
}
