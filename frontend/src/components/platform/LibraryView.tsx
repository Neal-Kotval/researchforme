import { useEffect, useState } from "react";
import {
  ApiError,
  createLibraryProject,
  getLibraryRoot,
  listLibraryProjects,
  type LibraryProject,
} from "../../autonomous/api";
import ProjectWorkspace from "./ProjectWorkspace";

interface Props {
  /** Project slug to open, or null for the project list. */
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

/** Validation score → band class, matching the dashboard's read. */
function scoreBand(v: number): string {
  if (v >= 70) return "hi";
  if (v >= 45) return "mid";
  if (v >= 25) return "lo";
  return "crit";
}

/** A manila-folder glyph — the tab + body of a filesystem folder. */
function FolderIcon() {
  return (
    <svg className="lc-folder" width="46" height="38" viewBox="0 0 46 38" fill="none" aria-hidden>
      <path
        d="M3 8a3 3 0 0 1 3-3h11l4 4h19a3 3 0 0 1 3 3v18a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V8Z"
        fill="var(--accent-tint)"
        stroke="var(--accent-strong)"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M3 13h40" stroke="var(--accent-strong)" strokeWidth="1.1" opacity="0.4" />
    </svg>
  );
}

/**
 * The library (Phase 5 W-2): projects are real folders of markdown on disk.
 *
 * The library root is shown, deliberately: these are the user's files, in a
 * path they can open in any editor, and the app should never be coy about where
 * it put them.
 */
export default function LibraryView({ slug, onOpenProject }: Props) {
  const [projects, setProjects] = useState<LibraryProject[] | null>(null);
  const [root, setRoot] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

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

  useEffect(() => {
    if (slug === null) void load();
  }, [slug]);

  if (slug) {
    return <ProjectWorkspace slug={slug} onBack={() => onOpenProject(null)} />;
  }

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

  if (error) return <div className="gy-empty">{error}</div>;
  if (projects === null) return <div className="gy-empty">Reading the library…</div>;

  return (
    <div className="pf-view w940 library-view">
      <div className="lib-bar">
        <span className="lib-root" title="Your projects are plain markdown files here — open them in any editor, or put them in git.">
          {root || "…"}
        </span>
        {creating ? (
          <div className="lib-new">
            <input
              autoFocus
              className="lib-input"
              placeholder="Project name…"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void create();
                if (e.key === "Escape") setCreating(false);
              }}
            />
            <button className="btn btn-primary" disabled={busy || !title.trim()} onClick={() => void create()}>
              Create
            </button>
            <button className="btn btn-ghost" onClick={() => setCreating(false)}>
              Cancel
            </button>
          </div>
        ) : (
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            + New project
          </button>
        )}
      </div>

      {projects.length === 0 ? (
        <div className="gy-empty">
          <p>The library is empty.</p>
          <p className="insp-note">
            Star ideas in an exploration, then export them from <b>Starred</b> — each
            project becomes a folder of markdown you own.
          </p>
        </div>
      ) : (
        <div className="lib-grid">
          {projects.map((p) => (
            <button className="lib-card" key={p.slug} onClick={() => onOpenProject(p.slug)}>
              <div className="lc-top">
                <FolderIcon />
                {p.viability != null && (
                  <span
                    className={`lc-score ${scoreBand(p.viability)}`}
                    title="Project critique score — how well the assembled bet survived the red team"
                  >
                    {p.viability}
                  </span>
                )}
              </div>
              <span className="lc-title" title={p.title}>{p.title}</span>
              <div className="lc-meta">
                <span className={`lc-status st-${p.status}`}>{p.status}</span>
                <span>{p.idea_count} idea{p.idea_count === 1 ? "" : "s"}</span>
                <span>{fmtWhen(p.updated_at)}</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
