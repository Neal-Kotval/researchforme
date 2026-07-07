import { useCallback, useMemo, useRef, useState } from "react";
import { viabilityRamp, type TreeNode } from "../../autonomous/types";

interface Props {
  nodes: Record<string, TreeNode>;
  rootId: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

const KIND_TICK: Record<string, string> = {
  domain: "var(--accent)",
  subarea: "var(--src-newsletter)",
  segment: "var(--src-arxiv)",
  gap_candidate: "var(--text-ghost)",
  gap: "var(--ramp-3)",
};

function isGapish(n: TreeNode): boolean {
  return n.kind === "gap" || n.kind === "gap_candidate";
}
function isLive(n: TreeNode): boolean {
  return (
    n.state === "expanding" ||
    n.state === "synthesizing" ||
    n.state === "pressure_testing"
  );
}

interface Row {
  node: TreeNode;
  depth: number;
  hasChildren: boolean;
  expanded: boolean;
}

/**
 * The exploration tree: indented, collapsible rows (Domain › SubArea › Segment ›
 * Gap). Gap rows carry a compact viability chip (neutral→vermillion ramp) and a
 * ⭐ when starred; live nodes pulse. A ⭐-only filter and a "viability ≥ N" slider
 * prune the view, and any node can be zoomed into (with a breadcrumb back out).
 * Branches sort by best-descendant viability so strong paths float up (SPEC §10.2).
 */
export default function ExplorationTree({ nodes, rootId, selectedId, onSelect }: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [starOnly, setStarOnly] = useState(false);
  const [viabMin, setViabMin] = useState(0);
  const [zoomId, setZoomId] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const rowRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  const domainId = useMemo(() => {
    if (rootId && nodes[rootId]) return rootId;
    const root = Object.values(nodes).find((n) => n.parent_id == null);
    return root?.id ?? null;
  }, [nodes, rootId]);

  // parent_id → children (authoritative; child_ids can lag the event stream).
  const childrenOf = useMemo(() => {
    const m: Record<string, TreeNode[]> = {};
    for (const n of Object.values(nodes)) {
      if (n.parent_id == null) continue;
      (m[n.parent_id] ??= []).push(n);
    }
    return m;
  }, [nodes]);

  // Aggregate viability of the *scored sub-ideas* under each node: `best` drives
  // branch ordering; `avg` gives a macro node one honest 0..100 score (the mean
  // of its descendant gaps) so the whole tree speaks a single viability scale
  // instead of mixing a 0..100 gap chip with a raw child-count on branches.
  const aggBelow = useMemo(() => {
    const memo = new Map<string, { best: number; sum: number; count: number }>();
    const visit = (id: string): { best: number; sum: number; count: number } => {
      const cached = memo.get(id);
      if (cached !== undefined) return cached;
      const node = nodes[id];
      let best = -1;
      let sum = 0;
      let count = 0;
      if (node && isGapish(node) && node.viability != null) {
        best = node.viability;
        sum = node.viability;
        count = 1;
      }
      for (const c of childrenOf[id] ?? []) {
        const r = visit(c.id);
        best = Math.max(best, r.best);
        sum += r.sum;
        count += r.count;
      }
      const agg = { best, sum, count };
      memo.set(id, agg);
      return agg;
    };
    for (const id of Object.keys(nodes)) visit(id);
    return memo;
  }, [nodes, childrenOf]);
  const bestBelow = useMemo(() => {
    const m = new Map<string, number>();
    for (const [id, a] of aggBelow) m.set(id, a.best);
    return m;
  }, [aggBelow]);
  const avgViab = useCallback(
    (id: string): number | null => {
      const a = aggBelow.get(id);
      return a && a.count > 0 ? Math.round(a.sum / a.count) : null;
    },
    [aggBelow]
  );

  const sortedChildren = useCallback(
    (id: string): TreeNode[] => {
      const kids = childrenOf[id] ?? [];
      return [...kids].sort((a, b) => {
        const bv = (bestBelow.get(b.id) ?? -1) - (bestBelow.get(a.id) ?? -1);
        if (bv !== 0) return bv;
        if (a.star !== b.star) return a.star ? -1 : 1;
        if (b.priority !== a.priority) return b.priority - a.priority;
        return a.title.localeCompare(b.title);
      });
    },
    [childrenOf, bestBelow]
  );

  const filterActive = starOnly || viabMin > 0;

  // When filtering, keep only matching gap nodes and their ancestor chain.
  const includeSet = useMemo(() => {
    if (!filterActive) return null;
    const keep = new Set<string>();
    for (const n of Object.values(nodes)) {
      if (!isGapish(n) || n.viability == null) continue;
      if (starOnly && !n.star) continue;
      if (n.viability < viabMin) continue;
      let cur: string | null = n.id;
      while (cur && !keep.has(cur)) {
        keep.add(cur);
        cur = nodes[cur]?.parent_id ?? null;
      }
    }
    return keep;
  }, [nodes, filterActive, starOnly, viabMin]);

  const zoomRootId = zoomId && nodes[zoomId] ? zoomId : domainId;

  // Flatten the visible tree into rows (for rendering + roving-tabindex nav).
  const rows = useMemo(() => {
    const out: Row[] = [];
    if (!zoomRootId) return out;
    const walk = (id: string, depth: number) => {
      const node = nodes[id];
      if (!node) return;
      if (includeSet && !includeSet.has(id)) return;
      let kids = sortedChildren(id);
      if (includeSet) kids = kids.filter((c) => includeSet.has(c.id));
      const hasChildren = kids.length > 0;
      const expanded = includeSet ? true : !collapsed.has(id);
      out.push({ node, depth, hasChildren, expanded });
      if (hasChildren && expanded) for (const c of kids) walk(c.id, depth + 1);
    };
    walk(zoomRootId, 0);
    return out;
  }, [zoomRootId, nodes, includeSet, sortedChildren, collapsed]);

  // Breadcrumb from the domain root down to the current zoom root.
  const crumbs = useMemo(() => {
    if (!zoomId || !nodes[zoomId]) return [];
    const chain: TreeNode[] = [];
    let cur: string | null = zoomId;
    while (cur && nodes[cur]) {
      chain.unshift(nodes[cur]);
      cur = nodes[cur].parent_id;
    }
    return chain;
  }, [zoomId, nodes]);

  const toggle = useCallback((id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }, []);

  const focusRow = useCallback((id: string | null) => {
    if (!id) return;
    setFocusedId(id);
    rowRefs.current.get(id)?.focus();
  }, []);

  const onRowKey = useCallback(
    (e: React.KeyboardEvent, row: Row) => {
      const idx = rows.findIndex((r) => r.node.id === row.node.id);
      if (e.key === "ArrowDown") {
        e.preventDefault();
        focusRow(rows[Math.min(rows.length - 1, idx + 1)]?.node.id ?? null);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        focusRow(rows[Math.max(0, idx - 1)]?.node.id ?? null);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        if (row.hasChildren && !row.expanded) toggle(row.node.id);
        else if (row.hasChildren) focusRow(rows[idx + 1]?.node.id ?? null);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        if (row.hasChildren && row.expanded) toggle(row.node.id);
        else focusRow(row.node.parent_id);
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onSelect(row.node.id);
      }
    },
    [rows, focusRow, toggle, onSelect]
  );

  if (!domainId) {
    return (
      <div className="tree-empty">
        <div className="tree-empty-ico">◲</div>
        <div className="tree-empty-t">Awaiting the first branches…</div>
        <div className="tree-empty-d">The explorer is decomposing the domain.</div>
      </div>
    );
  }

  // The single row that carries tabIndex=0 (roving tabindex); never stranded.
  const focusableId =
    focusedId && rows.some((r) => r.node.id === focusedId)
      ? focusedId
      : rows[0]?.node.id ?? null;

  return (
    <div className="exp-tree">
      {/* filter toolbar */}
      <div className="tree-toolbar">
        <button
          className={`tree-filter${starOnly ? " on" : ""}`}
          aria-pressed={starOnly}
          onClick={() => setStarOnly((v) => !v)}
          title="Show only starred gaps and their branches"
        >
          <span className="tf-star">★</span> Starred only
        </button>
        <label className="tree-viab">
          <span className="tv-lab">Viability ≥</span>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={viabMin}
            onChange={(e) => setViabMin(Number(e.target.value))}
            aria-label="Minimum viability filter"
          />
          <span className="tv-val">{viabMin}</span>
        </label>
        <span className="tree-count">{rows.length} shown</span>
      </div>

      {/* zoom breadcrumb */}
      {crumbs.length > 0 && (
        <div className="tree-crumbs" aria-label="Zoom breadcrumb">
          <button className="crumb" onClick={() => setZoomId(null)}>
            All
          </button>
          {crumbs.map((c, i) => (
            <span key={c.id} className="crumb-wrap">
              <span className="crumb-sep">›</span>
              <button
                className={`crumb${i === crumbs.length - 1 ? " current" : ""}`}
                onClick={() => setZoomId(c.id)}
              >
                {c.title}
              </button>
            </span>
          ))}
        </div>
      )}

      {/* the rows */}
      <div className="tree-rows" role="tree" aria-label="Exploration tree">
        {rows.map((row) => {
          const n = row.node;
          const selected = n.id === selectedId;
          const gapish = isGapish(n);
          const live = isLive(n);
          const pruned = n.state === "pruned";
          const tick = KIND_TICK[n.kind] ?? "var(--text-ghost)";
          const isFocusable = n.id === focusableId;
          return (
            <div
              key={n.id}
              ref={(el) => {
                if (el) rowRefs.current.set(n.id, el);
                else rowRefs.current.delete(n.id);
              }}
              role="treeitem"
              aria-level={row.depth + 1}
              aria-selected={selected}
              aria-expanded={row.hasChildren ? row.expanded : undefined}
              tabIndex={isFocusable ? 0 : -1}
              className={
                "tree-row" +
                (selected ? " selected" : "") +
                (live ? " live" : "") +
                (pruned ? " pruned" : "")
              }
              style={{ paddingLeft: 8 + row.depth * 16 }}
              onClick={() => {
                setFocusedId(n.id);
                onSelect(n.id);
              }}
              onKeyDown={(e) => onRowKey(e, row)}
              title={n.rationale || n.title}
            >
              <span className="tr-tick" style={{ background: tick }} aria-hidden />
              {row.hasChildren ? (
                <button
                  className={`tr-caret${row.expanded ? " open" : ""}`}
                  aria-label={row.expanded ? "Collapse" : "Expand"}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (!includeSet) toggle(n.id);
                  }}
                  tabIndex={-1}
                >
                  ▸
                </button>
              ) : (
                <span className="tr-caret empty" aria-hidden />
              )}

              <span className="tr-title">{n.title}</span>

              {gapish && n.gap?.sub_segment && (
                <span className="tr-sub">{n.gap.sub_segment}</span>
              )}

              <span className="tr-right">
                {live && <span className="tr-live" aria-label={n.state} />}
                {!gapish && (() => {
                  const av = avgViab(n.id);
                  const kids = (childrenOf[n.id] ?? []).length;
                  return (
                    <>
                      {kids > 0 && (
                        <span className="tr-childcount" title={`${kids} sub-branches`}>{kids}</span>
                      )}
                      {av != null && (
                        <span
                          className="viab-chip avg"
                          style={{ background: viabilityRamp(av) }}
                          title={`Average viability of scored sub-ideas: ${av}/100`}
                        >
                          ⌀{av}
                        </span>
                      )}
                    </>
                  );
                })()}
                {gapish && n.viability != null ? (
                  <span
                    className={`viab-chip${n.star ? " star" : ""}`}
                    style={{ background: viabilityRamp(n.viability) }}
                    title={`Viability ${n.viability} · ${n.confidence ?? "?"} confidence${
                      n.pressure_test ? ` · ${n.pressure_test.test_rigor} test` : ""
                    }`}
                  >
                    {n.star && <span className="vc-star">★</span>}
                    {n.viability}
                  </span>
                ) : gapish ? (
                  <span className="viab-chip pending" title={n.state}>
                    {live ? "…" : "—"}
                  </span>
                ) : null}
                <button
                  className={`tr-zoom${zoomId === n.id ? " on" : ""}`}
                  aria-label="Zoom into this branch"
                  title="Zoom into this branch"
                  onClick={(e) => {
                    e.stopPropagation();
                    setZoomId(n.id);
                  }}
                  tabIndex={-1}
                >
                  ⤢
                </button>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
