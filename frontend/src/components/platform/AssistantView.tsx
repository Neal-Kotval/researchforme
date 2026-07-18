import { useEffect, useRef, useState } from "react";
import {
  assistantChat,
  ApiError,
  type AssistantAction,
  type AssistantMessage,
} from "../../autonomous/api";
import Markdown from "../autonomous/Markdown";
import { Composer } from "../ui";

interface Msg {
  role: "you" | "assistant";
  text: string;
  actions?: AssistantAction[];
  error?: boolean;
}

interface Props {
  onNav: (view: "home" | "explore" | "pressure" | "compare" | "assistant") => void;
  onActed: () => void;
}

const MUTATING = new Set(["gap.explore.start", "gap.run.pause", "gap.run.resume"]);
const EXAMPLES = [
  "find me something in independent HVAC, keep it under 200k tokens",
  "what survived the red team?",
];

/** Render a recorded tool call as its dotted-CLI form (mono is right for commands). */
function CommandBlock({ action }: { action: AssistantAction }) {
  return (
    <div className="as-cmd">
      <span className="as-cmd-chip">✓ ran</span>
      <span>
        <span className="as-cmd-verb">{action.tool}</span>
        {Object.entries(action.args).map(([k, v]) => (
          <span key={k}> --{k} <span className="as-cmd-arg">{typeof v === "string" ? `"${v}"` : String(v)}</span></span>
        ))}
      </span>
    </div>
  );
}

/**
 * Assistant (v3): the Claude/ChatGPT chat pattern — a centered thread where the
 * assistant speaks as plain text and only the user's turns get a bubble, with
 * example commands as suggestion chips above a pinned composer. Each turn runs
 * one real agent query; every command block below actually executed.
 */
export default function AssistantView({ onNav, onActed }: Props) {
  const [draft, setDraft] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [pending, setPending] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [msgs.length, pending]);

  const sendText = async (raw: string) => {
    const text = raw.trim();
    if (!text || pending) return;
    setDraft("");
    const nextMsgs: Msg[] = [...msgs, { role: "you", text }];
    setMsgs(nextMsgs);
    setPending(true);
    try {
      const history: AssistantMessage[] = nextMsgs.map((m) => ({ role: m.role === "you" ? "user" : "assistant", text: m.text }));
      const res = await assistantChat(history);
      setMsgs((m) => [...m, { role: "assistant", text: res.reply, actions: res.actions }]);
      if (res.actions.some((a) => MUTATING.has(a.tool))) onActed();
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", error: true, text: e instanceof ApiError ? e.message : "The assistant did not answer — is the backend running?" }]);
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="as-wrap2">
      <div className="as-thread2">
        <div className="as-col">
          {/* standing intro — assistant, plain text */}
          <div>
            <div className="as-role"><span className="pf-dot" />Assistant</div>
            <div className="as-assistant-text">
              I drive the platform through its tool layer — starting hunts, pausing and resuming runs,
              and ranking the survivors. Every command I run shows up inline, and results land on{" "}
              <button className="as-linkbtn" onClick={() => onNav("explore")}>Explore</button> and{" "}
              <button className="as-linkbtn" onClick={() => onNav("compare")}>Compare</button>.
            </div>
          </div>

          {msgs.map((m, i) =>
            m.role === "you" ? (
              <div className="as-user" key={i}><div className="as-user-bubble">{m.text}</div></div>
            ) : (
              <div key={i}>
                <div className="as-role"><span className="pf-dot" />Assistant</div>
                {m.actions?.map((a, j) => <CommandBlock key={j} action={a} />)}
                <div className={`as-assistant-text${m.error ? " error" : ""}`} role={m.error ? "alert" : undefined}>
                  {m.error ? <>⚠︎ {m.text}</> : <Markdown text={m.text} />}
                </div>
              </div>
            )
          )}

          {pending && (
            <div aria-live="polite">
              <div className="as-role"><span className="pf-dot pulse" />Assistant</div>
              <div className="as-assistant-text as-thinking2">Working — running the tool layer…</div>
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>

      <div className="as-composer2">
        <div className="as-composer2-col">
          {msgs.length === 0 && (
            <div className="as-suggests">
              {EXAMPLES.map((ex) => (
                <button key={ex} className="as-suggest" onClick={() => void sendText(ex)}>{ex}</button>
              ))}
            </div>
          )}
          <Composer
            value={draft}
            onChange={setDraft}
            onSubmit={() => void sendText(draft)}
            placeholder="Ask the assistant, or tell it what to hunt…"
            ariaLabel="Message the assistant"
            disabled={pending}
            submit={{ disabled: !draft.trim(), busy: pending, title: "Send" }}
          />
        </div>
      </div>
    </div>
  );
}
