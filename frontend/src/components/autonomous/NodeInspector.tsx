import { useCallback, useState } from "react";
import {
  SCORE_KEYS,
  SCORE_LABELS,
  SCORE_HELP,
  SOURCE_LABEL,
} from "../../types";
import {
  displayRationale,
  isUnevaluatedLens,
  nodeTrust,
  viabilityRamp,
  STAGES,
  STAGE_LABELS,
  TRIAGE_REASONS,
  TRIAGE_REASON_LABELS,
  type Stage,
  type TreeNode,
  type Triage,
} from "../../autonomous/types";
import type { Project } from "../../autonomous/types";
import { ApiError, getResearchPack } from "../../autonomous/api";
import { gapToMarkdown } from "../../autonomous/exportGap";
import FitChip from "./FitChip";
import Markdown from "./Markdown";
import ViabChip from "./ViabChip";

interface Props {
  node: TreeNode | null;
  childNodes?: TreeNode[];
  onSelectChild?: (id: string) => void;
  /** Pin/unpin a queued branch so the frontier expands it next (SPEC §4.1). */
  onTogglePin?: (nodeId: string, pinned: boolean) => void;
  /** Star/unstar for the user's own shortlist (writes `user_star`). */
  onToggleStar?: (nodeId: string, starred: boolean) => void;
  /** The project, so an exported gap can carry its domain + steering as context. */
  project?: Project | null;
  /** S1 triage — interested/pass (+ reason); null clears the verdict. */
  onSetTriage?: (nodeId: string, triage: Triage | null, reason: string) => void;
  /** S2 look-into checklist — stage (+ learnings); null clears the stage. */
  onSetStage?: (nodeId: string, stage: Stage | null, learnings: string) => void;
  /** C2 Space Watch — toggle background source sweeps for this node. */
  onToggleWatch?: (nodeId: string, watched: boolean) => void;
  /** Needed for the Research Pack call (H2). */
  projectId?: string;
  /** Whether this project was given founder steering (drives fit messaging). */
  hasSteering?: boolean;
  busy?: boolean;
}

const VERDICT_META: Record<string, { cls: string; word: string }> = {
  survives: { cls: "survives", word: "Survives" },
  weakens: { cls: "weakens", word: "Weakens" },
  kills: { cls: "kills", word: "Kills" },
};

/** Plain-sentence hover definitions — numbers framed as hypotheses (memo §3). */
const METRIC_HELP = {
  viability:
    "Market strength after adversarial testing. Not a promise — a prioritized hypothesis.",
  confidence:
    "How much fresh evidence backs the score. High means corroborated; low means the model is mostly guessing.",
  rigor:
    "How hard the red team pushed. Light passes are quick reads — their scores stay unverified.",
  fit:
    "Founder fit — how attackable this space is for YOU, from your steering. Orthogonal to viability.",
};

// Map a 1..5 score onto the theme's grey→blue→navy ramp (denser = stronger),
// so score numbers read in the CNC palette, not a rainbow.
function scoreColor(v: number): string {
  const i = Math.min(4, Math.max(0, Math.round(v) - 1));
  return `var(--ramp-${i})`;
}
function titleCase(k: string): string {
  return k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
function fmtDate(d: string | null): string | null {
  if (!d) return null;
  const t = new Date(d);
  return Number.isNaN(t.getTime())
    ? d
    : t.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/* ------------------------------------------------- watch toggle (C2) ---- */
function WatchButton({
  node,
  busy,
  onToggleWatch,
}: {
  node: TreeNode;
  busy: boolean;
  onToggleWatch: (nodeId: string, watched: boolean) => void;
}) {
  return (
    <button
      className={`btn btn-ghost watch-btn${node.watched ? " on" : ""}`}
      disabled={busy}
      aria-pressed={node.watched}
      onClick={() => onToggleWatch(node.id, !node.watched)}
      title={
        node.watched
          ? "Stop re-checking this space's sources for material shifts"
          : "Watch this space — sweeps re-check its sources and alert you on material shifts. No LLM spend."
      }
    >
      {node.watched ? "◉ Watching" : "◎ Watch this space"}
    </button>
  );
}

/* ------------------------------- copy-for-chat export -------------------- */
/** Copy the gap as a portable markdown brief for pasting into any chat assistant.
 *
 * Deliberately client-side and LLM-free: it serializes data already on the node,
 * so it is instant, costs nothing, and works offline — unlike the Research Pack,
 * which is a strong-model call. Falls back to a hidden textarea + execCommand on
 * browsers/contexts where the async clipboard API is unavailable (non-HTTPS
 * origins), so the button is never a dead end.
 */
function CopyGapButton({
  node,
  project,
}: {
  node: TreeNode;
  project?: Project | null;
}) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);

  const copy = useCallback(async () => {
    const md = gapToMarkdown(node, project ?? null);
    let ok = false;
    try {
      await navigator.clipboard.writeText(md);
      ok = true;
    } catch {
      // Clipboard API blocked (insecure origin / permissions) — fall back.
      try {
        const ta = document.createElement("textarea");
        ta.value = md;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        ok = document.execCommand("copy");
        document.body.removeChild(ta);
      } catch {
        ok = false;
      }
    }
    setCopied(ok);
    setFailed(!ok);
    window.setTimeout(() => {
      setCopied(false);
      setFailed(false);
    }, 2200);
  }, [node, project]);

  return (
    <button
      className={`btn btn-ghost copy-btn${copied ? " ok" : ""}${failed ? " err" : ""}`}
      onClick={copy}
      title="Copy this gap as a self-contained markdown brief — thesis, evidence, and the red-team criticism — ready to paste into ChatGPT, Claude, or a doc."
    >
      {copied ? "✓ Copied" : failed ? "Copy failed" : "⧉ Copy for chat"}
    </button>
  );
}

