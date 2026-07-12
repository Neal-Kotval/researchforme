// The New-exploration drawer — the single launch surface for an autonomous
// run (domain, steering, intake questions, pace/models/budget). Shared by the
// explorer (scout prefill, ⌘K) and the platform shell's "New exploration".
import { useEffect, useRef, useState } from "react";
import { ApiError, getIntake, sortResearch } from "../../autonomous/api";
import {
  DEFAULT_BUDGET,
  type Budget,
  type CreateProjectRequest,
  type IntakeQuestion,
  type Pace,
} from "../../autonomous/types";
import ModelPicker from "../ModelPicker";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback;
}


/* -------------------------------------------------------------------- depth -- */
// "Quick scan" is the folded-in single-area analysis: a fast, shallow pass that
// lands in the same tree/idea UI. Standard/Deep widen the budget. The preset
// only *seeds* the budget fields — they stay editable.
export type Depth = "quick" | "standard" | "deep";

export const DEPTH_PRESETS: Record<Depth, Pick<Budget, "max_nodes" | "max_tokens" | "pace">> = {
  quick:    { max_nodes: 40,   max_tokens: 150_000, pace: "sprint" },
  standard: { max_nodes: 400,  max_tokens: null,    pace: "balanced" },
  deep:     { max_nodes: 1200, max_tokens: null,    pace: "balanced" },
};

/** Seed values for the drawer (scout candidate → prefilled launch). */
export interface DialogPrefill {
  domain: string;
  segs: string[];
  brief: string;
}

interface DialogProps {
  initialDepth: Depth;
  prefill?: DialogPrefill | null;
  onClose: () => void;
  onCreate: (req: CreateProjectRequest) => Promise<void>;
}

