import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  control,
  getStarred,
  getTree,
  importIdeasInto,
  importIdeasToNewProject,
  listLibraryProjects,
  type ImportIdeaInput,
  type LibraryProject,
} from "../../autonomous/api";
import { gapToMarkdown } from "../../autonomous/exportGap";
import type { PortfolioItem } from "../../autonomous/types";
import { Button, Card } from "../ui";

interface Props {
  onOpenNode: (pid: string, nodeId: string) => void;
  onImported?: (slug: string) => void;
}

function fmtWhen(iso: string | null): string {
  if (!iso) return "";
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${Math.max(m, 0)}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/**
 * Starred ideas (v3): the founder's own shortlist (user_star, not the engine's
 * score) as flat rows with checkboxes, grouped by exploration, with a floating
 * "Export to project" bar and the export panel.
 */
export default function StarredView({ onOpenNode, onImported }: Props) {
  const [items, setItems] = useState<PortfolioItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [projectTitle, setProjectTitle] = useState("");
  const [develop, setDevelop] = useState(true);
  const [progress, setProgress] = useState("");
  const [targetSlug, setTargetSlug] = useState("");
  const [libProjects, setLibProjects] = useState<LibraryProject[]>([]);

  const load = async () => {
    try { setItems(await getStarred()); setError(null); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Could not load starred ideas."); }
  };
  useEffect(() => { void load(); }, []);

  const byProject = useMemo(() => {
    const groups = new Map<string, { domain: string; rows: PortfolioItem[] }>();
    for (const it of items ?? []) {
      if (!groups.has(it.project_id)) groups.set(it.project_id, { domain: it.domain ?? "(deleted exploration)", rows: [] });
      groups.get(it.project_id)!.rows.push(it);
    }
    return [...groups.entries()];
  }, [items]);

  const toggle = (id: string) => setSelected((prev) => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const openExport = () => { setExporting(true); listLibraryProjects().then(setLibProjects).catch(() => {}); };

  const runExport = async () => {
    const chosen = (items ?? []).filter((i) => selected.has(i.node_id));
    if (chosen.length === 0) return;
    if (!targetSlug && !projectTitle.trim()) return;
    setBusy(true); setError(null); setProgress("Reading the ideas…");
    try {
      const grouped = new Map<string, PortfolioItem[]>();
      for (const it of chosen) {
        if (!grouped.has(it.project_id)) grouped.set(it.project_id, []);
        grouped.get(it.project_id)!.push(it);
      }
      const payload: ImportIdeaInput[] = [];
      for (const [pid, rows] of grouped) {
        const snap = await getTree(pid);
        for (const row of rows) {
          const node = snap.nodes.find((n) => n.id === row.node_id);
          if (!node) continue;
          payload.push({ project_id: pid, node_id: row.node_id, title: node.gap?.title ?? node.title, markdown: gapToMarkdown(node, snap.project) });
        }
      }
      if (payload.length === 0) { setError("Could not load those ideas from their explorations."); return; }
      setProgress(develop ? `Developing ${payload.length} idea${payload.length === 1 ? "" : "s"}… (one model pass each)` : `Writing ${payload.length} idea${payload.length === 1 ? "" : "s"}…`);
      const result = targetSlug
        ? await importIdeasInto(targetSlug, payload, develop)
        : await importIdeasToNewProject(payload, projectTitle.trim(), develop);
      const degraded = result.imported.filter((i) => develop && !i.developed);
      if (degraded.length > 0) {
        setError(`Exported, but ${degraded.length} idea${degraded.length === 1 ? "" : "s"} could not be developed and landed as the raw export instead: ${degraded[0].note}`);
      }
      setExporting(false); setSelected(new Set()); setProjectTitle("");
      onImported?.(result.project.slug);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not export those ideas.");
    } finally { setBusy(false); setProgress(""); }
  };

  const unstar = async (it: PortfolioItem) => {
    setBusy(true);
    try {
      await control(it.project_id, { action: "unstar_node", node_id: it.node_id });
      setSelected((prev) => { const next = new Set(prev); next.delete(it.node_id); return next; });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not unstar that idea.");
    } finally { setBusy(false); }
  };

  if (error && items === null) return <div className="pf-view ui-page"><Card pad><div className="ui-empty-body" role="alert" style={{ textAlign: "center", maxWidth: "none" }}>{error}</div></Card></div>;
  if (items === null) return <div className="pf-view ui-page"><Card pad><div className="ui-empty-body" style={{ textAlign: "center", maxWidth: "none" }}>Loading your starred ideas…</div></Card></div>;

  if (items.length === 0) {
    return (
      <div className="pf-view ui-page">
        <Card pad>
          <div className="ui-empty">
            <div className="ui-empty-title">No starred ideas yet</div>
            <div className="ui-empty-body">Star an idea from its detail panel (☆ Star idea) and it lands here — your shortlist across every exploration, independent of how the engine scored it.</div>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="pf-view ui-page">
      <section>
        {error && <div className="ui-inline-err" role="alert" style={{ marginBottom: 12 }}>{error}</div>}

        {exporting && (
          <Card pad style={{ marginBottom: 16 }}>
            <div className="sv-export">
              <div className="sv-export-row">
                <label htmlFor="ep-target">Destination</label>
                <select id="ep-target" className="lib-input2" value={targetSlug} onChange={(e) => setTargetSlug(e.target.value)}>
                  <option value="">+ New project…</option>
                  {libProjects.map((p) => <option key={p.slug} value={p.slug}>{p.title}</option>)}
                </select>
              </div>
              {!targetSlug && (
                <div className="sv-export-row">
                  <label htmlFor="ep-title">Name</label>
                  <input id="ep-title" autoFocus className="lib-input2" placeholder="e.g. Kernel CI" value={projectTitle}
                    onChange={(e) => setProjectTitle(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && projectTitle.trim()) void runExport(); if (e.key === "Escape") setExporting(false); }} />
                </div>
              )}
              <label className="sv-check-row">
                <input type="checkbox" className="sv-check" checked={develop} onChange={(e) => setDevelop(e.target.checked)} />
                <span><b>Develop each idea first</b> — one model pass that sharpens the thesis, turns the wedge into a first move, and converts the riskiest assumption into a falsification plan. Costs tokens. The red team's criticism is carried through, never dropped.</span>
              </label>
              <div className="sv-export-actions">
                {progress && <span className="sv-progress">{progress}</span>}
                <Button variant="primary" disabled={busy || (!targetSlug && !projectTitle.trim())} onClick={() => void runExport()}>
                  {busy ? "Exporting…" : `Export ${selected.size} idea${selected.size === 1 ? "" : "s"}${targetSlug ? " →" : ""}`}
                </Button>
                <Button variant="quiet" disabled={busy} onClick={() => setExporting(false)}>Cancel</Button>
              </div>
            </div>
          </Card>
        )}

        {byProject.map(([pid, group]) => (
          <div className="sv-group" key={pid}>
            <div className="sv-group-head">{group.domain}</div>
            <div className="sv-rowlist">
              {group.rows.map((it) => (
                <div className={`sv-row${selected.has(it.node_id) ? " sel" : ""}`} key={it.node_id}>
                  <input type="checkbox" className="sv-check" checked={selected.has(it.node_id)} onChange={() => toggle(it.node_id)} aria-label={`Select ${it.title}`} />
                  <button className="sv-main" onClick={() => onOpenNode(it.project_id, it.node_id)}>
                    <span className="sv-title">{it.title}</span>
                    <span className="sv-meta">
                      {it.viability != null && <span className="ui-chip ui-chip--slate ui-chip--sm">v{it.viability}</span>}
                      {it.fit != null && <span className="ui-chip ui-chip--slate ui-chip--sm">fit {it.fit}</span>}
                      {it.star && <span className="ui-chip ui-chip--sm" title="The engine also rated this above your star threshold.">engine pick</span>}
                      {it.updated_at && <span style={{ fontSize: "var(--fs-label)", color: "var(--text-tertiary)" }}>{fmtWhen(it.updated_at)}</span>}
                    </span>
                  </button>
                  <Button variant="quiet" size="sm" className="sv-unstar" disabled={busy} title="Remove from your starred ideas" onClick={() => void unstar(it)}>★</Button>
                </div>
              ))}
            </div>
          </div>
        ))}

        {selected.size > 0 && (
          <div className="sv-bar">
            <span className="sv-bar-count">{selected.size} of {items.length} selected</span>
            <div className="sv-bar-actions">
              <Button variant="quiet" size="sm" onClick={() => setSelected(new Set())}>Clear</Button>
              <Button variant="primary" size="sm" disabled={busy} onClick={openExport}>Export to project →</Button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
