import { useEffect, useMemo, useRef, useState } from "react";
import { getTree, scout, ApiError } from "../../autonomous/api";
import {
  fitViabScore,
  nodeTrust,
  type Project,
  type ScoutCandidate,
  type TreeNode,
} from "../../autonomous/types";
import { statusMeta } from "./statusMeta";
import FitChip from "./FitChip";
import ViabChip from "./ViabChip";

interface Props {
  projects: Project[];
  onOpen: (pid: string) => void;
  onOpenNode: (pid: string, nodeId: string) => void;
  onNew: () => void;
  onQuickNew: () => void;
  /** Launch the new-exploration drawer prefilled from a scout candidate. */
  onExploreCandidate: (c: ScoutCandidate) => void;
}

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

const ACTIVE_STATUSES = new Set(["running", "paused", "usage_paused", "milestone_paused"]);

/** The teaching pipeline (memo §3) — rendered on first run and as the page sub. */
const PIPELINE: { step: string; line: string }[] = [
  { step: "Brief", line: "Tell it a domain — and who you are, so fit means something." },
  { step: "Explore", line: "It maps the space into segments and hunts for openings." },
  { step: "Pressure-test", line: "Six adversarial lenses try to kill every idea." },
  { step: "Compare", line: "Survivors are ranked by viability × founder fit." },
  { step: "Take away", line: "You leave with a shortlist worth your next move." },
];

interface HeroIdea {
  node: TreeNode;
  pid: string;
  domain: string;
}

/**
 * The home dashboard, rebuilt to the memo §4 IA: the machine's findings first
 * (Worth your attention), then what it is doing (Active exploration), then
 * where to look next (Suggested spaces / Scout). Usage stays demoted to the
 * global footer bar — cost is never the hero.
 */
