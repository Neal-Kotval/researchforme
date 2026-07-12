import { useMemo, useCallback, useEffect, type CSSProperties } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
  useReactFlow,
  ReactFlowProvider,
  type Node as RFNode,
  type Edge as RFEdge,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "dagre";
import { nodeTrust, viabilityRamp, type TreeNode } from "../../autonomous/types";

interface Props {
  nodes: Record<string, TreeNode>;
  rootId: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

const NODE_W = 208;
const NODE_H = 56;

const KIND_LABEL: Record<string, string> = {
  domain: "Domain", subarea: "Sub-area", segment: "Segment",
  gap_candidate: "Candidate", gap: "Gap",
};

function isGapish(k: string) { return k === "gap" || k === "gap_candidate"; }
function isLive(s: string) { return s === "expanding" || s === "synthesizing" || s === "pressure_testing"; }

/* ------------------------------------------------------------- custom node -- */
type NodeData = {
  node: TreeNode;
  selected: boolean;
  onSelect: (id: string) => void;
};

function GraphNode({ data }: NodeProps) {
  const { node, selected, onSelect } = data as unknown as NodeData;
  const gapish = isGapish(node.kind);
  const live = isLive(node.state);
  const viab = gapish ? node.viability : null;

  return (
    <div
      className={`gnode k-${node.kind}${selected ? " sel" : ""}${live ? " live" : ""}${node.star ? " star" : ""}`}
      onClick={() => onSelect(node.id)}
      title={node.title}
    >
      <Handle type="target" position={Position.Top} className="gnode-handle" />
      <div className="gnode-row">
        <span className="gnode-kind">{node.star && <span className="gnode-star">★</span>}{KIND_LABEL[node.kind] ?? node.kind}</span>
        {viab != null && (() => {
          // Trust encoding (memo §2) reaches the canvas too: only an earned
          // score wears the solid ramp fill; unverified goes dashed + faded.
          const trust = nodeTrust(node);
          const ramp = viabilityRamp(viab);
          const style =
            trust === "earned"
              ? { background: ramp, color: viab >= 55 ? "#fff" : "var(--text)" }
              : ({ "--chip-ramp": ramp } as CSSProperties);
          return (
            <span
              className={`gnode-viab trust-${trust}`}
              style={style}
              title={trust === "unverified" ? `Viability ${viab} — unverified` : `Viability ${viab}`}
            >
              {viab}
            </span>
          );
        })()}
        {live && <span className="gnode-pulse" aria-hidden />}
      </div>
      <div className="gnode-title">{node.title}</div>
      <Handle type="source" position={Position.Bottom} className="gnode-handle" />
    </div>
  );
}

const nodeTypes = { gnode: GraphNode };

/* ----------------------------------------------------------- dagre layout -- */
function layout(tnodes: TreeNode[]): Record<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 26, ranksep: 60, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of tnodes) g.setNode(n.id, { width: NODE_W, height: NODE_H });
  for (const n of tnodes) {
    if (n.parent_id && tnodes.some((m) => m.id === n.parent_id)) g.setEdge(n.parent_id, n.id);
  }
  dagre.layout(g);
  const pos: Record<string, { x: number; y: number }> = {};
  for (const n of tnodes) {
    const p = g.node(n.id);
    if (p) pos[n.id] = { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 };
  }
  return pos;
}

/* --------------------------------------------------------------- inner --- */
function Canvas({ nodes, rootId, selectedId, onSelect }: Props) {
  const tnodes = useMemo(() => Object.values(nodes), [nodes]);
  const { fitView } = useReactFlow();

  const { rfNodes, rfEdges } = useMemo(() => {
    const pos = layout(tnodes);
    const rfNodes: RFNode[] = tnodes.map((n) => ({
      id: n.id,
      type: "gnode",
      position: pos[n.id] ?? { x: 0, y: 0 },
      data: { node: n, selected: n.id === selectedId, onSelect } as unknown as Record<string, unknown>,
      draggable: false,
    }));
    const rfEdges: RFEdge[] = tnodes
      .filter((n) => n.parent_id && nodes[n.parent_id])
      .map((n) => ({
        id: `${n.parent_id}-${n.id}`,
        source: n.parent_id as string,
        target: n.id,
        type: "smoothstep",
        animated: isLive(n.state),
        style: { stroke: "var(--border-strong)", strokeWidth: 1.5 },
      }));
    return { rfNodes, rfEdges };
  }, [tnodes, nodes, selectedId, onSelect]);

  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState(rfNodes);
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState(rfEdges);

  // Re-sync when the tree grows/changes (new nodes stream in live).
  const sig = tnodes.length + ":" + selectedId;
  useEffect(() => { setFlowNodes(rfNodes); setFlowEdges(rfEdges); }, [sig]); // eslint-disable-line

  // Fit on first load, but cap the zoom so nodes stay readable on wide trees
  // (the minimap + pan cover the rest instead of shrinking everything to dots).
  useEffect(() => {
    const t = setTimeout(() => fitView({ padding: 0.18, duration: 300, maxZoom: 0.85, minZoom: 0.4 }), 140);
    return () => clearTimeout(t);
  }, [tnodes.length >= 1, fitView]); // eslint-disable-line

  const onNodeClick = useCallback((_: unknown, n: RFNode) => onSelect(n.id), [onSelect]);

  if (tnodes.length === 0) {
    return (
      <div className="graph-empty">
        <div className="ge-ico">◇</div>
        <div className="ge-t">Awaiting the first branches…</div>
        <div className="ge-d">The explorer is decomposing the domain.</div>
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={onNodeClick}
      nodeTypes={nodeTypes}
      fitView
      fitViewOptions={{ padding: 0.18, maxZoom: 0.85, minZoom: 0.4 }}
      minZoom={0.15}
      maxZoom={2.2}
      proOptions={{ hideAttribution: true }}
      nodesConnectable={false}
      nodesDraggable={false}
      className="mgf-flow"
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="var(--border)" />
      <Controls showInteractive={false} />
      <MiniMap
        pannable zoomable
        nodeColor={(n) => {
          const t = (n.data as unknown as NodeData)?.node;
          return t && isGapish(t.kind) && t.viability != null ? viabilityRamp(t.viability) : "var(--border-strong)";
        }}
        maskColor="rgba(0,0,0,0.06)"
        className="mgf-minimap"
      />
    </ReactFlow>
  );
}

/**
 * Infinite-canvas node-link view of the exploration tree (owner ask). Pan/zoom,
 * dagre top-down auto-layout, live-animated edges + pulsing nodes, viability-
 * colored gap nodes, a minimap, and fit-to-view. Clicking a node selects it
 * (opens the idea detail / inspector). Streams live as the tree grows.
 */
export default function GraphCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
