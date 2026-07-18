import { useState } from "react";
import { nodeTrust, type TreeNode } from "../../autonomous/types";
import FitChip from "../autonomous/FitChip";
import ViabChip from "../autonomous/ViabChip";
import PortfolioScatter from "./PortfolioScatter";
import { usePressureTestedIdeas } from "./usePressureTestedIdeas";
import { Button, Card, SectionHeader, Table } from "../ui";

/** The novelty verdict — the honest second axis: is this space open or taken? */
function NoveltyChip({ node }: { node: TreeNode }) {
  const nv = node.novelty_scan;
  if (!nv) return <span className="nv-chip nv-none" title="Not yet scanned against funded incumbents">—</span>;
  const v = nv.verdict || "—";
  return (
    <span className={`nv-chip nv-${v}`}
      title={`${nv.novelty_0_100}/100 — ${nv.rationale || v}${nv.structural_risk ? `  ·  ⚠ ${nv.structural_risk}` : ""}`}>
      {v} · {nv.novelty_0_100}
    </span>
  );
}

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
 * Compare (v3): the whole portfolio on the 2×2, then the shortlist as a minimal
 * table — hairline rows, tabular numbers, all six columns, the top pick's
 * "Choose" as the screen's one primary.
 */
export default function CompareView({ onOpenNode, onNewExploration }: Props) {
  const { ideas, loading, error } = usePressureTestedIdeas();
  const [hideOccupied, setHideOccupied] = useState(false);
  const allSurvivors = ideas.filter((i) => (i.node.pressure_test?.survived ?? 0) > 0);
  const occupiedCount = allSurvivors.filter((i) => i.node.novelty_scan?.verdict === "occupied").length;
  const survivors = hideOccupied
    ? allSurvivors.filter((i) => i.node.novelty_scan?.verdict !== "occupied")
    : allSurvivors;

  return (
    <div className="pf-view w1000 ui-page">
      {/* 1 — the whole portfolio (2×2) */}
      <section>
        <SectionHeader title="The whole portfolio"
          sub="Every scored gap across your runs, placed by founder fit × viability. Dots carry their trust — hover for the story, click to open the dossier." />
        <PortfolioScatter onOpenNode={onOpenNode} />
      </section>

      {/* 2 — the shortlist */}
      <section>
        <SectionHeader title="Shortlist"
          sub="Only ideas that survived the red team make this table, ranked by fit × viability." />

        {loading ? (
          <Card pad><div className="ui-empty"><div className="ui-empty-title">Ranking the survivors…</div><div className="ui-empty-body">Weighing each red-team survivor by fit × viability and pulling its provenance.</div></div></Card>
        ) : error ? (
          <Card pad><div className="ui-empty"><div className="ui-empty-title">Couldn't load the shortlist</div><div className="ui-empty-body">{error}</div></div></Card>
        ) : survivors.length === 0 ? (
          <Card pad>
            <div className="ui-empty">
              <div className="ui-empty-title">No survivors yet</div>
              <div className="ui-empty-body">Nothing has survived a red team yet — the shortlist fills as runs pressure-test their candidates and the strongest ideas come through.</div>
              <div className="ui-empty-cta"><Button variant="primary" iconLeft="＋" onClick={onNewExploration}>New exploration</Button></div>
            </div>
          </Card>
        ) : (
          <>
            <div className="cmp-intro2">
              <span><b>{survivors.length} survivor{survivors.length === 1 ? "" : "s"}</b> across your runs. Viability says the space is real; <b>Novelty</b> says whether it's still open or already owned — weigh both.</span>
              {occupiedCount > 0 && (
                <label className="cmp-filter2" title="Hide ideas the novelty scan found already occupied by a funded incumbent">
                  <input type="checkbox" checked={hideOccupied} onChange={(e) => setHideOccupied(e.target.checked)} />
                  Hide occupied ({occupiedCount})
                </label>
              )}
            </div>
            <Table ariaLabel="Shortlist" head={<>
              <th>Viab</th><th>Fit</th><th>Novelty</th><th>Idea</th><th>Worth</th><th>Provenance</th><th aria-label="Choose" />
            </>}>
              {survivors.slice(0, 8).map((i, idx) => {
                const n = i.node;
                const pt = n.pressure_test!;
                const signals = pt.lenses.reduce((s, l) => s + l.evidence.length, 0);
                const lead = idx === 0;
                const roi = roiHeadline(n);
                return (
                  <tr key={n.id}>
                    <td><ViabChip value={n.viability} trust={nodeTrust(n)} star={n.star} /></td>
                    <td><FitChip value={n.fit} /></td>
                    <td><NoveltyChip node={n} /></td>
                    <td>
                      <div className="cmp-idea2">
                        <span className="cmp-rank2">#{idx + 1}</span>
                        <b>{n.gap?.title ?? n.title}</b>
                        <small> · {i.domain}</small>
                      </div>
                    </td>
                    <td>{roi ? <span className="ui-chip ui-chip--slate ui-chip--sm" title={roi.full}>{roi.text}</span> : <span style={{ color: "var(--text-tertiary)" }}>—</span>}</td>
                    <td>{signals} signal{signals === 1 ? "" : "s"} · rt {pt.survived}/{pt.lenses.length}</td>
                    <td className="ui-table-num"><Button variant={lead ? "primary" : "quiet"} size="sm" onClick={() => onOpenNode(i.pid, n.id)}>Choose</Button></td>
                  </tr>
                );
              })}
            </Table>
            <div className="cmp-note2">
              Choosing one opens its full dossier in the explorer — the gap, its evidence, and the lenses it survived (and the ones it didn't).
            </div>
          </>
        )}
      </section>
    </div>
  );
}
