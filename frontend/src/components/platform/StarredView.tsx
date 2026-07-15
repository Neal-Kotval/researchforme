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

interface Props {
  onOpenNode: (pid: string, nodeId: string) => void;
  /** Jump to the new project once its ideas are on disk. */
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
 * Starred ideas (W-1) — the founder's own shortlist across every exploration.
 *
 * Reads `user_star`, never the engine's `star`: this list is taste, not score.
 * An idea the engine rated 88 and you ignored does not belong here; one you
 * starred over the engine's objection does.
 *
 * The multi-select is deliberate and load-bearing: it is the entry point for
 * the project workbench (Phase 5 W-3) — select N ideas, export them into a
 * project's `ideas/` folder. Until that lands, selection drives the export
 * button below, which is disabled with an honest reason rather than hidden.
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
  // "" = create a new project; a slug = add to that existing project.
  const [targetSlug, setTargetSlug] = useState("");
  const [libProjects, setLibProjects] = useState<LibraryProject[]>([]);

  const load = async () => {
    try {
      setItems(await getStarred());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load starred ideas.");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const byProject = useMemo(() => {
    const groups = new Map<string, { domain: string; rows: PortfolioItem[] }>();
    for (const it of items ?? []) {
      const key = it.project_id;
      if (!groups.has(key)) {
        groups.set(key, { domain: it.domain ?? "(deleted exploration)", rows: [] });
      }
      groups.get(key)!.rows.push(it);
    }
    return [...groups.entries()];
  }, [items]);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  /**
   * Export the selected ideas into a new project on disk (W-3).
   *
   * The markdown is serialized client-side by `gapToMarkdown` — the same
   * formatter the "Copy for chat" button uses, so an idea reads identically
   * whether it lands in your clipboard or in `ideas/`. That export carries the
   * red team's criticism, and the backend's optional development pass is
   * required to preserve it.
   */
  const openExport = () => {
    setExporting(true);
    listLibraryProjects().then(setLibProjects).catch(() => {});
  };

  const runExport = async () => {
    const chosen = (items ?? []).filter((i) => selected.has(i.node_id));
    // New project needs a name; adding to an existing one needs a target slug.
    if (chosen.length === 0) return;
    if (!targetSlug && !projectTitle.trim()) return;

    setBusy(true);
    setError(null);
    setProgress("Reading the ideas…");
    try {
      // Serialize each gap from its exploration's tree (the full node, with the
      // pressure test — the shortlist row alone is far too thin to export).
      const byProject = new Map<string, PortfolioItem[]>();
      for (const it of chosen) {
        if (!byProject.has(it.project_id)) byProject.set(it.project_id, []);
        byProject.get(it.project_id)!.push(it);
      }

      const payload: ImportIdeaInput[] = [];
      for (const [pid, rows] of byProject) {
        const snap = await getTree(pid);
        for (const row of rows) {
          const node = snap.nodes.find((n) => n.id === row.node_id);
          if (!node) continue;
          payload.push({
            project_id: pid,
            node_id: row.node_id,
            title: node.gap?.title ?? node.title,
            markdown: gapToMarkdown(node, snap.project),
          });
        }
      }

      if (payload.length === 0) {
        setError("Could not load those ideas from their explorations.");
        return;
      }

      setProgress(
        develop
          ? `Developing ${payload.length} idea${payload.length === 1 ? "" : "s"}… (one model pass each)`
          : `Writing ${payload.length} idea${payload.length === 1 ? "" : "s"}…`,
      );
      const result = targetSlug
        ? await importIdeasInto(targetSlug, payload, develop)
        : await importIdeasToNewProject(payload, projectTitle.trim(), develop);

      // Surface any idea whose development pass degraded — never swallow it.
      const degraded = result.imported.filter((i) => develop && !i.developed);
      if (degraded.length > 0) {
        setError(
          `Exported, but ${degraded.length} idea${degraded.length === 1 ? "" : "s"} could not be ` +
            `developed and landed as the raw export instead: ${degraded[0].note}`,
        );
      }
      setExporting(false);
      setSelected(new Set());
      setProjectTitle("");
      onImported?.(result.project.slug);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not export those ideas.");
    } finally {
      setBusy(false);
      setProgress("");
    }
  };

