import { useCallback, useEffect, useRef } from "react";
import type { TreeNode } from "../../autonomous/types";

/**
 * EvolutionGraph — a live, force-directed bubble view of a tree's DYNAMICS.
 *
 * The static canvas/list shows structure; this shows the engine *thinking*:
 *  - every node is a bubble, drawn into a spring/repulsion/collision simulation;
 *  - structural growth pushes new bubbles out from their parent;
 *  - a CROSSOVER offspring is born at the midpoint of its two parents with both
 *    lineage edges converging into it — the "bubbles merging" moment;
 *  - a MUTATION hangs off its one parent by a dashed twist edge;
 *  - pruned / killed / occupied-and-unstarred bubbles fade to grey (the "done,
 *    marked grey, linear" retirement the founder asked for);
 *  - ⭐ winners wear an accent ring; live nodes pulse.
 *
 * Pure canvas + requestAnimationFrame (no graph lib): full control over the
 * physics and the merge/retire animations. Theme-aware (reads CSS tokens, re-reads
 * on theme flip) and honours prefers-reduced-motion (keeps the layout, drops the
 * decorative flashes).
 */

interface Props {
  nodes: Record<string, TreeNode>;
  rootId: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

interface Particle {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;        // current radius (eased)
  tr: number;       // target radius
  a: number;        // current alpha (eased)
  ta: number;       // target alpha
  born: number;     // ms timestamp of first sighting (for merge flash)
  node: TreeNode;
}

interface Tokens {
  neutral: string;
  grey: string;
  accent: string;
  text: string;
  textDim: string;
  border: string;
  surface: string;
  ramp: string[];   // ramp-0..4
}

interface Sim {
  parts: Map<string, Particle>;
  w: number;
  h: number;
  cam: { x: number; y: number; k: number };   // pan x/y, zoom k
  userCam: boolean;                            // user grabbed the camera → stop auto-fit
  drag: { lastX: number; lastY: number; moved: number } | null; // active pan
  tokens: Tokens | null;
  hover: string | null;
  mouse: { x: number; y: number } | null;      // canvas-space
  reduced: boolean;
}

function readTokens(el: HTMLElement): Tokens {
  const s = getComputedStyle(el);
  const g = (name: string, fb: string) => (s.getPropertyValue(name).trim() || fb);
  return {
    neutral: g("--data-neutral", "#9aa0a6"),
    grey: g("--border-strong", "#c4c4c4"),
    accent: g("--accent", "#e8552a"),
    text: g("--text", "#1a1a1a"),
    textDim: g("--text-dim", "#5f6368"),
    border: g("--border", "#e3e3e0"),
    surface: g("--surface", "#ffffff"),
    ramp: [
      g("--ramp-0", "#b9412a"),
      g("--ramp-1", "#c9773a"),
      g("--ramp-2", "#c9a83a"),
      g("--ramp-3", "#7fa84a"),
      g("--ramp-4", "#3f8f5a"),
    ],
  };
}

const isStructural = (k: string) => k === "domain" || k === "subarea" || k === "segment";
const isLive = (s: string) =>
  s === "expanding" || s === "synthesizing" || s === "pressure_testing";
const isRetired = (n: TreeNode) =>
  n.state === "pruned" || n.state === "errored" ||
  (n.novelty_scan?.verdict === "occupied");
const isCrossover = (n: TreeNode) =>
  !!n.cross_parent_id || !!n.gap?.tags?.includes("crossover");
const isMutation = (n: TreeNode) => !!n.gap?.tags?.includes("mutation");

function radiusFor(n: TreeNode): number {
  switch (n.kind) {
    case "domain": return 24;
    case "subarea": return 15;
    case "segment": return 12;
    case "gap_candidate": return 10;
    case "gap": return 11 + (n.viability != null ? (n.viability / 100) * 17 : 4);
    default: return 10;
  }
}

function colorFor(n: TreeNode, t: Tokens): string {
  if (isRetired(n)) return t.grey;
  if (n.kind === "gap" && n.viability != null) {
    const i = Math.min(4, Math.max(0, Math.floor((n.viability / 100) * 5)));
    return t.ramp[i];
  }
  if (n.kind === "gap_candidate") return t.neutral;
  return t.neutral;
}

export default function EvolutionGraph({ nodes, rootId, selectedId, onSelect }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const simRef = useRef<Sim>({
    parts: new Map(), w: 0, h: 0, cam: { x: 0, y: 0, k: 1 },
    userCam: false, drag: null,
    tokens: null, hover: null, mouse: null,
    reduced: typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true,
  });
  const propsRef = useRef({ nodes, selectedId, onSelect, rootId });
  propsRef.current = { nodes, selectedId, onSelect, rootId };

  // ---- reconcile particles from the live node map ------------------------- //
  useEffect(() => {
    const sim = simRef.current;
    const now = performance.now();
    const cx = sim.w / 2 || 300;
    const cy = sim.h / 2 || 220;
    const seen = new Set<string>();

    // Insert in creation order so a parent exists before its child spawns on it.
    const ordered = Object.values(nodes).sort(
      (a, b) => (a.created_at < b.created_at ? -1 : 1),
    );
    for (const n of ordered) {
      seen.add(n.id);
      const existing = sim.parts.get(n.id);
      if (existing) {
        existing.node = n;
        existing.tr = radiusFor(n);
        existing.ta = isRetired(n) ? 0.34 : 1;
        continue;
      }
      // Spawn position: a crossover offspring is born BETWEEN its two parents
      // (the merge); anything else near its single parent; roots near center.
      const p1 = n.parent_id ? sim.parts.get(n.parent_id) : undefined;
      const p2 = n.cross_parent_id ? sim.parts.get(n.cross_parent_id) : undefined;
      let sx = cx, sy = cy;
      if (p1 && p2) { sx = (p1.x + p2.x) / 2; sy = (p1.y + p2.y) / 2; }
      else if (p1) { sx = p1.x + (Math.random() - 0.5) * 24; sy = p1.y + (Math.random() - 0.5) * 24; }
      else { sx = cx + (Math.random() - 0.5) * 40; sy = cy + (Math.random() - 0.5) * 40; }
      sim.parts.set(n.id, {
        id: n.id, x: sx, y: sy, vx: 0, vy: 0,
        r: 0.1, tr: radiusFor(n), a: 0, ta: isRetired(n) ? 0.34 : 1,
        born: now, node: n,
      });
    }
    // Nodes that vanished from the map (rare) retire out.
    for (const [id, p] of sim.parts) {
      if (!seen.has(id)) { p.ta = 0; p.tr = 0; }
    }
  }, [nodes]);

  // ---- sizing + tokens ---------------------------------------------------- //
  useEffect(() => {
    const wrap = wrapRef.current, canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const sim = simRef.current;
    const resize = () => {
      const rect = wrap.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      sim.w = rect.width; sim.h = rect.height;
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      const ctx = canvas.getContext("2d");
      if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    sim.tokens = readTokens(document.documentElement);
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    // Re-read tokens on theme flip.
    const mo = new MutationObserver(() => { sim.tokens = readTokens(document.documentElement); });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
    return () => { ro.disconnect(); mo.disconnect(); };
  }, []);

  // ---- wheel zoom (about the cursor), non-passive so the page won't scroll - //
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const sim = simRef.current;
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const wx = (mx - sim.cam.x) / sim.cam.k;
      const wy = (my - sim.cam.y) / sim.cam.k;
      const k = Math.max(0.2, Math.min(4, sim.cam.k * Math.exp(-e.deltaY * 0.0015)));
      sim.cam.k = k;
      sim.cam.x = mx - wx * k;   // keep the point under the cursor fixed
      sim.cam.y = my - wy * k;
      sim.userCam = true;
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, []);

  // ---- interaction: hover + click hit-test (camera-aware) ----------------- //
  const toWorld = (mx: number, my: number) => {
    const { cam } = simRef.current;
    return { x: (mx - cam.x) / cam.k, y: (my - cam.y) / cam.k };
  };
  const hitTest = (mx: number, my: number): string | null => {
    const sim = simRef.current;
    const w = toWorld(mx, my);
    let best: string | null = null, bestD = Infinity;
    for (const p of sim.parts.values()) {
      const d = Math.hypot(p.x - w.x, p.y - w.y);
      if (d <= p.r + 4 && d < bestD) { bestD = d; best = p.id; }
    }
    return best;
  };
  const onPointerDown = useCallback((e: React.PointerEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    simRef.current.drag = { lastX: e.clientX - rect.left, lastY: e.clientY - rect.top, moved: 0 };
    canvasRef.current!.setPointerCapture?.(e.pointerId);
  }, []);
  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const sim = simRef.current;
    const rect = canvasRef.current!.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    sim.mouse = { x: mx, y: my };
    if (sim.drag) {
      const dx = mx - sim.drag.lastX, dy = my - sim.drag.lastY;
      sim.drag.moved += Math.abs(dx) + Math.abs(dy);
      sim.cam.x += dx; sim.cam.y += dy;   // pan (screen-space)
      sim.drag.lastX = mx; sim.drag.lastY = my;
      sim.userCam = true;                  // took control → stop auto-fit
      canvasRef.current!.style.cursor = "grabbing";
    } else {
      sim.hover = hitTest(mx, my);
      canvasRef.current!.style.cursor = sim.hover ? "pointer" : "grab";
    }
  }, []);
  const onPointerUp = useCallback((e: React.PointerEvent) => {
    const sim = simRef.current;
    const rect = canvasRef.current!.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const wasDrag = sim.drag && sim.drag.moved > 5;   // distinguish pan from click
    sim.drag = null;
    canvasRef.current!.releasePointerCapture?.(e.pointerId);
    if (!wasDrag) {
      const hit = hitTest(mx, my);
      if (hit) propsRef.current.onSelect(hit);
    }
    canvasRef.current!.style.cursor = "grab";
  }, []);
  const onLeave = useCallback(() => {
    const sim = simRef.current;
    sim.hover = null; sim.mouse = null; sim.drag = null;
  }, []);
  const recenter = useCallback(() => { simRef.current.userCam = false; }, []);

  // ---- the simulation + render loop --------------------------------------- //
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let raf = 0;
    let last = performance.now();

    const step = (now: number) => {
      const sim = simRef.current;
      const dt = Math.min(2, (now - last) / 16.67); // ~frames, clamped
      last = now;
      const parts = [...sim.parts.values()];
      const t = sim.tokens ?? readTokens(document.documentElement);
      const cx = sim.w / 2, cy = sim.h / 2;

      // --- forces ---
      for (const p of parts) {
        // gentle pull to center
        p.vx += (cx - p.x) * 0.0016 * dt;
        p.vy += (cy - p.y) * 0.0016 * dt;
      }
      // repulsion (O(n^2); fine for a few hundred bubbles)
      for (let i = 0; i < parts.length; i++) {
        const a = parts[i];
        for (let j = i + 1; j < parts.length; j++) {
          const b = parts[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
          const minD = a.r + b.r + 10;
          const d = Math.sqrt(d2);
          // charge repulsion
          const rep = Math.min(6, (900 / d2)) * dt;
          const ux = dx / d, uy = dy / d;
          a.vx += ux * rep; a.vy += uy * rep;
          b.vx -= ux * rep; b.vy -= uy * rep;
          // hard collision (bubble packing)
          if (d < minD) {
            const push = (minD - d) * 0.04 * dt;
            a.vx += ux * push; a.vy += uy * push;
            b.vx -= ux * push; b.vy -= uy * push;
          }
        }
      }
      // link springs (structural parent + crossover second parent)
      for (const p of parts) {
        const n = p.node;
        const link = (pid: string | null | undefined, rest: number, k: number) => {
          if (!pid) return;
          const q = sim.parts.get(pid);
          if (!q) return;
          const dx = q.x - p.x, dy = q.y - p.y;
          const d = Math.hypot(dx, dy) || 1;
          const f = (d - rest) * k * dt;
          const ux = dx / d, uy = dy / d;
          p.vx += ux * f; p.vy += uy * f;
          q.vx -= ux * f; q.vy -= uy * f;
        };
        const rest = isStructural(n.kind) ? 70 : 52;
        link(n.parent_id, rest, 0.02);
        link(n.cross_parent_id, rest, 0.02);
      }
      // integrate
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const p of parts) {
        p.vx *= 0.86; p.vy *= 0.86;
        p.x += p.vx * dt; p.y += p.vy * dt;
        // ease radius + alpha (clamped: a render loop must never carry a
        // negative radius into a canvas arc, whatever the frame timing)
        p.r = Math.max(0, p.r + (p.tr - p.r) * 0.12 * dt);
        p.a = Math.max(0, Math.min(1, p.a + (p.ta - p.a) * 0.08 * dt));
        const pad = p.r + 8;
        minX = Math.min(minX, p.x - pad); maxX = Math.max(maxX, p.x + pad);
        minY = Math.min(minY, p.y - pad); maxY = Math.max(maxY, p.y + pad);
      }
      // reap fully-faded, vanished nodes
      for (const p of parts) {
        if (p.ta === 0 && p.a < 0.02) sim.parts.delete(p.id);
      }

      // --- auto-fit camera (smoothed) — yields the moment the user pans/zooms ---
      if (!sim.userCam && parts.length && isFinite(minX)) {
        const bw = Math.max(1, maxX - minX), bh = Math.max(1, maxY - minY);
        const k = Math.min(1.4, Math.min(sim.w / bw, sim.h / bh));
        const tx = sim.w / 2 - ((minX + maxX) / 2) * k;
        const ty = sim.h / 2 - ((minY + maxY) / 2) * k;
        sim.cam.k += (k - sim.cam.k) * 0.05;
        sim.cam.x += (tx - sim.cam.x) * 0.05;
        sim.cam.y += (ty - sim.cam.y) * 0.05;
      }

      // --- draw ---
      ctx.clearRect(0, 0, sim.w, sim.h);
      ctx.save();
      ctx.translate(sim.cam.x, sim.cam.y);
      ctx.scale(sim.cam.k, sim.cam.k);

      // edges first
      ctx.lineWidth = 1 / sim.cam.k;
      for (const p of parts) {
        const n = p.node;
        const drawEdge = (pid: string | null | undefined, kind: "tree" | "cross" | "mut") => {
          if (!pid) return;
          const q = sim.parts.get(pid);
          if (!q) return;
          const alpha = Math.min(p.a, q.a);
          if (kind === "tree") {
            ctx.strokeStyle = hexA(t.border, 0.9 * alpha);
            ctx.setLineDash([]);
          } else {
            ctx.strokeStyle = hexA(t.accent, 0.55 * alpha);
            ctx.setLineDash([4 / sim.cam.k, 3 / sim.cam.k]);
          }
          ctx.beginPath();
          ctx.moveTo(q.x, q.y);
          ctx.lineTo(p.x, p.y);
          ctx.stroke();
        };
        if (isCrossover(n)) {
          drawEdge(n.parent_id, "cross");
          drawEdge(n.cross_parent_id, "cross");
        } else if (isMutation(n)) {
          drawEdge(n.parent_id, "mut");
        } else {
          drawEdge(n.parent_id, "tree");
        }
      }
      ctx.setLineDash([]);

      // bubbles
      const selId = propsRef.current.selectedId;
      const hoverId = sim.hover;
      const circle = (x: number, y: number, r: number) => {
        ctx.beginPath();
        ctx.arc(x, y, Math.max(0.1, r), 0, Math.PI * 2);
      };
      for (const p of parts) {
        const n = p.node;
        const col = colorFor(n, t);
        const rr = Math.max(0.1, p.r);
        // merge / birth flash for recombination offspring
        const age = now - p.born;
        if (!sim.reduced && (isCrossover(n) || isMutation(n)) && age < 1100) {
          const pr = (1 - age / 1100);
          circle(p.x, p.y, rr + 10 * pr + 3);
          ctx.fillStyle = hexA(t.accent, 0.18 * pr);
          ctx.fill();
        }
        // live pulse ring
        if (!sim.reduced && isLive(n.state)) {
          const pulse = (Math.sin(now / 240) + 1) / 2;
          circle(p.x, p.y, rr + 3 + pulse * 4);
          ctx.strokeStyle = hexA(t.accent, 0.35 * p.a);
          ctx.lineWidth = 1.5 / sim.cam.k;
          ctx.stroke();
        }
        // body
        circle(p.x, p.y, rr);
        ctx.fillStyle = hexA(col, (isRetired(n) ? 0.5 : 0.92) * p.a);
        ctx.fill();
        // star ring (engine winner) — accent halo
        if (n.star) {
          circle(p.x, p.y, rr + 2.5);
          ctx.strokeStyle = hexA(t.accent, 0.95 * p.a);
          ctx.lineWidth = 2 / sim.cam.k;
          ctx.stroke();
        }
        // selection / hover ring
        if (n.id === selId || n.id === hoverId) {
          circle(p.x, p.y, rr + 4.5);
          ctx.strokeStyle = hexA(t.text, n.id === selId ? 0.9 : 0.4);
          ctx.lineWidth = 1.5 / sim.cam.k;
          ctx.stroke();
        }
      }
      ctx.restore();

      // hover label (screen space, above camera transform)
      if (hoverId) {
        const p = sim.parts.get(hoverId);
        if (p && sim.mouse) {
          const label = p.node.gap?.title || p.node.title || p.node.kind;
          const sub = p.node.kind === "gap" && p.node.viability != null
            ? `viability ${p.node.viability}${p.node.novelty_scan?.verdict ? ` · ${p.node.novelty_scan.verdict}` : ""}`
            : p.node.kind;
          drawLabel(ctx, sim.mouse.x, sim.mouse.y, label, sub, t);
        }
      }

      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div ref={wrapRef} className="evograph">
      <canvas
        ref={canvasRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onLeave}
      />
      <button className="evograph-fit" onClick={recenter} title="Fit to view">
        ⤢ Fit
      </button>
      <div className="evograph-legend" aria-hidden="true">
        <span><i className="ev-dot ev-live" /> live</span>
        <span><i className="ev-dot ev-gap" /> scored gap (size = viability)</span>
        <span><i className="ev-dot ev-star" /> ⭐ winner</span>
        <span><i className="ev-dot ev-grey" /> retired / occupied</span>
        <span><i className="ev-edge" /> crossover / mutation lineage</span>
      </div>
      <div className="evograph-caption" aria-hidden="true">
        Live evolution — bubbles merge on crossover, twist on mutation, grey out when retired.
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- helpers -- */
// Apply an alpha to a CSS color string (hex, rgb, or a resolved token value).
function hexA(color: string, alpha: number): string {
  const a = Math.max(0, Math.min(1, alpha));
  const c = color.trim();
  if (c.startsWith("#")) {
    let h = c.slice(1);
    if (h.length === 3) h = h.split("").map((x) => x + x).join("");
    const n = parseInt(h.slice(0, 6), 16);
    const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    return `rgba(${r},${g},${b},${a})`;
  }
  if (c.startsWith("rgb")) {
    const nums = c.replace(/rgba?\(|\)/g, "").split(",").map((s) => s.trim());
    return `rgba(${nums[0]},${nums[1]},${nums[2]},${a})`;
  }
  return c; // named/other — used opaque
}

function drawLabel(
  ctx: CanvasRenderingContext2D, x: number, y: number,
  title: string, sub: string, t: Tokens,
) {
  ctx.font = "600 12px ui-sans-serif, system-ui, sans-serif";
  const tw = ctx.measureText(title).width;
  ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
  const sw = ctx.measureText(sub).width;
  const w = Math.min(280, Math.max(tw, sw) + 20);
  const h = 40;
  let bx = x + 14, by = y - h - 6;
  if (bx + w > ctx.canvas.clientWidth) bx = x - w - 14;
  if (by < 0) by = y + 14;
  ctx.fillStyle = hexA(t.surface, 0.98);
  ctx.strokeStyle = hexA(t.border, 1);
  ctx.lineWidth = 1;
  roundRect(ctx, bx, by, w, h, 7);
  ctx.fill(); ctx.stroke();
  ctx.fillStyle = t.text;
  ctx.font = "600 12px ui-sans-serif, system-ui, sans-serif";
  ctx.fillText(ellipsize(ctx, title, w - 16), bx + 8, by + 17);
  ctx.fillStyle = t.textDim;
  ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
  ctx.fillText(ellipsize(ctx, sub, w - 16), bx + 8, by + 32);
}

function ellipsize(ctx: CanvasRenderingContext2D, s: string, max: number): string {
  if (ctx.measureText(s).width <= max) return s;
  let lo = 0, hi = s.length;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (ctx.measureText(s.slice(0, mid) + "…").width <= max) lo = mid; else hi = mid - 1;
  }
  return s.slice(0, lo) + "…";
}

function roundRect(
  ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number,
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
