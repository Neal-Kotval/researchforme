import { useEffect, useState } from "react";
import ModelPicker from "../ModelPicker";
import { modelLabel } from "../../types";
import type { Budget, Pace, Project } from "../../autonomous/types";
import type { ControlRequest } from "../../autonomous/api";

interface Props {
  project: Project;
  busy?: boolean;
  onControl: (req: ControlRequest) => void;
  /** Slim inline variant (transport + pace only) for the tab-strip header. */
  compact?: boolean;
}

const PACES: { id: Pace; label: string; sub: string }[] = [
  { id: "eco", label: "Eco", sub: "Subscription-safe" },
  { id: "balanced", label: "Balanced", sub: "Default" },
  { id: "sprint", label: "Sprint", sub: "Full throttle" },
];

function numOrNull(v: string): number | null {
  const n = parseInt(v, 10);
  return v.trim() === "" || Number.isNaN(n) ? null : n;
}

/**
 * Per-project run controls (SPEC §10.3): pause/resume (and a "Keep going" tap for
 * milestone check-ins), the pace dial, a budget + daily-cap editor, the
 * star-threshold slider, and the three-stage model config (reusing ModelPicker;
 * models are fixed at creation, shown read-only here).
 */
export default function RunControls({ project, busy, onControl, compact }: Props) {
  const [draft, setDraft] = useState<Budget>(project.budget);
  useEffect(() => setDraft(project.budget), [project.id]); // reset on tab switch

  const st = project.status;
  const running = st === "running";
  const milestone = st === "milestone_paused";
  const dirty = JSON.stringify(draft) !== JSON.stringify(project.budget);

  const setPace = (pace: Pace) => onControl({ action: "set_pace", pace });

  // Slim inline variant: just the transport button + a compact pace dial.
  if (compact) {
    return (
      <div className="run-controls compact">
        {milestone ? (
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => onControl({ action: "continue_milestone" })}>
            ▸ Keep going
          </button>
        ) : running ? (
          <button className="btn btn-sm" disabled={busy} onClick={() => onControl({ action: "pause" })}>
            ❚❚ Pause
          </button>
        ) : (
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => onControl({ action: "resume" })}>
            ▸ Resume
          </button>
        )}
        <div className="seg-control seg-mini" role="radiogroup" aria-label="Pace">
          {PACES.map((p) => (
            <button
              key={p.id} type="button" role="radio"
              aria-checked={project.budget.pace === p.id} aria-pressed={project.budget.pace === p.id}
              className="seg-option" disabled={busy} onClick={() => setPace(p.id)} title={p.sub}
            >
              <span className="so-label">{p.label}</span>
            </button>
          ))}
        </div>
      </div>
    );
  }
  const applyBudget = () => onControl({ action: "set_budget", budget: draft });
  const field = (k: keyof Budget, v: number | null) =>
    setDraft((d) => ({ ...d, [k]: v }));

  return (
    <div className="run-controls">
      {/* transport */}
      <div className="rc-transport">
        {milestone ? (
          <button
            className="btn btn-primary rc-keepgoing"
            disabled={busy}
            onClick={() => onControl({ action: "continue_milestone" })}
          >
            ▸ Keep going
          </button>
        ) : running ? (
          <button className="btn" disabled={busy} onClick={() => onControl({ action: "pause" })}>
            ❚❚ Pause
          </button>
        ) : (
          <button
            className="btn btn-primary"
            disabled={busy}
            onClick={() => onControl({ action: "resume" })}
          >
            ▸ Resume
          </button>
        )}
        {project.stats.stop_reason && !running && (
          <span className="rc-stop" title={project.stats.stop_reason}>
            {project.stats.stop_reason.replace(/_/g, " ")}
          </span>
        )}
      </div>

      {/* pace dial */}
      <div className="rc-block">
        <div
          className="rc-label"
          title="How fast the explorer burns tokens. Eco stays inside your subscription's comfort zone; Sprint spends freely."
        >
          Pace
        </div>
        <div className="seg-control" role="radiogroup" aria-label="Pace">
          {PACES.map((p) => {
            const active = project.budget.pace === p.id;
            return (
              <button
                key={p.id}
                type="button"
                role="radio"
                aria-checked={active}
                aria-pressed={active}
                className="seg-option"
                disabled={busy}
                onClick={() => setPace(p.id)}
                title={p.sub}
              >
                <span className="so-label">{p.label}</span>
                <span className="so-sub">{p.sub}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* star threshold */}
      <div className="rc-block">
        <div className="slider-top">
          <span
            className="rc-label"
            title="Ideas scoring at or above this viability get a star — a flag for your review, not a verdict."
          >
            Star threshold
          </span>
          <span className="slider-val">≥ {draft.star_threshold}</span>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          step={1}
          value={draft.star_threshold}
          onChange={(e) => field("star_threshold", Number(e.target.value))}
          aria-label="Star threshold"
        />
      </div>

      {/* budget editor */}
      <div className="rc-block">
        <div
          className="rc-label"
          title="Hard stops for the run. The explorer pauses itself when any cap is hit — nothing spends past these."
        >
          Budget &amp; caps
        </div>
        <div className="rc-grid">
          <label className="rc-field">
            <span>Max tokens</span>
            <input
              type="number"
              min={0}
              placeholder="∞"
              value={draft.max_tokens ?? ""}
              onChange={(e) => field("max_tokens", numOrNull(e.target.value))}
            />
          </label>
          <label className="rc-field">
            <span>Daily cap</span>
            <input
              type="number"
              min={0}
              placeholder="∞"
              value={draft.daily_cap_tokens ?? ""}
              onChange={(e) => field("daily_cap_tokens", numOrNull(e.target.value))}
            />
          </label>
          <label className="rc-field">
            <span>Max nodes</span>
            <input
              type="number"
              min={0}
              placeholder="∞"
              value={draft.max_nodes ?? ""}
              onChange={(e) => field("max_nodes", numOrNull(e.target.value))}
            />
          </label>
          <label className="rc-field">
            <span>Time limit (min)</span>
            <input
              type="number"
              min={0}
              placeholder="∞"
              value={draft.time_limit_minutes ?? ""}
              onChange={(e) => field("time_limit_minutes", numOrNull(e.target.value))}
            />
          </label>
          <label className="rc-field">
            <span>Milestone tokens</span>
            <input
              type="number"
              min={0}
              placeholder="off"
              value={draft.milestone_tokens || ""}
              onChange={(e) => field("milestone_tokens", numOrNull(e.target.value) ?? 0)}
            />
          </label>
        </div>
        <button
          className="btn btn-sm rc-apply"
          disabled={busy || !dirty}
          onClick={applyBudget}
        >
          {dirty ? "Apply budget" : "Saved"}
        </button>
      </div>

      {/* model policy (fixed at creation) */}
      <div className="rc-block">
        <div className="rc-label">Model policy</div>
        <div className="rc-models">
          <div className="rc-model-row">
            <span className="rcm-stage">Decompose</span>
            <span className="rcm-val">{modelLabel(project.decompose_model)}</span>
          </div>
          <div className="rc-model-row">
            <span className="rcm-stage">Synthesize</span>
            <span className="rcm-val">{modelLabel(project.synth_model)}</span>
          </div>
          <div className="rc-model-row">
            <span className="rcm-stage">Pressure-test</span>
            <span className="rcm-val">{modelLabel(project.pressure_model)}</span>
          </div>
        </div>
        <div className="rc-model-picker">
          <ModelPicker value={project.pressure_model} onChange={() => {}} compact disabled />
        </div>
      </div>
    </div>
  );
}
