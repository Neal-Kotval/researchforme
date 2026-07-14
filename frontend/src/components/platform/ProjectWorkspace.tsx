import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  listLibraryDocs,
  readLibraryDoc,
  writeLibraryDoc,
  type LibraryDoc,
} from "../../autonomous/api";
import Markdown from "../autonomous/Markdown";

interface Props {
  slug: string;
  onBack: () => void;
}

/** Strip the `---` frontmatter so the reader sees the document, not its plumbing. */
function stripFrontmatter(text: string): string {
  const m = /^---\n[\s\S]*?\n---\n?/.exec(text);
  return m ? text.slice(m[0].length) : text;
}

/**
 * A project workspace (Phase 5 W-5): tabs across the project's documents, each
 * rendered with the existing safe Markdown renderer, editable in place.
 *
 * The file on disk is the truth. We keep the mtime we read and send it back on
 * write, so an edit made outside the app (vim, Cursor, another agent) surfaces
 * as a conflict rather than being silently clobbered — the user's other tools
 * are first-class citizens here, not intruders.
 */
export default function ProjectWorkspace({ slug, onBack }: Props) {
  const [docs, setDocs] = useState<LibraryDoc[] | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [mtime, setMtime] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const openDoc = useCallback(
    async (path: string) => {
      try {
        const d = await readLibraryDoc(slug, path);
        setActive(path);
        setContent(d.content);
        setMtime(d.mtime);
        setEditing(false);
        setError(null);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Could not open that document.");
      }
    },
    [slug],
  );

  useEffect(() => {
    (async () => {
      try {
        const list = await listLibraryDocs(slug);
        setDocs(list);
        if (list.length) await openDoc(list[0].path);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Could not read that project.");
      }
    })();
  }, [slug, openDoc]);

  const save = async () => {
    if (!active) return;
    setBusy(true);
    try {
      await writeLibraryDoc(slug, active, draft, mtime);
      setContent(draft);
      setEditing(false);
      setStatus("Saved");
      window.setTimeout(() => setStatus(""), 1800);
      const d = await readLibraryDoc(slug, active);
      setMtime(d.mtime);
      setDocs(await listLibraryDocs(slug));
      setError(null);
    } catch (e) {
      // A 409 means someone edited the file on disk while we had it open. Say so
      // plainly and keep the draft — never throw away the user's typing.
      setError(
        e instanceof ApiError
          ? e.message
          : "Could not save. Your draft is still here.",
      );
    } finally {
      setBusy(false);
    }
  };

  if (docs === null && !error) return <div className="gy-empty">Opening {slug}…</div>;

  return (
    <div className="workspace">
      <div className="ws-bar">
        <button className="btn btn-ghost" onClick={onBack}>
          ← Library
        </button>
        <span className="ws-slug">{slug}/</span>
        <div className="ws-actions">
          {status && <span className="ws-status">{status}</span>}
          {active && !editing && (
            <button
              className="btn btn-ghost"
              onClick={() => {
                setDraft(content);
                setEditing(true);
              }}
            >
              ✎ Edit
            </button>
          )}
          {editing && (
            <>
              <button className="btn btn-primary" disabled={busy} onClick={() => void save()}>
                Save
              </button>
              <button className="btn btn-ghost" disabled={busy} onClick={() => setEditing(false)}>
                Cancel
              </button>
            </>
          )}
        </div>
      </div>

      {error && <div className="ws-error">{error}</div>}

      <div className="ws-tabs">
        {(docs ?? []).map((d) => (
          <button
            key={d.path}
            className={`ws-tab${active === d.path ? " on" : ""}`}
            onClick={() => void openDoc(d.path)}
            title={d.path}
          >
            {/* The title truncates; the kind badge must not — it is what tells you
                this tab is an imported idea rather than the project's own plan. */}
            <span className="wt-title">{d.title}</span>
            {d.path.startsWith("ideas/") && <span className="wt-kind">idea</span>}
          </button>
        ))}
      </div>

      <div className="ws-body">
        {!active ? (
          <div className="gy-empty">This project has no documents yet.</div>
        ) : editing ? (
          <textarea
            className="ws-editor"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
          />
        ) : (
          <Markdown className="ws-doc" text={stripFrontmatter(content)} />
        )}
      </div>
    </div>
  );
}
