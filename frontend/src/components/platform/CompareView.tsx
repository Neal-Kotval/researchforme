import { nodeTrust } from "../../autonomous/types";
import FitChip from "../autonomous/FitChip";
import ViabChip from "../autonomous/ViabChip";
import PortfolioScatter from "./PortfolioScatter";
import { usePressureTestedIdeas } from "./usePressureTestedIdeas";

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
  const survivors = ideas.filter((i) => (i.node.pressure_test?.survived ?? 0) > 0);

  return (
    <div className="pf-view w940">
      {/* 1 — the whole portfolio (H1 2×2) */}
      <section className="pfm">
        <div className="pfm-head">
          <div className="pfm-title">The whole portfolio</div>
          <div className="pfm-sub">
            Every scored gap across your runs, placed by founder fit × viability. Dots carry
            their trust — hover for the story, click to open the dossier.
          </div>
        </div>
        <PortfolioScatter onOpenNode={onOpenNode} />
      </section>

      {/* 2 — the shortlist (red-team survivors) */}
      <section className="pfm">
        <div className="pfm-head">
          <div className="pfm-title">Shortlist</div>
          <div className="pfm-sub">
            Only ideas that survived the red team make this table, ranked by fit × viability.
          </div>
        </div>
        {loading ? (
          <div className="pf-empty" aria-busy="true">Ranking the survivors…</div>
        ) : error ? (
          <div className="pf-empty" role="alert">⚠︎ {error}</div>
        ) : survivors.length === 0 ? (
          <div className="pf-empty">
            Nothing has survived a red team yet — the shortlist fills as runs
            pressure-test their candidates.
            <br />
            <button className="btn btn-primary" onClick={onNewExploration}>＋ New exploration</button>
          </div>
        ) : (
          <>
            <div className="cmp-intro">
              {survivors.length} survivor{survivors.length === 1 ? "" : "s"} across your runs.
              Every score carries its provenance — trust or discount it yourself.
            </div>
            <div className="cmp-scroll">
              <div className="cmp-table" role="table" aria-label="Shortlist">
                <div className="cmp-row head" role="row">
                  <span>Viab</span><span>Fit</span><span>Idea</span><span>Provenance</span><span />
                </div>
                {survivors.slice(0, 8).map((i, idx) => {
                  const n = i.node;
                  const pt = n.pressure_test!;
                  const signals = pt.lenses.reduce((s, l) => s + l.evidence.length, 0);
                  const lead = idx === 0;
                  return (
                    <div className={`cmp-row${lead ? " lead" : ""}`} role="row" key={n.id}>
                      <ViabChip value={n.viability} trust={nodeTrust(n)} star={n.star} />
                      <span><FitChip value={n.fit} /></span>
                      <div className="cmp-idea">
                        <b>{n.gap?.title ?? n.title}</b>
                        <small> · {i.domain}</small>
                      </div>
                      <span className="cmp-prov">
                        {signals} signal{signals === 1 ? "" : "s"} · rt {pt.survived}/{pt.lenses.length}
                      </span>
                      <button
                        className={lead ? "btn btn-primary btn-sm" : "btn btn-sm"}
                        onClick={() => onOpenNode(i.pid, n.id)}
                      >
                        Choose
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="cmp-note">
              Choosing one opens its full dossier in the explorer — the gap, its evidence, and the
              lenses it survived (and the ones it didn't).
            </div>
          </>
        )}
      </section>
    </div>
  );
}
