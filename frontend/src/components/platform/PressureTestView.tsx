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
import { Button, Card, Chip, Segmented } from "../ui";

interface Props {
  onOpenNode: (pid: string, nodeId: string) => void;
  onNewExploration: () => void;
}

type Verdict = "survives" | "weakens" | "kills" | "none";
function verdictOf(l: LensVerdict, pt: PressureTest): Verdict {
  return isUnevaluatedLens(l, pt.test_rigor) ? "none" : l.verdict;
}

/** The verdict as a quiet chip — accent only for survives, danger for kills. */
function VerdictChip({ v }: { v: Verdict }) {
  if (v === "survives") return <Chip tone="tint" dot="accent">Survives</Chip>;
  if (v === "weakens") return <Chip tone="slate" dot="slate">Weakens</Chip>;
  if (v === "kills") return <Chip tone="danger" dot="danger">Killed</Chip>;
  return <Chip tone="outline">Not evaluated</Chip>;
}

/** Lens ids arrive snake_cased ("demand_mirage") — humanize for display. */
function lensName(id: string): string {
  const s = id.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * Pressure-test (v3): one candidate, six adversarial lenses as flat rows with a
 * verdict chip and clamped finding, and a survival summary strip with the score
 * and the screen's one primary — "Open in explorer".
 */
export default function PressureTestView({ onOpenNode, onNewExploration }: Props) {
  const { ideas, loading, error } = usePressureTestedIdeas();
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => {
    if (sel == null && ideas.length > 0) setSel(ideas[0].node.id);
  }, [ideas, sel]);

  if (loading) return <div className="pf-view ui-page"><Card pad><EmptyLine text="Loading pressure-test results…" /></Card></div>;
  if (error) return <div className="pf-view ui-page"><Card pad><EmptyLine text={`⚠︎ ${error}`} alert /></Card></div>;
  if (ideas.length === 0) {
    return (
      <div className="pf-view ui-page">
        <Card pad>
          <div className="ui-empty">
            <div className="ui-empty-title">No candidate has reached the red team yet</div>
            <div className="ui-empty-body">Ideas land here once a run scores them and the six adversarial lenses have had their turn.</div>
            <div className="ui-empty-cta"><Button variant="primary" iconLeft="＋" onClick={onNewExploration}>New exploration</Button></div>
          </div>
        </Card>
      </div>
    );
  }

  const current = ideas.find((i) => i.node.id === sel) ?? ideas[0];
  const n = current.node;
  const pt = n.pressure_test!;
  const kills = pt.lenses.filter((l) => verdictOf(l, pt) === "kills").length;

  return (
    <div className="pf-view ui-page">
      {ideas.length > 1 && (
        <Segmented
          items={ideas.slice(0, 6).map((i) => ({
            id: i.node.id,
            label: (
              <>
                {i.node.gap?.title ?? i.node.title}
                {i.node.triage && <span style={{ color: "var(--text-tertiary)" }}> · {i.node.triage === "interested" ? "interested" : "passed"}</span>}
              </>
            ),
          }))}
          value={n.id}
          onChange={setSel}
          ariaLabel="Pressure-tested candidates"
        />
      )}

      <section>
        <div className="rt-header">
          <div className="rt-eyebrow">
            Red team · {current.domain}
            {n.triage && (
              <Chip tone="outline">{n.triage === "interested" ? "interested" : "passed"}</Chip>
            )}
          </div>
          <div className="rt-title">{n.gap?.title ?? n.title}</div>
        </div>

        <div className="rt-lenslist">
          {pt.lenses.map((l) => {
            const v = verdictOf(l, pt);
            return (
              <div className="rt-lensrow" key={l.lens}>
                <div className="rt-lens-vcol"><VerdictChip v={v} /></div>
                <div className="rt-lens-main">
                  <div className="rt-lens-name">{lensName(l.lens)}</div>
                  <div className="rt-lens-finding ui-clamp ui-clamp-2" title={l.argument}>{l.argument}</div>
                  <div className="rt-lens-meta">
                    {v === "none"
                      ? "Not evaluated · runs next window"
                      : `${pt.test_rigor} rigor · ${l.evidence.length} signal${l.evidence.length === 1 ? "" : "s"} cited`}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <Card pad>
          <div className="rt-summary">
            <div className="rt-summary-text">
              {pt.summary ? (
                <><b>Survived {pt.survived} of {pt.lenses.length}</b>{kills > 0 && <> · {kills} kill{kills === 1 ? "" : "s"}</>} — {pt.summary}</>
              ) : (
                <>Survived <b>{pt.survived} of {pt.lenses.length}</b> lenses at {pt.test_rigor} rigor.</>
              )}
            </div>
            <div className="rt-summary-actions">
              <ViabChip value={n.viability} trust={nodeTrust(n)} star={n.star} />
              <FitChip value={n.fit} labeled />
              <Button variant="primary" onClick={() => onOpenNode(current.pid, n.id)}>Open in explorer</Button>
            </div>
          </div>
        </Card>
      </section>
    </div>
  );
}

function EmptyLine({ text, alert }: { text: string; alert?: boolean }) {
  return <div className="ui-empty-body" role={alert ? "alert" : undefined} style={{ textAlign: "center", maxWidth: "none" }}>{text}</div>;
}
