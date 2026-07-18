import { useState } from "react";
import { nodeTrust, type TreeNode } from "../../autonomous/types";
import { Reveal, Stagger, StaggerItem } from "../../motion";
import FitChip from "../autonomous/FitChip";
import ViabChip from "../autonomous/ViabChip";
import PortfolioScatter from "./PortfolioScatter";
import { usePressureTestedIdeas } from "./usePressureTestedIdeas";
import "./CompareView.rd.css";

/** The novelty verdict — the honest second axis: is this space open or taken? */
function NoveltyChip({ node }: { node: TreeNode }) {
  const nv = node.novelty_scan;
  if (!nv) return <span className="nv-chip nv-none" title="Not yet scanned against funded incumbents">—</span>;
  const v = nv.verdict || "—";
  return (
    <span
      className={`nv-chip nv-${v}`}
      title={`${nv.novelty_0_100}/100 — ${nv.rationale || v}${nv.structural_risk ? `  ·  ⚠ ${nv.structural_risk}` : ""}`}
    >
      {v} · {nv.novelty_0_100}
    </span>
  );
}

/** Pull the headline dollar figure out of a value-model's annual_value prose. */
function roiHeadline(node: TreeNode): { text: string; full: string } | null {
  const vm = node.value_model;
  if (!vm?.annual_value) return null;
  const matches = vm.annual_value.match(/\$[\d,]+(?:\.\d+)?\s*[KkMmBb]?(?:\/yr)?/g);
  const text = matches && matches.length ? matches[matches.length - 1] : "ROI";
  return { text, full: vm.annual_value };
}

interface Props {
  onOpenNode: (pid: string, nodeId: string) => void;
  onNewExploration: () => void;
}

/**
 * The weighing screen: the whole portfolio on the H1 2×2 (every scored gap
 * across every run, fit × viability, trust-encoded), then the shortlist
 * (design handoff §4) — survivors ranked in a provenance-bearing table. The
 * lead row wears the highlighter tint; Choose opens the idea in the explorer
 * where the full dossier lives.
 */
export default function CompareView({ onOpenNode, onNewExploration }: Props) {
  const { ideas, loading, error } = usePressureTestedIdeas();
  const [hideOccupied, setHideOccupied] = useState(false);
  const allSurvivors = ideas.filter((i) => (i.node.pressure_test?.survived ?? 0) > 0);
  const occupiedCount = allSurvivors.filter(
    (i) => i.node.novelty_scan?.verdict === "occupied",
  ).length;
  const survivors = hideOccupied
    ? allSurvivors.filter((i) => i.node.novelty_scan?.verdict !== "occupied")
    : allSurvivors;

  return (
    <div className="pf-view w940 rd-compare">
      {/* 1 — the whole portfolio (H1 2×2) */}
      <Reveal as="section" className="pfm">
        <div className="pfm-head rd-head">
          <div className="rd-eyebrow">Portfolio · fit × viability</div>
          <div className="pfm-title">The whole portfolio</div>
          <div className="pfm-sub">
            Every scored gap across your runs, placed by founder fit × viability. Dots carry
            their trust — hover for the story, click to open the dossier.
          </div>
        </div>
        <PortfolioScatter onOpenNode={onOpenNode} />
      </Reveal>

      {/* 2 — the shortlist (red-team survivors) */}
      <Reveal as="section" className="pfm" delay={0.06}>
        <div className="pfm-head rd-head">
          <div className="rd-eyebrow">Shortlist · red-team survivors</div>
          <div className="pfm-title">Shortlist</div>
          <div className="pfm-sub">
            Only ideas that survived the red team make this table, ranked by fit × viability.
          </div>
        </div>
        {loading ? (
          <div className="pf-empty rd-empty" aria-busy="true">
            <div className="rd-empty-glyph" aria-hidden="true">⏳</div>
            <div className="rd-empty-title">Ranking the survivors…</div>
            <div className="rd-empty-body">
              Weighing each red-team survivor by fit × viability and pulling its provenance.
            </div>
          </div>
        ) : error ? (
          <div className="pf-empty rd-empty" role="alert">
            <div className="rd-empty-glyph" aria-hidden="true">⚠︎</div>
            <div className="rd-empty-title">Couldn't load the shortlist</div>
            <div className="rd-empty-body">{error}</div>
          </div>
        ) : survivors.length === 0 ? (
          <div className="pf-empty rd-empty">
            <div className="rd-empty-glyph" aria-hidden="true">🛡︎</div>
            <div className="rd-empty-title">No survivors yet</div>
            <div className="rd-empty-body">
              Nothing has survived a red team yet — the shortlist fills as runs
              pressure-test their candidates and the strongest ideas come through.
            </div>
            <button className="btn btn-primary" onClick={onNewExploration}>＋ New exploration</button>
          </div>
        ) : (
          <>
            <div className="cmp-intro">
              <span className="cmp-count">
                {survivors.length} survivor{survivors.length === 1 ? "" : "s"}
              </span>
              <span>
                across your runs. Viability says the space is real; <b>Novelty</b> says whether
                it's still open or already owned — weigh both.
              </span>
              {occupiedCount > 0 && (
                <label className="cmp-filter" title="Hide ideas the novelty scan found already occupied by a funded incumbent">
                  <input
                    type="checkbox"
                    checked={hideOccupied}
                    onChange={(e) => setHideOccupied(e.target.checked)}
                  />
                  Hide occupied ({occupiedCount})
                </label>
              )}
            </div>
            <div className="cmp-scroll">
              <div className="cmp-table" role="table" aria-label="Shortlist">
                <div className="cmp-row head" role="row">
                  <span>Viab</span><span>Fit</span><span>Novelty</span><span>Idea</span>
                  <span>Worth</span><span>Provenance</span><span />
                </div>
                <Stagger className="cmp-rows" delay={0.05}>
                  {survivors.slice(0, 8).map((i, idx) => {
                    const n = i.node;
                    const pt = n.pressure_test!;
                    const signals = pt.lenses.reduce((s, l) => s + l.evidence.length, 0);
                    const lead = idx === 0;
                    return (
                      <StaggerItem
                        className={`cmp-row${lead ? " lead" : ""}`}
                        role="row"
                        key={n.id}
                      >
                        <ViabChip value={n.viability} trust={nodeTrust(n)} star={n.star} />
                        <span><FitChip value={n.fit} /></span>
                        <span><NoveltyChip node={n} /></span>
                        <div className="cmp-idea">
                          <b>
                            <span className="cmp-rank">#{idx + 1}</span>
                            {n.gap?.title ?? n.title}
                          </b>
                          <small> · {i.domain}</small>
                        </div>
                        <span className="cmp-worth">
                          {(() => {
                            const roi = roiHeadline(n);
                            return roi
                              ? <span className="worth-chip" title={roi.full}>{roi.text}</span>
                              : <span className="worth-none">—</span>;
                          })()}
                        </span>
                        <span className="cmp-prov">
                          {signals} signal{signals === 1 ? "" : "s"} · rt {pt.survived}/{pt.lenses.length}
                        </span>
                        <button
                          className={lead ? "btn btn-primary btn-sm" : "btn btn-sm"}
                          onClick={() => onOpenNode(i.pid, n.id)}
                        >
                          Choose
                        </button>
                      </StaggerItem>
                    );
                  })}
                </Stagger>
              </div>
            </div>
            <div className="cmp-note">
              Choosing one opens its full dossier in the explorer — the gap, its evidence, and the
              lenses it survived (and the ones it didn't).
            </div>
          </>
        )}
      </Reveal>
    </div>
  );
}