/* ------------------------------------------------ triage block (S1) ----- */
function TriageBlock({
  node,
  busy,
  onSetTriage,
}: {
  node: TreeNode;
  busy: boolean;
  onSetTriage: (nodeId: string, triage: Triage | null, reason: string) => void;
}) {
  const [reasonDraft, setReasonDraft] = useState("");
  const isTaxonomy = (TRIAGE_REASONS as readonly string[]).includes(node.triage_reason);
  const commitFreeText = () => {
    const t = reasonDraft.trim();
    if (t && t !== node.triage_reason) onSetTriage(node.id, "passed", t);
  };
  return (
    <div className="triage-block">
      <div className="triage-btns">
        <button
          className={`btn triage-btn${node.triage === "interested" ? " on" : ""}`}
          disabled={busy}
          aria-pressed={node.triage === "interested"}
          onClick={() =>
            onSetTriage(node.id, node.triage === "interested" ? null : "interested", "")
          }
          title="Mark interested — press i when the inspector is focused. Click again to clear."
        >
          Interested <kbd className="kbd-hint">i</kbd>
        </button>
        <button
          className={`btn triage-btn${node.triage === "passed" ? " on" : ""}`}
          disabled={busy}
          aria-pressed={node.triage === "passed"}
          onClick={() =>
            onSetTriage(node.id, node.triage === "passed" ? null : "passed", "")
          }
          title="Pass on this space — press p when the inspector is focused. Click again to clear."
        >
          Pass <kbd className="kbd-hint">p</kbd>
        </button>
      </div>
      {node.triage === "passed" && (
        <div className="triage-reason">
          <div className="tr-hint">Why the pass? Your reasons teach the engine your taste.</div>
          <div className="chip-wrap">
            {TRIAGE_REASONS.map((r) => (
              <button
                key={r}
                className={`tagpill reason-chip${node.triage_reason === r ? " on" : ""}`}
                disabled={busy}
                aria-pressed={node.triage_reason === r}
                onClick={() =>
                  onSetTriage(node.id, "passed", node.triage_reason === r ? "" : r)
                }
              >
                {TRIAGE_REASON_LABELS[r]}
              </button>
            ))}
          </div>
          <input
            className="reason-input"
            type="text"
            placeholder="…or say it in your own words (Enter to save)"
            value={reasonDraft}
            disabled={busy}
            onChange={(e) => setReasonDraft(e.target.value)}
            onBlur={commitFreeText}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitFreeText();
            }}
          />
          {node.triage_reason && !isTaxonomy && (
            <div className="tr-current">Saved reason: “{node.triage_reason}”</div>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------- stage block (S2) ----- */
function StageBlock({
  node,
  busy,
  onSetStage,
}: {
  node: TreeNode;
  busy: boolean;
  onSetStage: (nodeId: string, stage: Stage | null, learnings: string) => void;
}) {
  const [draft, setDraft] = useState(node.learnings);
  const dirty = draft !== node.learnings;
  const save = () => {
    if (dirty) onSetStage(node.id, node.stage, draft);
  };
  return (
    <div className="stage-block">
      <div className="stage-row">
        <label className="stage-label" htmlFor={`stage-${node.id}`}>
          Look-into stage
        </label>
        <select
          id={`stage-${node.id}`}
          className="stage-select"
          disabled={busy}
          value={node.stage ?? ""}
          onChange={(e) =>
            onSetStage(node.id, (e.target.value || null) as Stage | null, node.learnings)
          }
        >
          <option value="">— not started —</option>
          {STAGES.map((s) => (
            <option key={s} value={s}>
              {STAGE_LABELS[s]}
            </option>
          ))}
        </select>
      </div>
      <textarea
        className="learnings-box"
        placeholder="What have you learned so far? Interview notes, smoke-test numbers, dead ends…"
        value={draft}
        disabled={busy}
        rows={3}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={save}
      />
      {dirty && (
        <button className="btn btn-sm" disabled={busy} onClick={save}>
          Save learnings
        </button>
      )}
    </div>
  );
}

/* -------------------------------------------- research pack (H2) -------- */
type PackState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "ready"; markdown: string; cached: boolean }
  | { phase: "error"; message: string; unavailable: boolean };

function ResearchPackButton({
  projectId,
  node,
}: {
  projectId: string;
  node: TreeNode;
}) {
  const [open, setOpen] = useState(false);
  const [pack, setPack] = useState<PackState>({ phase: "idle" });

  const fetchPack = useCallback(
    async (refresh: boolean) => {
      setPack({ phase: "loading" });
      try {
        const r = await getResearchPack(projectId, node.id, refresh);
        setPack({ phase: "ready", markdown: r.markdown, cached: r.cached });
      } catch (e) {
        const status = e instanceof ApiError ? e.status : 0;
        setPack({
          phase: "error",
          message:
            e instanceof ApiError && e.message
              ? e.message
              : "The research pack could not be generated.",
          unavailable: status === 503,
        });
      }
    },
    [projectId, node.id],
  );

  const openModal = () => {
    setOpen(true);
    if (node.research_pack) {
      // Serve the cached pack instantly — regenerate stays one click away.
      setPack({ phase: "ready", markdown: node.research_pack, cached: true });
    } else {
      fetchPack(false);
    }
  };

  const download = (markdown: string) => {
    const blob = new Blob([markdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `research-pack-${(node.gap?.title ?? node.title).replace(/[^a-z0-9]+/gi, "-").toLowerCase().slice(0, 60)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <button
        className={`btn${node.star ? " btn-primary" : ""} rp-btn`}
        onClick={openModal}
        title="One strong-model pass turns this gap into a hand-off pack: interview scripts, a smoke-test plan, a sizing sketch, and a falsification map."
      >
        ⎘ Research pack{node.research_pack ? " · ready" : ""}
      </button>
      {open && (
        <div className="rp-scrim" onClick={() => setOpen(false)} role="presentation">
          <div
            className="rp-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Research pack"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="rp-head">
              <div>
                <div className="eyebrow">Research pack</div>
                <div className="rp-title">{node.gap?.title ?? node.title}</div>
              </div>
              <button className="drawer-close" onClick={() => setOpen(false)} aria-label="Close">
                ×
              </button>
            </div>
            <div className="rp-body">
              {pack.phase === "loading" && (
                <div className="rp-loading" aria-busy="true">
                  Writing the pack — one strong-model pass over this gap's evidence. Interview
                  scripts quote only live evidence; nothing is invented.
                </div>
              )}
              {pack.phase === "error" && (
                <div className="rp-error" role="alert">
                  <div className="rp-error-t">
                    {pack.unavailable ? "Pack unavailable" : "Something failed"}
                  </div>
                  <div className="rp-error-d">{pack.message}</div>
                  <div className="rp-error-d">
                    Nothing canned is shown in its place — a pack only exists when the model
                    actually wrote one.
                  </div>
                  <button className="btn" onClick={() => fetchPack(false)}>
                    Try again
                  </button>
                </div>
              )}
              {pack.phase === "ready" && <Markdown className="rp-md" text={pack.markdown} />}
            </div>
            {pack.phase === "ready" && (
              <div className="rp-foot">
                <span className="rp-cached">
                  {pack.cached ? "cached — regenerating costs one strong-model call" : "freshly generated"}
                </span>
                <button className="btn" onClick={() => fetchPack(true)}>
                  ↻ Regenerate
                </button>
                <button className="btn btn-primary" onClick={() => download(pack.markdown)}>
                  ↓ Download .md
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

/**
 * Right-rail inspector. For gap nodes it renders the full case (thesis, the
 * narrative pillars, competitors, evidence — the same structure as the
 * single-area GapDetail drawer) plus the pressure-test panel: each adversarial
 * lens with its verdict + argument, the viability score, confidence, and
 * test_rigor. For structural nodes it shows the branch rationale, keywords, and a
 * child summary (SPEC §10.2).
 */
export default function NodeInspector({
  node,
  childNodes = [],
  onSelectChild,
  onTogglePin,
  onToggleStar,
  onSetTriage,
  onSetStage,
  onToggleWatch,
  project,
  projectId,
  hasSteering = false,
  busy = false,
}: Props) {
  // Keyboard triage (S1): i = interested, p = pass, when the inspector holds
  // focus and the keypress is not aimed at a form field.
  const handleKey = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (!node || !onSetTriage || busy) return;
      if (node.kind !== "gap" && node.kind !== "gap_candidate") return;
      const t = e.target as HTMLElement;
      if (/^(input|textarea|select)$/i.test(t.tagName) || t.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "i") {
        e.preventDefault();
        onSetTriage(node.id, node.triage === "interested" ? null : "interested", "");
      } else if (k === "p") {
        e.preventDefault();
        onSetTriage(node.id, node.triage === "passed" ? null : "passed", "");
      }
    },
    [node, onSetTriage, busy],
  );
  if (!node) {
    return (
      <div className="inspector-empty">
        <div className="ie-ico">☞</div>
        <div className="ie-t">Nothing selected</div>
        <div className="ie-d">
          Click a node in the tree. Gaps open their full case, evidence, and pressure test;
          branches show their rationale and children. Solid scores are earned — dashed ones
          are still unverified.
        </div>
      </div>
    );
  }

  const gapish = node.kind === "gap" || node.kind === "gap_candidate";
  const g = node.gap;

  /* ---------------------------------------------------- structural nodes -- */
  if (!gapish || !g) {
    // Pinning only reorders the frontier, which holds queued structural nodes —
    // so the toggle is offered exactly there and read-only everywhere else.
    const pinnable = node.state === "queued" && !!onTogglePin;
    return (
      <div className="inspector">
        <div className="insp-head">
          <div className="insp-kind">
            {node.pinned && <span className="ik-pin">⚑ Pinned · </span>}
            {node.watched && <span className="ik-watch">◉ Watched · </span>}
            {titleCase(node.kind)}
          </div>
          <h3 className="insp-title">{node.title}</h3>
          {((pinnable || node.pinned) || (node.kind === "segment" && onToggleWatch)) && (
            <div className="insp-actions">
              {(pinnable || node.pinned) && (
                <button
                  className={`btn btn-ghost pin-btn${node.pinned ? " pinned" : ""}`}
                  disabled={busy || (!pinnable && !node.pinned)}
                  onClick={() => onTogglePin?.(node.id, !node.pinned)}
                  title={
                    node.pinned
                      ? "Remove the priority boost from this branch"
                      : "Boost this branch to the front of the exploration queue"
                  }
                >
                  {node.pinned ? "⚑ Unpin branch" : "⚑ Explore next"}
                </button>
              )}
              {node.kind === "segment" && onToggleWatch && (
                <WatchButton node={node} busy={busy} onToggleWatch={onToggleWatch} />
              )}
            </div>
          )}
        </div>
        {displayRationale(node.rationale) && (
          <Markdown className="detail-thesis md-clamp" text={displayRationale(node.rationale)} />
        )}
        {node.rationale !== null && displayRationale(node.rationale) !== (node.rationale ?? "").trim() && (
          <div className="insp-note">
            Steered by your founder brief — every step under this branch honours it.
          </div>
        )}

        {node.keywords.length > 0 && (
          <div className="detail-block">
            <h4>Query keywords</h4>
            <div className="chip-wrap">
              {node.keywords.map((k) => (
                <span className="tagpill" key={k}>
                  {k}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="detail-block">
          <h4>Children ({childNodes.length})</h4>
          {childNodes.length === 0 ? (
            <div className="insp-note">
              {node.state === "expanding"
                ? "Decomposing this branch…"
                : node.state === "errored"
                ? node.error || "This branch failed to expand."
                : "No children yet."}
            </div>
          ) : (
            <div className="child-list">
              {childNodes.map((c) => (
                <button
                  key={c.id}
                  className="child-row"
                  onClick={() => onSelectChild?.(c.id)}
                >
                  <span className="cr-title">{c.title}</span>
                  {(c.kind === "gap" || c.kind === "gap_candidate") && c.fit != null && (
                    <FitChip
                      value={c.fit}
                      title={`Founder fit ${c.fit} — how attackable this space is for YOU, from your steering. Orthogonal to viability.`}
                    />
                  )}
                  {(c.kind === "gap" || c.kind === "gap_candidate") && c.viability != null && (
                    <ViabChip
                      value={c.viability}
                      trust={nodeTrust(c)}
                      star={c.star}
                      title={
                        nodeTrust(c) === "unverified"
                          ? `Viability ${c.viability} — unverified`
                          : `Viability ${c.viability}`
                      }
                    />
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  /* ---------------------------------------------------------- gap nodes -- */
  const pt = node.pressure_test;
  const trust = nodeTrust(node);
  const ramp = viabilityRamp(node.viability);
  // Unverified numbers desaturate toward the neutral grey (memo §2).
  const inkColor =
    trust === "unverified" ? `color-mix(in srgb, ${ramp} 42%, var(--data-neutral))` : ramp;
  const fixture = g.tags.includes("fixture");
  return (
    <div className="inspector" tabIndex={0} onKeyDown={handleKey}>
      <div className="insp-head">
        <div className="insp-kind">
          {node.star && (
            <span className="ik-star" title="The engine rated this above your star threshold.">
              ★ Engine pick ·{" "}
            </span>
          )}
          {node.user_star && (
            <span className="ik-userstar" title="You starred this.">
              ★ Starred ·{" "}
            </span>
          )}
          {node.watched && <span className="ik-watch">◉ Watched · </span>}
          {node.triage && (
            <span className="ik-triage">
              {node.triage === "interested" ? "Interested" : "Passed"} ·{" "}
            </span>
          )}
          {node.kind === "gap" ? "Pressure-tested gap" : "Candidate gap"}
        </div>
        <h3 className="insp-title">{g.title}</h3>
        <div className="insp-actions">
          {onToggleStar && (
            <button
              className={`btn btn-ghost star-btn${node.user_star ? " on" : ""}`}
              disabled={busy}
              aria-pressed={node.user_star}
              onClick={() => onToggleStar(node.id, !node.user_star)}
              title={
                node.user_star
                  ? "Remove from your starred ideas"
                  : "Star this idea — keeps it on your shortlist, independent of the engine's score"
              }
            >
              {node.user_star ? "★ Starred" : "☆ Star idea"}
            </button>
          )}
          <CopyGapButton node={node} project={project} />
        </div>
      </div>

      {/* provenance banner — this gap never touched the live LLM (memo §2) */}
      {fixture && (
        <div className="fixture-banner" role="alert">
          <span className="fb-badge">canned data</span>
          <div className="fb-text">
            This gap was served from canned fixture data — the backend could not reach the
            LLM. Treat as placeholder, not a finding.
          </div>
        </div>
      )}

      {/* viability headline */}
      <div className="viab-head">
        <div
          className={`viab-big trust-${trust}`}
          style={{ borderColor: inkColor, color: inkColor }}
          title={METRIC_HELP.viability}
        >
          <span className="vb-num">{node.viability ?? "—"}</span>
          <span className="vb-lab">viability</span>
          {trust === "unverified" && <span className="vb-unverified">unverified</span>}
        </div>
        {node.fit != null && (
          <div className="viab-big fit-big" title={METRIC_HELP.fit}>
            <span className="vb-num">{node.fit}</span>
            <span className="vb-lab">founder fit</span>
          </div>
        )}
        <div className="viab-side">
          <div className="vs-row">
            <span className="vs-k" title={METRIC_HELP.confidence}>Confidence</span>
            <span className={`conf-pill ${node.confidence ?? "low"}`}>
              {node.confidence ?? "—"}
            </span>
          </div>
          {pt && (
            <div className="vs-row">
              <span className="vs-k" title={METRIC_HELP.rigor}>Test rigor</span>
              <span className="rigor-pill">{pt.test_rigor}</span>
            </div>
          )}
          {pt && (
            <div className="vs-tally">
              <span className="tally survives">{pt.survived} survived</span>
              <span className="tally weakens">{pt.weakened} weak</span>
              <span className="tally kills">{pt.killed} killed</span>
            </div>
          )}
        </div>
      </div>

      {/* founder-fit narrative / instruction (memo §1: vermillion = the founder) */}
      {node.fit != null && node.fit_reason && (
        <div className="fit-reason">
          <span className="fr-k">Why this fit</span>
          <p>{node.fit_reason}</p>
        </div>
      )}
      {node.fit == null && !hasSteering && (
        <div className="fit-hint">
          Add founder context when starting a run to get fit scores — viability says real
          space, fit says real for you.
        </div>
      )}

      {/* your read — triage, watch, stage, research pack (S1/S2/C2/H2).
          Workflow state, not scores: neutral ink throughout (memo §1). */}
      {(onSetTriage || onToggleWatch || projectId) && (
        <div className="detail-block sensor-block">
          <h4>Your read</h4>
          <div className="mod-sub">
            Log your verdict so the engine learns your taste — <kbd className="kbd-hint">i</kbd>{" "}
            interested · <kbd className="kbd-hint">p</kbd> pass when this panel is focused.
          </div>
          <div className="sensor-actions">
            {onSetTriage && (
              <TriageBlock key={node.id} node={node} busy={busy} onSetTriage={onSetTriage} />
            )}
            <div className="sensor-side">
              {onToggleWatch && (
                <WatchButton node={node} busy={busy} onToggleWatch={onToggleWatch} />
              )}
              {projectId && <ResearchPackButton key={`rp-${node.id}`} projectId={projectId} node={node} />}
            </div>
          </div>
          {node.star && onSetStage && (
            <StageBlock key={`stage-${node.id}`} node={node} busy={busy} onSetStage={onSetStage} />
          )}
        </div>
      )}

      <Markdown className="detail-thesis" text={g.thesis} />

      {/* Plain language, before any jargon. Sits directly under the thesis and
          above "The company" on purpose: everything below this point is written
          for someone already fluent in the domain, and an idea you cannot
          explain to an outsider is one you cannot sell, hire for, or raise on.
          Gaps synthesized before this field existed have no easy_explain — the
          block is omitted rather than faked from the thesis. */}
      {g.easy_explain?.trim() && (
        <div className="detail-block explain-block">
          <h4>In plain language</h4>
          <Markdown className="detail-explain" text={g.easy_explain} />
        </div>
      )}

      {/* company concept — the standalone business, not a feature */}
      {g.company && (
        <div className="detail-block company-block">
          <h4>
            The company
            <span className={`company-badge ${g.company.standalone ? "yes" : "no"}`}>
              {g.company.standalone ? "Standalone company" : "Risk: just a feature"}
            </span>
          </h4>
          {g.company.product && (
            <div className="company-product">{g.company.product}</div>
          )}
          <div className="kv-grid">
            {g.company.icp && (
              <div className="kv">
                <div className="kv-k">Who it's for</div>
                <div className="kv-v">{g.company.icp}</div>
              </div>
            )}
            {g.company.business_model && (
              <div className="kv accent">
                <div className="kv-k">Business model</div>
                <div className="kv-v">{g.company.business_model}</div>
              </div>
            )}
            {g.company.expansion_path && (
              <div className="kv">
                <div className="kv-k">Wedge → platform</div>
                <div className="kv-v">{g.company.expansion_path}</div>
              </div>
            )}
            {g.company.moat && (
              <div className="kv">
                <div className="kv-k">Moat</div>
                <div className="kv-v">{g.company.moat}</div>
              </div>
            )}
            {g.company.standalone_reason && (
              <div className={`kv ${g.company.standalone ? "" : "warn"}`}>
                <div className="kv-k">Company, not a feature?</div>
                <div className="kv-v">{g.company.standalone_reason}</div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Value model — the costed status-quo → ROI case (⭐ gaps only). The
          founder asked for a validation step that models the current state and
          how much cost the idea removes; this renders it when present. */}
      {node.value_model && (
        <div className="detail-block value-block">
          <h4>
            What it's worth
            {node.value_model.confidence && (
              <span className={`value-conf ${node.value_model.confidence}`}>
                {node.value_model.confidence} confidence
              </span>
            )}
          </h4>
          {node.value_model.annual_value && (
            <div className="value-headline">{node.value_model.annual_value}</div>
          )}
          <div className="kv-grid">
            {node.value_model.status_quo && (
              <div className="kv">
                <div className="kv-k">Status quo today</div>
                <div className="kv-v">{node.value_model.status_quo}</div>
              </div>
            )}
            {node.value_model.current_cost && (
              <div className="kv">
                <div className="kv-k">What that costs</div>
                <div className="kv-v">{node.value_model.current_cost}</div>
              </div>
            )}
            {node.value_model.solution_delta && (
              <div className="kv">
                <div className="kv-k">How this cuts it</div>
                <div className="kv-v">{node.value_model.solution_delta}</div>
              </div>
            )}
          </div>
          {node.value_model.cost_drivers.length > 0 && (
            <div className="value-drivers">
              {node.value_model.cost_drivers.map((d, i) => (
                <span className="value-chip" key={i}>{d}</span>
              ))}
            </div>
          )}
          {node.value_model.assumptions.length > 0 && (
            <div className="value-assump">
              <span className="fr-k">Rests on</span>
              <ul>
                {node.value_model.assumptions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Novelty scan — how much open space is left above funded incumbents.
          Directly answers the founder's recurring "is this already occupied?". */}
      {node.novelty_scan && (
        <div className="detail-block novelty-block">
          <h4>
            Open space
            <span className={`novelty-verdict ${node.novelty_scan.verdict}`}>
              {node.novelty_scan.verdict || "—"} · {node.novelty_scan.novelty_0_100}/100
            </span>
          </h4>
          {node.novelty_scan.rationale && <p className="novelty-why">{node.novelty_scan.rationale}</p>}
          {node.novelty_scan.structural_risk && (
            <div className="novelty-trap">
              <span className="trap-k">⚠︎ Empty for a reason</span>
              <p>{node.novelty_scan.structural_risk}</p>
            </div>
          )}
          {node.novelty_scan.nearest_known.length > 0 && (
            <div className="novelty-near">
              <span className="fr-k">Nearest funded</span>
              {node.novelty_scan.nearest_known.map((c, i) => (
                <div className="near-co" key={i}>
                  <div className="near-head">
                    {c.url ? (
                      <a href={c.url} target="_blank" rel="noreferrer">{c.name || c.url}</a>
                    ) : (
                      <span>{c.name || "—"}</span>
                    )}
                    {c.funded && <span className="near-funded">{c.funded}</span>}
                  </div>
                  {c.why_similar && <div className="near-why">{c.why_similar}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* base scores */}
      <div className="detail-scorebar">
        {SCORE_KEYS.map((k) => (
          <div className="dsb-item" key={k} title={SCORE_HELP[k]}>
            <div className="dsb-val" style={{ color: scoreColor(g.scores[k]) }}>
              {g.scores[k]}
            </div>
            <div className="dsb-lab">{SCORE_LABELS[k]}</div>
          </div>
        ))}
      </div>

      {g.empty_for_a_reason && (
        <div className="empty-flag">
          <span className="ef-ico">⚠︎</span>
          <div>
            <div className="ef-t">Possibly empty for a reason</div>
            <div className="ef-d">
              {g.empty_reason || "This opening may be structurally unattractive — validate before committing."}
            </div>
          </div>
        </div>
      )}

      {/* pressure-test panel */}
      {pt && pt.lenses.length > 0 && (
        <div className="detail-block">
          <h4>Pressure test — {pt.lenses.length} adversarial lenses</h4>
          <div className="mod-sub">
            Each lens tried to kill this idea. A survival only counts when backed by fresh
            evidence.
          </div>
          {pt.summary && <div className="pt-summary">{pt.summary}</div>}
          {pt.self_critique && (
            <div className="pt-critique">
              <span className="ptc-k">Strongest reason the score is wrong</span>
              <p>{pt.self_critique}</p>
            </div>
          )}
          <div className="lens-list">
            {pt.lenses.map((l, i) => {
              // A heuristic-filled verdict never wears a full-authority pill.
              const meta = isUnevaluatedLens(l, pt.test_rigor)
                ? { cls: "noteval", word: "Not evaluated" }
                : VERDICT_META[l.verdict] ?? VERDICT_META.weakens;
              return (
                <div className={`lens-item ${meta.cls}`} key={l.lens + i}>
                  <div className="lens-top">
                    <span className="lens-name">{titleCase(l.lens)}</span>
                    <span
                      className={`verdict-pill ${meta.cls}`}
                      title={
                        meta.cls === "noteval"
                          ? "This lens was filled heuristically — no real red-team pass ran."
                          : undefined
                      }
                    >
                      {meta.word}
                    </span>
                  </div>
                  <div className="lens-arg">{l.argument}</div>
                  {l.evidence.length > 0 && (
                    <div className="lens-ev">
                      {l.evidence.map((e, j) => (
                        <a
                          key={j}
                          className={`lens-ev-src ${e.source}`}
                          href={e.url || undefined}
                          target="_blank"
                          rel="noreferrer"
                          title={e.quote}
                        >
                          {SOURCE_LABEL[e.source]} ↗
                        </a>
                      ))}
                      {l.evidence.some((e) => e.live === false) && (
                        <span className="canned-badge" title="One or more quotes came from canned fixture data, not a live fetch.">
                          canned data
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* narrative pillars */}
      <div className="detail-block">
        <h4>The case</h4>
        <div className="kv-grid">
          {g.why_now && (
            <div className="kv accent">
              <div className="kv-k">Why now</div>
              <div className="kv-v">{g.why_now}</div>
            </div>
          )}
          <div className="kv">
            <div className="kv-k">Wedge</div>
            <div className="kv-v">{g.wedge}</div>
          </div>
          <div className="kv warn">
            <div className="kv-k">Riskiest assumption</div>
            <div className="kv-v">{g.riskiest_assumption}</div>
          </div>
          <div className="kv warn">
            <div className="kv-k">Weakest link</div>
            <div className="kv-v">{g.weakest_link}</div>
          </div>
        </div>
      </div>

      {/* competitors */}
      {g.competitors.length > 0 && (
        <div className="detail-block">
          <h4>Top competitors &amp; their blind spots</h4>
          <div className="table-scroll">
            <table className="comp-table">
              <thead>
                <tr>
                  <th>Player</th>
                  <th>Segment</th>
                  <th>Tier</th>
                  <th>Blind spot</th>
                </tr>
              </thead>
              <tbody>
                {g.competitors.map((c, i) => (
                  <tr key={c.name + i}>
                    <td>
                      {c.url ? (
                        <a className="comp-name" href={c.url} target="_blank" rel="noreferrer">
                          {c.name} <span className="ext">↗</span>
                        </a>
                      ) : (
                        <span className="comp-name">{c.name}</span>
                      )}
                      {c.positioning && (
                        <div style={{ color: "var(--text-faint)", fontSize: 11, marginTop: 3 }}>
                          {c.positioning}
                        </div>
                      )}
                    </td>
                    <td className="comp-seg">{c.segment}</td>
                    <td>
                      <span className="price-tier">{c.price_tier || "—"}</span>
                    </td>
                    <td className="comp-weak">{c.weakness}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* evidence */}
      {g.evidence.length > 0 && (
        <div className="detail-block">
          <h4>Evidence ({g.evidence.length})</h4>
          <div className="mod-sub">
            The raw quotes this case stands on. Follow the links — a claim without a live
            source is still a hypothesis.
          </div>
          <div className="evidence-list">
            {g.evidence.map((e, i) => {
              const date = fmtDate(e.date);
              return (
                <div className="ev-item" key={i}>
                  <span className={`ev-src ${e.source}`}>{SOURCE_LABEL[e.source]}</span>
                  <div className="ev-content">
                    <div className="ev-quote">{e.quote}</div>
                    <div className="ev-foot">
                      {e.live === false && (
                        <span className="canned-badge" title="Served from canned fixture data, not a live fetch.">
                          canned data
                        </span>
                      )}
                      {date && <span>{date}</span>}
                      {e.url && (
                        <a href={e.url} target="_blank" rel="noreferrer">
                          View source ↗
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {g.tags.length > 0 && (
        <div className="detail-block">
          <h4>Tags</h4>
          <div className="chip-wrap">
            {g.tags.map((t) => (
              <span className="tagpill" key={t}>
                {t}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
