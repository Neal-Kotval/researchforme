import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  consolidateProject,
  listLibraryDocs,
  readLibraryDoc,
  writeLibraryDoc,
  type LibraryDoc,
} from "../../autonomous/api";
import Markdown from "../autonomous/Markdown";
import ProjectChat from "./ProjectChat";

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
 * The engine writes long, explanatory titles ("The Kernel Equivalence Harness: CI
 * for hand-written GPU kernels"). Good in a heading, terrible in a list — the
 * subtitle after the colon is what makes the rail unreadable. Keep the name, drop
 * the explanation; the full title is still the tooltip and the document's own H1.
 */
function shortTitle(title: string): string {
  const head = title.split(/\s+[—–:]\s+|:\s+/)[0].trim() || title;
  return head.length > 42 ? `${head.slice(0, 41).trimEnd()}…` : head;
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
  const [consolidating, setConsolidating] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);

  // The assistant may edit documents; re-read the list and the open doc from disk.
  const reloadFromDisk = useCallback(async () => {
    try {
      setDocs(await listLibraryDocs(slug));
      if (active) {
        const d = await readLibraryDoc(slug, active);
        setContent(d.content);
        setMtime(d.mtime);
      }
    } catch {
      /* a transient read failure shouldn't break the workspace */
    }
  }, [slug, active]);

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

  // The project's own documents vs. the ideas imported into it — two different
  // kinds of reading, so they get two groups rather than one undifferentiated list.
  const plan = (docs ?? []).filter((d) => !d.path.startsWith("ideas/"));
  const ideas = (docs ?? []).filter((d) => d.path.startsWith("ideas/"));

  const consolidate = async () => {
    setConsolidating(true);
    setError(null);
    try {
      const { path } = await consolidateProject(slug);
      setDocs(await listLibraryDocs(slug));
      await openDoc(path);
    } catch (e) {
      // 503 = no usable LLM backend; the endpoint refuses to write canned content.
      setError(e instanceof ApiError ? e.message : "Could not consolidate the ideas.");
    } finally {
      setConsolidating(false);
    }
  };

  if (docs === null && !error) return <div className="gy-empty">Opening {slug}…</div>;

  return (
    <div className="pf-view workspace">
      <div className="ws-bar">
        <button className="btn btn-ghost" onClick={onBack}>
          ← Library
        </button>
        <span className="ws-slug">{slug}/</span>
        <div className="ws-actions">
          {status && <span className="ws-status">{status}</span>}
          {ideas.length >= 2 && !editing && (
            <button
              className="btn btn-ghost"
              disabled={consolidating}
              title="Read all the ideas together and write one thesis + plan — naming where they conflict, and what doesn't belong. One model pass."
              onClick={() => void consolidate()}
            >
              {consolidating ? "Consolidating…" : "⋈ Consolidate ideas"}
            </button>
          )}
          <button
            className={`btn btn-ghost${chatOpen ? " on" : ""}`}
            title="Chat about this project — the assistant can read and edit its documents"
            onClick={() => setChatOpen((v) => !v)}
          >
            💬 Chat
          </button>
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

      {/* A rail, not a tab strip. A project accumulates ideas — ten of them in one
          horizontal strip is an unreadable scroll, and the long generated titles
          made it worse. The rail separates the project's own documents from its
          ideas, scales down the list, and gives each idea room for a real name. */}
      <div className={`ws-split${chatOpen ? " with-chat" : ""}`}>
        <nav className="ws-rail" aria-label="Project documents">
          <div className="wr-group">
            <div className="wr-head">Project</div>
            {plan.map((d) => (
              <button
                key={d.path}
                className={`wr-item${active === d.path ? " on" : ""}`}
                onClick={() => void openDoc(d.path)}
                title={d.path}
              >
                {d.title}
              </button>
            ))}
          </div>

          {ideas.length > 0 && (
            <div className="wr-group">
              <div className="wr-head">
                Ideas <span className="wr-count">{ideas.length}</span>
              </div>
              {ideas.map((d) => (
                <button
                  key={d.path}
                  className={`wr-item${active === d.path ? " on" : ""}`}
                  onClick={() => void openDoc(d.path)}
                  title={d.title}
                >
                  {shortTitle(d.title)}
                </button>
              ))}
            </div>
          )}
        </nav>

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

        {chatOpen && (
          <ProjectChat slug={slug} projectTitle={slug} onDocsChanged={() => void reloadFromDisk()} />
        )}
      </div>
    </div>
  );
}