export default function NewExplorationDialog({ initialDepth, prefill, onClose, onCreate }: DialogProps) {
  const [domain, setDomain] = useState(prefill?.domain ?? "");
  const [segs, setSegs] = useState<string[]>(prefill?.segs ?? []);
  const [segText, setSegText] = useState("");
  const [decompose, setDecompose] = useState("claude-haiku-4-5-20251001");
  const [synth, setSynth] = useState("claude-opus-4-8");
  const [pressure, setPressure] = useState("claude-opus-4-8");
  const [depth, setDepth] = useState<Depth>(initialDepth);
  const [budget, setBudget] = useState<Budget>({ ...DEFAULT_BUDGET, ...DEPTH_PRESETS[initialDepth] });

  // Selecting a depth re-seeds the budget preset (fields stay editable after).
  const applyDepth = (d: Depth) => {
    setDepth(d);
    setBudget((b) => ({ ...b, ...DEPTH_PRESETS[d] }));
  };
  const [autostart, setAutostart] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Rich steering context — a founder brief + structured fields + pasted research.
  const [brief, setBrief] = useState(prefill?.brief ?? "");
  const [advantages, setAdvantages] = useState("");
  const [constraints, setConstraints] = useState("");
  const [avoid, setAvoid] = useState("");
  const [timeHorizon, setTimeHorizon] = useState("");
  const [research, setResearch] = useState("");
  const [showSteering, setShowSteering] = useState(Boolean(prefill?.brief));
  const [sortOpen, setSortOpen] = useState(false);
  const [sortText, setSortText] = useState("");
  const [sorting, setSorting] = useState(false);

  // Preflight intake: generated clarifying questions that steer the exploration.
  const [questions, setQuestions] = useState<IntakeQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [intakeLoading, setIntakeLoading] = useState(false);
  // The questions mount below the drawer's fold — without this scroll the
  // "Refine with questions" button reads as a no-op.
  const questionsRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (questions.length) {
      questionsRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [questions]);

  const lines = (s: string) =>
    s.split("\n").map((x) => x.trim()).filter(Boolean);

  const refineWithQuestions = async () => {
    if (domain.trim().length < 2) {
      setError("Enter a domain first, then refine.");
      return;
    }
    setIntakeLoading(true);
    setError(null);
    try {
      const { questions: qs } = await getIntake(domain.trim(), brief.trim());
      setQuestions(qs);
    } catch (e) {
      setError(errMsg(e, "Could not load intake questions."));
    } finally {
      setIntakeLoading(false);
    }
  };

  // Bulk-paste: sort a wall of the founder's own research into a launchable job.
  const sortPastedResearch = async () => {
    if (sortText.trim().length < 1) return;
    setSorting(true);
    setError(null);
    try {
      const sorted = await sortResearch(sortText.trim());
      if (sorted.domain) setDomain(sorted.domain);
      if (sorted.sub_segments?.length) {
        setSegs((prev) => Array.from(new Set([...prev, ...sorted.sub_segments])));
      }
      if (sorted.brief) setBrief(sorted.brief);
      setResearch(sorted.research || sortText.trim());
      setShowSteering(true);
      setSortOpen(false);
    } catch (e) {
      setError(errMsg(e, "Could not sort that research."));
    } finally {
      setSorting(false);
    }
  };

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const addSeg = () => {
    const v = segText.trim().replace(/,$/, "");
    if (v && !segs.includes(v)) setSegs([...segs, v]);
    setSegText("");
  };
  const setBudgetNum = (k: keyof Budget, raw: string) => {
    const n = parseInt(raw, 10);
    setBudget((b) => ({ ...b, [k]: raw.trim() === "" || Number.isNaN(n) ? null : n }));
  };

  const submit = async () => {
    if (domain.trim().length < 2) {
      setError("Give the explorer a domain (2+ characters).");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const intake = Object.fromEntries(
        Object.entries(answers).filter(([, v]) => v && v.trim())
      );
      const steering = {
        brief: brief.trim(),
        advantages: lines(advantages),
        constraints: lines(constraints),
        avoid: lines(avoid),
        time_horizon: timeHorizon.trim(),
        research: research.trim(),
      };
      await onCreate({
        domain: domain.trim(),
        sub_segments: segs,
        budget,
        decompose_model: decompose,
        synth_model: synth,
        pressure_model: pressure,
        intake,
        steering,
        autostart,
      });
    } catch (e) {
      setError(errMsg(e, "Could not start the exploration."));
      setSubmitting(false);
    }
  };

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <div className="new-dialog" role="dialog" aria-modal="true" aria-label="New exploration">
        <div className="nd-head">
          <div>
            <div className="eyebrow">Autonomous mode</div>
            <h3>New exploration</h3>
            <div className="mod-sub">
              Point the explorer at a domain. It maps the space, hypothesizes gaps, and
              red-teams each one — everything below just steers it.
            </div>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="nd-body">
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
          <label className="field-label">
            Domain <span className="req">*</span>
          </label>
          <input
            ref={inputRef}
            className="big-input"
            placeholder="e.g. embedded AI"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && submit()}
          />

          {/* bulk-paste: sort a wall of your own research into a launchable job */}
          <div className="nd-steer-toggles">
            <button
              type="button"
              className="nd-refine"
              onClick={() => setSortOpen((v) => !v)}
            >
              {sortOpen ? "× Close research paste" : "⤵ Paste my research"}
            </button>
            <button
              type="button"
              className="nd-refine ghost"
              onClick={() => setShowSteering((v) => !v)}
            >
              {showSteering ? "× Hide steering" : "✎ Add context & steering"}
            </button>
          </div>

          {sortOpen && (
            <div className="nd-sort">
              <label className="field-label">
                Paste research <span className="nd-opt">(notes, links, theses — anything)</span>
              </label>
              <textarea
                className="nd-textarea"
                rows={6}
                placeholder="Paste your own research here. I'll sort it into a domain, sub-segments, and a brief you can review before launching."
                value={sortText}
                onChange={(e) => setSortText(e.target.value)}
              />
              <button
                type="button"
                className="nd-sort-go"
                onClick={sortPastedResearch}
                disabled={sorting || sortText.trim().length < 1}
              >
                {sorting ? "Sorting…" : "↯ Sort into a job"}
              </button>
            </div>
          )}

          {/* rich steering — a founder brief + structured fields */}
          {showSteering && (
            <div className="nd-steer">
              <label className="field-label">
                Founder brief <span className="nd-opt">(your background, thesis, angle — weighted heavily)</span>
              </label>
              <textarea
                className="nd-textarea"
                rows={4}
                placeholder="Who you are, what you already believe about this space, assets you can bring, the wedge you're drawn to…"
                value={brief}
                onChange={(e) => setBrief(e.target.value)}
              />
              <div className="nd-row">
                <div className="nd-col">
                  <label className="field-label">Unfair advantages <span className="nd-opt">(one per line)</span></label>
                  <textarea className="nd-textarea sm" rows={3} value={advantages}
                    placeholder={"deep payments expertise\nan audience of 20k devs"}
                    onChange={(e) => setAdvantages(e.target.value)} />
                </div>
                <div className="nd-col">
                  <label className="field-label">Hard constraints <span className="nd-opt">(one per line)</span></label>
                  <textarea className="nd-textarea sm" rows={3} value={constraints}
                    placeholder={"solo-founder feasible only\nno heavy capital"}
                    onChange={(e) => setConstraints(e.target.value)} />
                </div>
              </div>
              <div className="nd-row">
                <div className="nd-col">
                  <label className="field-label">Avoid <span className="nd-opt">(one per line)</span></label>
                  <textarea className="nd-textarea sm" rows={2} value={avoid}
                    placeholder={"crypto\nregulated healthcare"}
                    onChange={(e) => setAvoid(e.target.value)} />
                </div>
                <div className="nd-col">
                  <label className="field-label">Time horizon</label>
                  <input className="nd-q-free" value={timeHorizon}
                    placeholder="e.g. nights & weekends for now"
                    onChange={(e) => setTimeHorizon(e.target.value)} />
                </div>
              </div>
              {research.trim() && (
                <div className="nd-research-note">
                  ↯ {research.trim().length.toLocaleString()} chars of your research attached — it steers every step.
                </div>
              )}
            </div>
          )}

          {/* preflight intake — clarifying questions that steer the exploration */}
          <div className="nd-intake">
            <button
              type="button"
              className="nd-refine"
              onClick={refineWithQuestions}
              disabled={intakeLoading}
            >
              {intakeLoading ? "Thinking…" : questions.length ? "↻ Regenerate questions" : "✦ Refine with questions"}
            </button>
            <span className="nd-refine-hint">
              {questions.length ? "Answer any that help — they steer the tree." : "Let it ask a few sharp questions first (optional)."}
            </span>
          </div>

          {questions.length > 0 && (
            <div className="nd-questions" ref={questionsRef}>
              {questions.map((q, i) => (
                <div className="nd-q" key={i}>
                  <div className="nd-q-label">{q.question}</div>
                  <div className="nd-q-chips">
                    {q.suggestions.map((s) => (
                      <button
                        type="button"
                        key={s}
                        className={`nd-q-chip${answers[q.question] === s ? " on" : ""}`}
                        onClick={() =>
                          setAnswers((a) => ({ ...a, [q.question]: a[q.question] === s ? "" : s }))
                        }
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                  <input
                    className="nd-q-free"
                    placeholder="…or type your own"
                    value={q.suggestions.includes(answers[q.question]) ? "" : answers[q.question] ?? ""}
                    onChange={(e) => setAnswers((a) => ({ ...a, [q.question]: e.target.value }))}
                  />
                </div>
              ))}
            </div>
          )}

          <label className="field-label" style={{ marginTop: 16 }}>
            Seed sub-segments <span className="nd-opt">(optional)</span>
          </label>
          <div className="chips-input">
            {segs.map((s) => (
              <span className="seg-chip" key={s}>
                {s}
                <button onClick={() => setSegs(segs.filter((x) => x !== s))} aria-label={`Remove ${s}`}>
                  ×
                </button>
              </span>
            ))}
            <input
              value={segText}
              onChange={(e) => setSegText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === ",") {
                  e.preventDefault();
                  addSeg();
                } else if (e.key === "Backspace" && !segText && segs.length) {
                  setSegs(segs.slice(0, -1));
                }
              }}
              onBlur={addSeg}
              placeholder={segs.length ? "" : "Add a segment, press Enter"}
            />
          </div>

          {/* pace */}
          <div className="nd-row">
            <div className="nd-col">
              <label className="field-label">Pace</label>
              <div className="seg-control" role="radiogroup" aria-label="Pace">
                {(["eco", "balanced", "sprint"] as Pace[]).map((p) => (
                  <button
                    key={p}
                    type="button"
                    role="radio"
                    aria-checked={budget.pace === p}
                    aria-pressed={budget.pace === p}
                    className="seg-option"
                    onClick={() => setBudget((b) => ({ ...b, pace: p }))}
                  >
                    <span className="so-label" style={{ textTransform: "capitalize" }}>
                      {p}
                    </span>
                  </button>
                ))}
              </div>
            </div>
            <div className="nd-col">
              <div className="slider-top">
                <label
                  className="field-label"
                  style={{ margin: 0 }}
                  title="Ideas scoring at or above this viability get a star — a flag for your review, not a verdict."
                >
                  Star threshold
                </label>
                <span className="slider-val">≥ {budget.star_threshold}</span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={budget.star_threshold}
                onChange={(e) => setBudget((b) => ({ ...b, star_threshold: Number(e.target.value) }))}
              />
            </div>
          </div>

          {/* models */}
          <label className="field-label" style={{ marginTop: 18 }}>
            Model policy
          </label>
          <div className="nd-models">
            <div className="nd-model">
              <span className="ndm-lab">Decompose</span>
              <ModelPicker value={decompose} onChange={setDecompose} compact />
            </div>
            <div className="nd-model">
              <span className="ndm-lab">Synthesize</span>
              <ModelPicker value={synth} onChange={setSynth} compact />
            </div>
            <div className="nd-model">
              <span className="ndm-lab">Pressure-test</span>
              <ModelPicker value={pressure} onChange={setPressure} compact />
            </div>
          </div>

          {/* budget caps */}
          <label className="field-label" style={{ marginTop: 18 }}>
            Budget &amp; caps <span className="nd-opt">(blank = no cap)</span>
          </label>
          <div className="nd-budget">
            {(
              [
                ["max_tokens", "Max tokens", "∞"],
                ["daily_cap_tokens", "Daily cap", "∞"],
                ["max_nodes", "Max nodes", "∞"],
                ["time_limit_minutes", "Time limit (min)", "∞"],
                ["milestone_tokens", "Milestone every", "off"],
              ] as [keyof Budget, string, string][]
            ).map(([k, label, ph]) => (
              <label className="rc-field" key={k}>
                <span>{label}</span>
                <input
                  type="number"
                  min={0}
                  placeholder={ph}
                  value={(budget[k] as number | null) ?? ""}
                  onChange={(e) => setBudgetNum(k, e.target.value)}
                />
              </label>
            ))}
          </div>

          <label className="nd-check">
            <input
              type="checkbox"
              checked={autostart}
              onChange={(e) => setAutostart(e.target.checked)}
            />
            Start exploring immediately
          </label>

          {error && <div className="nd-error">{error}</div>}
        </div>

        <div className="nd-foot">
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={submitting}>
            {submitting ? "Starting…" : autostart ? "Explore ▸" : "Create"}
          </button>
        </div>
      </div>
    </>
  );
}
