import { useMemo } from "react";
import { nodeTrust, type TreeNode } from "../../autonomous/types";
import FitChip from "./FitChip";
import ViabChip from "./ViabChip";

interface Props {
  nodes: Record<string, TreeNode>;
  onSelect: (id: string) => void;
  topN?: number;
}

function isGap(n: TreeNode): boolean {
  return (n.kind === "gap" || n.kind === "gap_candidate") && n.viability != null;
}

/**
 * The "come back later" surface (SPEC §10.4): the top starred gaps and the newest
 * high-viability finds, each a one-click jump into the tree. This is the thing you
 * actually open when you return to a long-running exploration.
 */
export default function ProjectDigest({ nodes, onSelect, topN = 6 }: Props) {
  const { starred, recent } = useMemo(() => {
    const gaps = Object.values(nodes).filter(isGap);
    const starred = gaps
      .filter((n) => n.star)
      .sort((a, b) => (b.viability ?? 0) - (a.viability ?? 0))
      .slice(0, topN);
    const recent = [...gaps]
      .filter((n) => (n.viability ?? 0) >= 55)
      .sort((a, b) => (b.updated_at > a.updated_at ? 1 : -1))
      .slice(0, topN);
    return { starred, recent };
  }, [nodes, topN]);

  const row = (n: TreeNode) => (
    <button key={n.id} className="digest-row" onClick={() => onSelect(n.id)}>
      <ViabChip
        value={n.viability}
        trust={nodeTrust(n)}
        star={n.star}
        title={
          nodeTrust(n) === "unverified"
            ? `Viability ${n.viability} — unverified`
            : `Viability ${n.viability}`
        }
      />
      {n.fit != null && (
        <FitChip
          value={n.fit}
          title={`Founder fit ${n.fit} — how attackable this space is for YOU, from your steering. Orthogonal to viability.`}
        />
      )}
      <span className="dr-body">
        <span className="dr-title">{n.gap?.title ?? n.title}</span>
        {n.gap?.sub_segment && <span className="dr-sub">{n.gap.sub_segment}</span>}
      </span>
      <span className={`conf-pill ${n.confidence ?? "low"}`}>{n.confidence ?? "—"}</span>
    </button>
  );

  const empty = starred.length === 0 && recent.length === 0;

  return (
    <div className="digest">
      <div className="digest-head">
        <div className="eyebrow">Digest</div>
        <h4 className="digest-title">Worth your attention</h4>
        <div className="mod-sub">
          Ideas that survived the red team, ranked by how much they deserve your attention.
        </div>
      </div>

      {empty ? (
        <div className="digest-empty">
          No strong finds yet. Ideas land here once they score 55+ after pressure-testing —
          let the run keep going, or lower the star threshold in Controls.
        </div>
      ) : (
        <>
          {starred.length > 0 && (
            <div className="digest-sec">
              <div className="digest-sec-h">
                <span className="ds-star">★</span> Top starred gaps
              </div>
              <div className="digest-list">{starred.map(row)}</div>
            </div>
          )}
          {recent.length > 0 && (
            <div className="digest-sec">
              <div className="digest-sec-h">Newest high-viability finds</div>
              <div className="digest-list">{recent.map(row)}</div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