export default function HomeDashboard({
  projects,
  onOpen,
  onOpenNode,
  onNew,
  onQuickNew,
  onExploreCandidate,
}: Props) {
  /* ------------------------------------------- hero: ideas across projects -- */
  // Lazily fetch the trees of the top few projects (by best viability) via the
  // one-shot REST snapshot — no SSE stream per project. Cache keyed by pid +
  // gap/star counts so a run that found something new re-syncs on the next poll.
  const [trees, setTrees] = useState<Record<string, TreeNode[]>>({});
  const [heroLoading, setHeroLoading] = useState(false);
  const fetchedKeys = useRef<Map<string, string>>(new Map());

  const heroSources = useMemo(
    () =>
      projects
        .filter((p) => p.stats.gaps > 0 || p.stats.candidates > 0)
        .sort((a, b) => b.stats.max_viability - a.stats.max_viability)
        .slice(0, 3),
    [projects]
  );
  const heroSig = heroSources
    .map((p) => `${p.id}:${p.stats.gaps}:${p.stats.stars}:${p.stats.candidates}`)
    .join("|");

  useEffect(() => {
    let alive = true;
    const stale = heroSources.filter((p) => {
      const key = `${p.stats.gaps}:${p.stats.stars}:${p.stats.candidates}`;
      return fetchedKeys.current.get(p.id) !== key;
    });
    if (stale.length === 0) return;
    if (Object.keys(trees).length === 0) setHeroLoading(true);
    (async () => {
      const entries = await Promise.all(
        stale.map(async (p) => {
          try {
            const snap = await getTree(p.id);
            fetchedKeys.current.set(
              p.id,
              `${p.stats.gaps}:${p.stats.stars}:${p.stats.candidates}`
            );
            return [p.id, snap.nodes] as const;
          } catch {
            return null; // keep whatever we had; next poll retries
          }
        })
      );
      if (!alive) return;
      setTrees((prev) => {
        const next = { ...prev };
        for (const e of entries) if (e) next[e[0]] = e[1];
        return next;
      });
      setHeroLoading(false);
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [heroSig]);

  const heroIdeas: HeroIdea[] = useMemo(() => {
    const out: HeroIdea[] = [];
    for (const p of heroSources) {
      for (const n of trees[p.id] ?? []) {
        if ((n.kind === "gap" || n.kind === "gap_candidate") && n.viability != null) {
          // "Survived the red team" is the hero's promise (memo §4) — a killed
          // or zero-viability idea never deserves the top of the dashboard.
          const pt = n.pressure_test;
          const killed = pt != null && pt.lenses.length > 0 && pt.survived === 0;
          if (n.viability <= 0 || killed) continue;
          out.push({ node: n, pid: p.id, domain: p.domain });
        }
      }
    }
    // One card per distinct idea — runs re-find the same gap on several
    // branches (and sibling runs can converge on it too). Prefer the instance
    // that carries a fit score (it knows more), then the stronger one; rank
    // the survivors by fit × viability.
    const byTitle = new Map<string, HeroIdea>();
    for (const h of out) {
      const key = (h.node.gap?.title ?? h.node.title).toLowerCase();
      const prev = byTitle.get(key);
      if (
        !prev ||
        (h.node.fit != null && prev.node.fit == null) ||
        (Boolean(h.node.fit != null) === Boolean(prev.node.fit != null) &&
          fitViabScore(h.node) > fitViabScore(prev.node))
      ) {
        byTitle.set(key, h);
      }
    }
    return [...byTitle.values()]
      .sort(
        (a, b) =>
          fitViabScore(b.node) - fitViabScore(a.node) ||
          (b.node.star ? 1 : 0) - (a.node.star ? 1 : 0)
      )
      .slice(0, 6);
  }, [trees, heroSources]);

  /* ----------------------------------------------------------------- scout -- */
  const [scoutBrief, setScoutBrief] = useState("");
  const [scoutState, setScoutState] = useState<"idle" | "loading" | "ready" | "error">(
    "idle"
  );
  const [scoutErr, setScoutErr] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<ScoutCandidate[]>([]);

  const runScout = async () => {
    setScoutState("loading");
    setScoutErr(null);
    try {
      const res = await scout(scoutBrief.trim());
      setCandidates(res.candidates);
      setScoutState("ready");
    } catch (e) {
      setScoutErr(
        e instanceof ApiError ? e.message : "Scout failed — the backend did not answer."
      );
      setScoutState("error");
    }
  };

  /* ---------------------------------------------------------------- groups -- */
  const active = useMemo(
    () =>
      projects
        .filter((p) => ACTIVE_STATUSES.has(p.status))
        .sort((a, b) => {
          const r = (b.status === "running" ? 1 : 0) - (a.status === "running" ? 1 : 0);
          return r !== 0 ? r : (b.updated_at || "").localeCompare(a.updated_at || "");
        }),
    [projects]
  );
  const finished = useMemo(
    () =>
      projects
        .filter((p) => !ACTIVE_STATUSES.has(p.status))
        .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || "")),
    [projects]
  );

  /* -------------------------------------------------------------- first run -- */
  if (projects.length === 0) {
    return (
      <div className="home-dash">
        <div className="home-head">
          <div>
            <div className="eyebrow">Autonomous mode</div>
            <h2>Gap Finder</h2>
            <div className="mod-sub">The machine hunts; you decide.</div>
          </div>
          <div className="home-head-actions">
            <button className="btn" onClick={onQuickNew}>⚡ Quick scan</button>
            <button className="btn btn-primary" onClick={onNew}>＋ New exploration</button>
          </div>
        </div>

        <div className="card pipeline rise" style={{ animationDelay: "0ms" }}>
          <div className="pipe-row">
            {PIPELINE.map((s, i) => (
              <div className="pipe-step" key={s.step}>
                <div className="pipe-top">
                  <span className="pipe-n">{i + 1}</span>
                  <span className="pipe-name">{s.step}</span>
                  {i < PIPELINE.length - 1 && <span className="pipe-arrow" aria-hidden>→</span>}
                </div>
                <div className="pipe-line">{s.line}</div>
              </div>
            ))}
          </div>
          <div className="pipe-cta">
            <button className="btn btn-primary" onClick={onNew}>Start your first exploration ▸</button>
            <span className="pipe-cta-sub">
              Point it at any domain and walk away — it explores, red-teams, and ranks
              while you do something else.
            </span>
          </div>
        </div>

        {renderScoutModule()}
      </div>
    );
  }

  /* --------------------------------------------------------------- modules -- */
  function renderScoutModule() {
    return (
      <div className="module scout-module">
        <div className="mod-head">
          <div className="mod-title">Suggested spaces</div>
          <div className="mod-sub">
            Domains the engine proposes from what is hot right now — each shows the
            signals that triggered it. Launch one and it lands prefilled in the drawer.
          </div>
        </div>

        <div className="scout-bar">
          <input
            className="scout-brief"
            placeholder="Optional: who you are / what you're good at — sharpens the suggestions"
            value={scoutBrief}
            onChange={(e) => setScoutBrief(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && scoutState !== "loading" && runScout()}
          />
          <button
            className="btn btn-primary scout-go"
            onClick={runScout}
            disabled={scoutState === "loading"}
          >
            {scoutState === "loading" ? "Scouting…" : "◎ Scout for spaces"}
          </button>
        </div>

        {scoutState === "loading" && (
          <div className="scout-row" aria-hidden>
            {[0, 1, 2].map((i) => (
              <div className="card scout-card skel" key={i} style={{ animationDelay: `${i * 40}ms` }}>
                <div className="skel-line w70" />
                <div className="skel-line w95" />
                <div className="skel-line w85" />
                <div className="skel-line w40 tall" />
              </div>
            ))}
          </div>
        )}

        {scoutState === "error" && (
          <div className="scout-note error" role="alert">
            <span className="w-ico">⚠︎</span> {scoutErr}
            <button className="btn scout-retry" onClick={runScout}>Retry</button>
          </div>
        )}

        {scoutState === "ready" && candidates.length === 0 && (
          <div className="scout-note">
            No ownable spaces surfaced from the current signals. Add a line about
            yourself above and scout again — steering narrows the hunt.
          </div>
        )}

        {scoutState === "ready" && candidates.length > 0 && (
          <div className="scout-row">
            {candidates.map((c, i) => (
              <div className="card scout-card rise" key={c.domain + i} style={{ animationDelay: `${i * 40}ms` }}>
                <div className="scout-title">
                  {c.domain}
                  {c.degraded && (
                    <span
                      className="degraded-badge"
                      title="The LLM was unreachable — this grouping came from a deterministic fallback, not a reasoned pass."
                    >
                      rough clustering — LLM unavailable
                    </span>
                  )}
                </div>
                <p className="scout-why">{c.rationale}</p>
                {c.signals.length > 0 && (
                  <div className="scout-sigs">
                    {c.signals.map((s, j) => (
                      <a
                        key={j}
                        className="scout-sig"
                        href={s.url || undefined}
                        target="_blank"
                        rel="noreferrer"
                        title={s.title}
                      >
                        <span className="src-chip">{s.source}</span>
                        <span className="sig-title">{s.title}</span>
                      </a>
                    ))}
                  </div>
                )}
                {c.suggested_sub_segments.length > 0 && (
                  <div className="scout-segs">
                    {c.suggested_sub_segments.map((s) => (
                      <span className="tagpill" key={s}>{s}</span>
                    ))}
                  </div>
                )}
                <div className="scout-act">
                  <button className="btn btn-primary" onClick={() => onExploreCandidate(c)}>
                    Explore this space ▸
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {scoutState === "idle" && (
          <div className="scout-note">
            Not sure where to point the explorer? Scout reads live signals (Reddit, HN,
            GitHub, newsletters) and proposes spaces shaped like ownable companies.
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="home-dash">
      <div className="home-head">
        <div>
          <div className="eyebrow">Autonomous mode</div>
          <h2>Gap Finder</h2>
          <div className="mod-sub">
            Brief → Explore → Pressure-test → Compare → Take away. The machine hunts;
            you decide.
          </div>
        </div>
        <div className="home-head-actions">
          <button className="btn" onClick={onQuickNew}>⚡ Quick scan</button>
          <button className="btn btn-primary" onClick={onNew}>＋ New exploration</button>
        </div>
      </div>

      {/* 1. Worth your attention — what did the machine find? */}
      <div className="module">
        <div className="mod-head">
          <div className="mod-title">Worth your attention</div>
          <div className="mod-sub">
            Top ideas across all projects by fit × viability — survived the red team,
            ranked by how much they deserve your attention.
          </div>
        </div>
        {heroLoading && heroIdeas.length === 0 ? (
          <div className="hero-grid" aria-hidden>
            {[0, 1, 2].map((i) => (
              <div className="card idea-card skel" key={i} style={{ animationDelay: `${i * 40}ms` }}>
                <div className="skel-line w40" />
                <div className="skel-line w90" />
                <div className="skel-line w95" />
                <div className="skel-line w60" />
              </div>
            ))}
          </div>
        ) : heroIdeas.length === 0 ? (
          <div className="card hero-empty">
            No pressure-tested ideas yet. Ideas land here once a run scores them —
            open an active run below to watch it work, or start one.
          </div>
        ) : (
          <div className="hero-grid">
            {heroIdeas.map(({ node: n, pid, domain }, i) => {
              const trust = nodeTrust(n);
              const pt = n.pressure_test;
              return (
                <button
                  key={n.id}
                  className="card idea-card rise"
                  style={{ animationDelay: `${i * 40}ms` }}
                  onClick={() => onOpenNode(pid, n.id)}
                >
                  <div className="idea-scores">
                    <ViabChip
                      value={n.viability}
                      trust={trust}
                      star={n.star}
                      title={`Viability ${n.viability} · ${n.confidence ?? "?"} confidence${
                        trust === "unverified" ? " · unverified" : ""
                      }`}
                    />
                    {trust === "unverified" && <span className="idea-unv">unverified</span>}
                    <FitChip value={n.fit} labeled />
                  </div>
                  <div className="idea-title">{n.gap?.title ?? n.title}</div>
                  {n.gap?.why_now && <p className="idea-why">Why now: {n.gap.why_now}</p>}
                  <div className="idea-proj">
                    {domain}
                    {n.confidence ? ` · ${n.confidence} confidence` : ""}
                    {pt ? ` · red team ${pt.survived}/${pt.lenses.length}` : ""}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* 2. Active exploration — what is it doing right now? */}
      {active.length > 0 && (
        <div className="module">
          <div className="mod-head">
            <div className="mod-title">Active exploration</div>
            <div className="mod-sub">
              Live counters and spend for every ongoing run. Pause any run; it resumes
              exactly where it stopped.
            </div>
          </div>
          <div className="active-stack">
            {active.map((p, i) => {
              const meta = statusMeta(p);
              const cap = p.budget.max_tokens;
              const pct = cap ? Math.min(100, (p.stats.tokens_spent / cap) * 100) : null;
              return (
                <button
                  key={p.id}
                  className="card active-card rise"
                  style={{ animationDelay: `${i * 40}ms` }}
                  onClick={() => onOpen(p.id)}
                >
                  <div className="ac-main">
                    <div className="ac-head">
                      <span className={`es-dot ${meta.dot}${meta.live ? " live" : ""}`} />
                      <span className="ac-name">{p.domain}</span>
                      <span className={`hc-status ${meta.dot}`}>{meta.word}</span>
                    </div>
                    <div className="ac-stats">
                      <span className="ac-stat"><b>{p.stats.nodes}</b><small>nodes</small></span>
                      <span className="ac-stat"><b>{p.stats.gaps}</b><small>gaps</small></span>
                      <span className="ac-stat"><b>{p.stats.stars}</b><small>starred</small></span>
                      <span className="ac-stat"><b>{p.stats.max_viability}</b><small>top viab</small></span>
                    </div>
                  </div>
                  <div className="ac-spend">
                    <span className="ac-spend-lab">Spend</span>
                    <div className="ac-bar">
                      <span style={{ width: `${pct ?? 100}%` }} className={pct == null ? "nocap" : ""} />
                    </div>
                    <span className="ac-spend-cap">
                      {fmt(p.stats.tokens_spent)}{cap ? ` of ${fmt(cap)} tok` : " tok · no cap"}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* 3. Suggested spaces — where should I look next? */}
      {renderScoutModule()}

      {/* finished runs — quiet library (full detail lives in the sidebar) */}
      {finished.length > 0 && (
        <div className="module">
          <div className="mod-head">
            <div className="mod-title">Finished runs</div>
            <div className="mod-sub">Done exploring — their shortlists are still worth mining.</div>
          </div>
          <div className="home-grid">
            {finished.map((p) => {
              const meta = statusMeta(p);
              return (
                <button key={p.id} className="home-card" onClick={() => onOpen(p.id)}>
                  <div className="hc-top">
                    <span className={`es-dot ${meta.dot}`} />
                    <span className={`hc-status ${meta.dot}`}>{meta.word}</span>
                  </div>
                  <div className="hc-domain">{p.domain}</div>
                  <div className="hc-stats">
                    <span><b>{p.stats.gaps}</b> gaps</span>
                    <span className="hc-star"><b>{p.stats.stars}</b>★</span>
                    <span><b>{p.stats.max_viability}</b> top</span>
                    <span className="hc-tok">{fmt(p.stats.tokens_spent)} tok</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
