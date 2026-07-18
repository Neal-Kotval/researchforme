import { useEffect, useState } from "react";
import {
  ApiError,
  createLibraryProject,
  deleteLibraryProject,
  getLibraryRoot,
  listLibraryProjects,
  type LibraryProject,
} from "../../autonomous/api";
import ProjectWorkspace from "./ProjectWorkspace";
import { Button, Card, ScoreBadge } from "../ui";

interface Props {
  slug: string | null;
  onOpenProject: (slug: string | null) => void;
}

function fmtWhen(iso: string): string {
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${Math.max(m, 0)}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/**
 * Library (v3): projects are real folders of markdown on disk. Path + New
 * project on one row, then flat project rows ordered "which one is fundable?".
 */
export default function LibraryView({ slug, onOpenProject }: Props) {
  const [projects, setProjects] = useState<LibraryProject[] | null>(null);
  const [root, setRoot] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = async () => {
    try {
      const [ps, r] = await Promise.all([listLibraryProjects(), getLibraryRoot()]);
      setProjects(ps);
      setRoot(r.root);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not read the library.");
    }
  };

  useEffect(() => { if (slug === null) void load(); }, [slug]);

  if (slug) return <ProjectWorkspace slug={slug} onBack={() => onOpenProject(null)} />;

  const create = async () => {
    if (!title.trim()) return;
    setBusy(true);
    try {
      const p = await createLibraryProject(title.trim());
      setTitle("");
      setCreating(false);
      onOpenProject(p.slug);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not create the project.");
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async (s: string) => {
    setDeleting(true);
    try {
      await deleteLibraryProject(s);
      setPendingDelete(null);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not delete the project.");
    } finally {
      setDeleting(false);
    }
  };

  const ordered = projects ? [...projects].sort((a, b) => {
    const av = a.viability, bv = b.viability;
    if (av != null && bv != null) return bv - av;
    if (av != null) return -1;
    if (bv != null) return 1;
    return Date.parse(b.updated_at) - Date.parse(a.updated_at);
  }) : [];

  return (
    <div className="pf-view ui-page">
      <section>
        <div className="lib-bar2">
          <span className="lib-root2" title="Your projects are plain markdown files here — open them in any editor, or put them in git.">
            {root || "…"}
          </span>
          {creating ? (
            <div className="lib-new2">
              <input autoFocus className="lib-input2" placeholder="Project name…" value={title}
                onChange={(e) => setTitle(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") void create(); if (e.key === "Escape") setCreating(false); }} />
              <Button variant="primary" disabled={busy || !title.trim()} onClick={() => void create()}>Create</Button>
              <Button variant="quiet" onClick={() => setCreating(false)}>Cancel</Button>
            </div>
          ) : (
            <Button variant="primary" iconLeft="＋" onClick={() => setCreating(true)}>New project</Button>
          )}
        </div>

        {error ? (
          <Card pad><div className="ui-empty-body" role="alert" style={{ textAlign: "center", maxWidth: "none" }}>{error}</div></Card>
        ) : projects === null ? (
          <Card pad><div className="ui-empty-body" style={{ textAlign: "center", maxWidth: "none" }}>Reading the library…</div></Card>
        ) : projects.length === 0 ? (
          <Card pad>
            <div className="ui-empty">
              <div className="ui-empty-title">The library is empty</div>
              <div className="ui-empty-body">Star ideas in an exploration, then export them from Starred — each project becomes a folder of markdown you own.</div>
            </div>
          </Card>
        ) : (
          <div className="lib-list">
            {ordered.map((p) => (
              <div className="lib-row" key={p.slug}>
                <button className="lib-open" onClick={() => onOpenProject(p.slug)} title={p.verdict || undefined}>
                  <div className="lib-open-main">
                    <div className="lib-title-row">
                      <span className="lib-title2">{p.title}</span>
                      <span className="ui-chip ui-chip--slate ui-chip--sm">{p.status}</span>
                    </div>
                    {p.verdict && <div className="lib-verdict2">{p.verdict}</div>}
                    <div className="lib-meta2">{p.idea_count} idea{p.idea_count === 1 ? "" : "s"} · {fmtWhen(p.updated_at)}</div>
                  </div>
                  {p.viability != null && (
                    <span title={p.validation_stale
                      ? `Score ${p.viability} is stale — the plan was strengthened since. Re-validate to rescore.`
                      : "Project critique score — how well the assembled bet survived the red team"}>
                      <ScoreBadge value={p.viability} verified={!p.validation_stale} />
                    </span>
                  )}
                </button>
                <button className="lib-del2" title="Delete this project" aria-label={`Delete ${p.title}`}
                  onClick={(e) => { e.stopPropagation(); setPendingDelete(p.slug); }}>×</button>
                {pendingDelete === p.slug && (
                  <div className="lib-confirm2" onClick={(e) => e.stopPropagation()}>
                    <span className="lib-confirm2-q">Delete this project?</span>
                    <div className="lib-confirm2-btns">
                      <Button variant="danger" size="sm" disabled={deleting} onClick={() => void confirmDelete(p.slug)}>{deleting ? "Deleting…" : "Delete"}</Button>
                      <Button variant="quiet" size="sm" disabled={deleting} onClick={() => setPendingDelete(null)}>Cancel</Button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
