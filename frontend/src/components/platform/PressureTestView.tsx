import { useEffect, useState } from "react";
import {
  isUnevaluatedLens,
  nodeTrust,
  type LensVerdict,
  type PressureTest,
} from "../../autonomous/types";
import FitChip from "../autonomous/FitChip";
import ViabChip from "../autonomous/ViabChip";
import { usePressureTestedIdeas } from "./usePressureTestedIdeas";

interface Props {
  onOpenNode: (pid: string, nodeId: string) => void;
  onNewExploration: () => void;
}

function verdictOf(l: LensVerdict, pt: PressureTest): "survives" | "weakens" | "kills" | "none" {
  return isUnevaluatedLens(l, pt.test_rigor) ? "none" : l.verdict;
}

const VERDICT_LABEL = { survives: "survives", weakens: "weakens", kills: "kills", none: "not evaluated" } as const;

/** Lens ids arrive snake_cased ("demand_mirage") — humanize for display. */
function lensName(id: string): string {
  const s = id.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * The red-team screen (design handoff §3): one candidate, six adversarial
 * lenses, each with its verdict pill, finding, and provenance line. Real data —
 * every pressure-tested gap across projects; a picker switches the candidate.
 */
export default function PressureTestView({ onOpenNode, onNewExploration }: Props) {
  const { ideas, loading, error } = usePressureTestedIdeas();
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => {
    if (sel == null && ideas.length > 0) setSel(ideas[0].node.id);
  }, [ideas, sel]);

  if (loading) {
    return (
      <div className="pf-view w880">
        <div className="pf-empty" aria-busy="true">Loading pressure-test results…</div>
      </div>
    );
  }
  if (error) {
    return (
      <div className="pf-view w880">
        <div className="pf-empty" role="alert">⚠︎ {error}</div>
      </div>
    );
  }
  if (ideas.length === 0) {
    return (
      <div className="pf-view w880">
        <div className="pf-empty">
          No candidate has reached the red team yet. Ideas land here once a run
          scores them and the six adversarial lenses have had their turn.
          <br />
          <button className="btn btn-primary" onClick={onNewExploration}>＋ New exploration</button>
        </div>
      </div>
    );
  }

  const current = ideas.find((i) => i.node.id === sel) ?? ideas[0];
  const n = current.node;
  const pt = n.pressure_test!;
  const kills = pt.lenses.filter((l) => verdictOf(l, pt) === "kills").length;

  return (
    <div className="pf-view w880">
      {ideas.length > 1 && (
        <div className="pt-picker" role="tablist" aria-label="Pressure-tested candidates">
          {ideas.slice(0, 6).map((i) => (
            <button
              key={i.node.id}
              role="tab"
              aria-selected={i.node.id === n.id}
              className={i.node.id === n.id ? "on" : ""}
              onClick={() => setSel(i.node.id)}
            >
              {i.node.gap?.title ?? i.node.title}
              {i.node.triage && (
                <span className={`pt-triage ${i.node.triage}`}>
                  {i.node.triage === "interested" ? "interested" : "passed"}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      <div className="pt-head">
        <div>
          <div className="pt-eyebrow">
            Red team · {current.domain}
            {n.triage && (
              <span
                className={`pt-triage ${n.triage}`}
                title={
                  n.triage === "interested"
                    ? "You marked this space interested."
                    : `You passed on this space${n.triage_reason ? ` — ${n.triage_reason.replace(/_/g, " ")}` : ""}.`
                }
              >
                {n.triage === "interested" ? "interested" : "passed"}
              </span>
            )}
          </div>
          <div className="pt-title">{n.gap?.title ?? n.title}</div>
        </div>
        <div className="pt-score">
          <b>{n.viability ?? "—"}</b>
          <small>viability · {pt.test_rigor} rigor</small>
        </div>
      </div>

      <div className="pt-lenses">
        {pt.lenses.map((l) => {
          const v = verdictOf(l, pt);
          return (
            <div className="pt-lens" key={l.lens}>
              <span className={`pf-verdict ${v}`}>
                {v !== "none" && <span className="pf-dot" />}
                {VERDICT_LABEL[v]}
              </span>
              <div className="pt-lens-body">
                <div className="pt-lens-name">{lensName(l.lens)}</div>
                <div className="pt-lens-finding">{l.argument}</div>
                <div className="pt-lens-meta">
                  {v === "none"
                    ? "no rigor · runs next window"
                    : `${pt.test_rigor} rigor · ${l.evidence.length} signal${l.evidence.length === 1 ? "" : "s"} cited`}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="pt-foot">
        <div className="pt-foot-text">
          {pt.summary ? (
            <>
              <b>Survived {pt.survived} of {pt.lenses.length}</b>
              {kills > 0 && <> · {kills} kill{kills === 1 ? "" : "s"}</>} — {pt.summary}
            </>
          ) : (
            <>Survived <b>{pt.survived} of {pt.lenses.length}</b> lenses at {pt.test_rigor} rigor.</>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, flex: "none", alignItems: "center" }}>
          <ViabChip value={n.viability} trust={nodeTrust(n)} star={n.star} />
          <FitChip value={n.fit} labeled />
          <button className="btn btn-primary" onClick={() => onOpenNode(current.pid, n.id)}>
            Open in explorer ▸
          </button>
        </div>
      </div>
    </div>
  );
}