  const unstar = async (it: PortfolioItem) => {
    setBusy(true);
    try {
      await control(it.project_id, { action: "unstar_node", node_id: it.node_id });
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(it.node_id);
        return next;
      });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not unstar that idea.");
    } finally {
      setBusy(false);
    }
  };

  if (error) return <div className="gy-empty">{error}</div>;
  if (items === null) return <div className="gy-empty">Loading your starred ideas…</div>;

  if (items.length === 0) {
    return (
      <div className="gy-empty">
        <p>No starred ideas yet.</p>
        <p className="insp-note">
          Star an idea from its detail panel (<b>☆ Star idea</b>) and it lands here —
          your shortlist across every exploration, independent of how the engine scored it.
        </p>
      </div>
    );
  }

  return (
    <div className="pf-view w940 starred-view">
      <div className="starred-bar">
        <span className="sb-count">
          {selected.size > 0
            ? `${selected.size} of ${items.length} selected`
            : `${items.length} starred idea${items.length === 1 ? "" : "s"}`}
        </span>
        <div className="sb-actions">
          {selected.size > 0 && (
            <button className="btn btn-ghost" onClick={() => setSelected(new Set())}>
              Clear selection
            </button>
          )}
          <button
            className="btn btn-primary"
            disabled={selected.size === 0 || busy}
            title={
              selected.size === 0
                ? "Select ideas to export into a project"
                : "Write these ideas as markdown into a project's ideas/ folder"
            }
            onClick={openExport}
          >
            Export {selected.size > 0 ? `${selected.size} ` : ""}to project →
          </button>
        </div>
      </div>

      {exporting && (
        <div className="export-panel">
          <div className="ep-row">
            <label className="ep-label" htmlFor="ep-target">Destination</label>
            <select
              id="ep-target"
              className="lib-input"
              value={targetSlug}
              onChange={(e) => setTargetSlug(e.target.value)}
            >
              <option value="">+ New project…</option>
              {libProjects.map((p) => (
                <option key={p.slug} value={p.slug}>{p.title}</option>
              ))}
            </select>
          </div>
          {!targetSlug && (
            <div className="ep-row">
              <label className="ep-label" htmlFor="ep-title">Name</label>
              <input
                id="ep-title"
                autoFocus
                className="lib-input"
                placeholder="e.g. Kernel CI"
                value={projectTitle}
                onChange={(e) => setProjectTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && projectTitle.trim()) void runExport();
                  if (e.key === "Escape") setExporting(false);
                }}
              />
            </div>
          )}
          <label className="ep-check">
            <input
              type="checkbox"
              checked={develop}
              onChange={(e) => setDevelop(e.target.checked)}
            />
            <span>
              <b>Develop each idea first</b> — one model pass that sharpens the thesis,
              turns the wedge into a first move, and converts the riskiest assumption
              into a falsification plan. Costs tokens. The red team's criticism is
              carried through, never dropped.
            </span>
          </label>
          <div className="ep-actions">
            {progress && <span className="ep-progress">{progress}</span>}
            <button
              className="btn btn-primary"
              disabled={busy || (!targetSlug && !projectTitle.trim())}
              onClick={() => void runExport()}
            >
              {busy
                ? "Exporting…"
                : `Export ${selected.size} idea${selected.size === 1 ? "" : "s"}` +
                  (targetSlug ? " →" : "")}
            </button>
            <button className="btn btn-ghost" disabled={busy} onClick={() => setExporting(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {byProject.map(([pid, group]) => (
        <div className="starred-group" key={pid}>
          <div className="sg-head">{group.domain}</div>
          {group.rows.map((it) => (
            <div className={`starred-row${selected.has(it.node_id) ? " sel" : ""}`} key={it.node_id}>
              <input
                type="checkbox"
                className="sr-check"
                checked={selected.has(it.node_id)}
                onChange={() => toggle(it.node_id)}
                aria-label={`Select ${it.title}`}
              />
              <button className="sr-main" onClick={() => onOpenNode(it.project_id, it.node_id)}>
                <span className="sr-title">{it.title}</span>
                <span className="sr-meta">
                  {it.viability != null && <span className="sr-viab">v{it.viability}</span>}
                  {it.fit != null && <span className="sr-fit">fit {it.fit}</span>}
                  {it.star && (
                    <span className="sr-enginepick" title="The engine also rated this above your star threshold.">
                      engine pick
                    </span>
                  )}
                  {it.updated_at && <span className="sr-when">{fmtWhen(it.updated_at)}</span>}
                </span>
              </button>
              <button
                className="btn btn-ghost sr-unstar"
                disabled={busy}
                title="Remove from your starred ideas"
                onClick={() => void unstar(it)}
              >
                ★
              </button>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
