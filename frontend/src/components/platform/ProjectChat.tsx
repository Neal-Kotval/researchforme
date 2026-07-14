import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  assistantChat,
  type AssistantMessage,
  type AssistantAction,
} from "../../autonomous/api";

interface Msg {
  role: "you" | "assistant";
  text: string;
  actions?: AssistantAction[];
  error?: boolean;
}

/** doc.* tools mean the assistant changed a file — the workspace should reload. */
const DOC_WROTE = new Set(["doc.write", "doc.append"]);

interface Props {
  slug: string;
  projectTitle: string;
  /** Called when the assistant wrote a document, so the workspace re-reads disk. */
  onDocsChanged: () => void;
}

/**
 * A project-scoped chat inside the workspace (Phase 5 W-6). It runs the same
 * tool-using assistant as the global one, but with `project_slug` set — so it has
 * this project's documents in context and doc.* tools to edit them. "Add what we
 * learned to the plan" lands as a real diff in a file the founder owns, and the
 * workspace reloads to show it.
 */
export default function ProjectChat({ slug, projectTitle, onDocsChanged }: Props) {
  const [draft, setDraft] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [pending, setPending] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [msgs.length, pending]);

  const send = async () => {
    const text = draft.trim();
    if (!text || pending) return;
    setDraft("");
    const next: Msg[] = [...msgs, { role: "you", text }];
    setMsgs(next);
    setPending(true);
    try {
      const history: AssistantMessage[] = next.map((m) => ({
        role: m.role === "you" ? "user" : "assistant",
        text: m.text,
      }));
      const res = await assistantChat(history, slug);
      setMsgs((m) => [...m, { role: "assistant", text: res.reply, actions: res.actions }]);
      if (res.actions.some((a) => DOC_WROTE.has(a.tool))) onDocsChanged();
    } catch (e) {
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          error: true,
          text: e instanceof ApiError ? e.message : "The assistant did not answer.",
        },
      ]);
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="pc-panel">
      <div className="pc-head">Chat · {projectTitle}</div>
      <div className="pc-thread">
        {msgs.length === 0 && (
          <div className="pc-hint">
            Ask about this project, or tell me to change it — I can read and edit its
            documents. Try <em>"summarize the ideas into the plan"</em> or{" "}
            <em>"add an open question about pricing to project.md"</em>.
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`pc-msg pc-${m.role}${m.error ? " err" : ""}`}>
            <div className="pc-role">{m.role === "you" ? "You" : "Assistant"}</div>
            <div className="pc-text">{m.text}</div>
            {m.actions && m.actions.length > 0 && (
              <div className="pc-actions">
                {m.actions.map((a, j) => (
                  <span className="pc-action" key={j} title={JSON.stringify(a.args)}>
                    {a.tool}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
        {pending && <div className="pc-msg pc-assistant"><div className="pc-role">Assistant</div><div className="pc-text pc-typing">…</div></div>}
        <div ref={endRef} />
      </div>
      <div className="pc-composer">
        <input
          className="pc-input"
          placeholder={`Ask or instruct — scoped to ${projectTitle}`}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void send(); }}
          disabled={pending}
        />
        <button className="btn btn-primary" disabled={pending || !draft.trim()} onClick={() => void send()}>
          Send
        </button>
      </div>
    </div>
  );
}
