import { useEffect, useRef, useState } from "react";
import {
  assistantChat,
  ApiError,
  type AssistantAction,
  type AssistantMessage,
} from "../../autonomous/api";
import Markdown from "../autonomous/Markdown";

interface Msg {
  role: "you" | "assistant";
  text: string;
  actions?: AssistantAction[];
  error?: boolean;
}

interface Props {
  onNav: (view: "home" | "explore" | "pressure" | "compare" | "assistant") => void;
  /** Tool calls mutate runs — let the App-level projects poll catch up now. */
  onActed: () => void;
}

/** Tools that change platform state (vs. read-only lookups). */
const MUTATING = new Set(["gap.explore.start", "gap.run.pause", "gap.run.resume"]);

/** Render a recorded tool call as its dotted-CLI form. */
function CommandBlock({ action }: { action: AssistantAction }) {
  return (
    <div className="as-cmd">
      <span className="as-cmd-chip">✓ ran</span>
      <span>
        <span className="as-cmd-verb">{action.tool}</span>
        {Object.entries(action.args).map(([k, v]) => (
          <span key={k}>
            {" "}--{k}{" "}
            <span className="as-cmd-arg">
              {typeof v === "string" ? `"${v}"` : String(v)}
            </span>
          </span>
        ))}
      </span>
    </div>
  );
}

/**
 * The chat control surface (design handoff §5): a centered thread + a fixed
 * composer. Each turn runs one agent query on the backend whose MCP tools are
 * the platform's real tool layer — every command block below is a call that
 * actually executed, never a mock.
 */
export default function AssistantView({ onNav, onActed }: Props) {
  const [draft, setDraft] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [pending, setPending] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [msgs.length, pending]);

  const send = async () => {
    const text = draft.trim();
    if (!text || pending) return;
    setDraft("");
    const nextMsgs: Msg[] = [...msgs, { role: "you", text }];
    setMsgs(nextMsgs);
    setPending(true);
    try {
      const history: AssistantMessage[] = nextMsgs.map((m) => ({
        role: m.role === "you" ? "user" : "assistant",
        text: m.text,
      }));
      const res = await assistantChat(history);
      setMsgs((m) => [...m, { role: "assistant", text: res.reply, actions: res.actions }]);
      if (res.actions.some((a) => MUTATING.has(a.tool))) onActed();
    } catch (e) {
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          error: true,
          text:
            e instanceof ApiError
              ? e.message
              : "The assistant did not answer — is the backend running?",
        },
      ]);
    } finally {
      setPending(false);
      inputRef.current?.focus();
    }
  };

  return (
    <div className="as-wrap">
      <div className="as-thread">
        <div className="as-thread-inner">
          <div>
            <div className="as-label assistant"><span className="pf-dot" />Assistant</div>
            <div className="as-prose">
              I drive the platform through its tool layer — starting hunts, pausing and
              resuming runs, and ranking the survivors. Try{" "}
              <em>"find me something in independent HVAC, keep it under 200k tokens"</em> or{" "}
              <em>"what survived the red team?"</em> — every command I run shows up below,{" "}
              and results land on{" "}
              <button className="as-link" onClick={() => onNav("explore")}>Explore</button> and{" "}
              <button className="as-link" onClick={() => onNav("compare")}>Compare</button>.
            </div>
          </div>

          {msgs.map((m, i) =>
            m.role === "you" ? (
              <div key={i}>
                <div className="as-label"><span className="pf-dot" />You</div>
                <div className="as-bubble">{m.text}</div>
              </div>
            ) : (
              <div key={i}>
                <div className="as-label assistant"><span className="pf-dot" />Assistant</div>
                {m.actions?.map((a, j) => <CommandBlock key={j} action={a} />)}
                <div className={`as-prose${m.error ? " error" : ""}`} role={m.error ? "alert" : undefined}>
                  {m.error ? <>⚠︎ {m.text}</> : <Markdown text={m.text} />}
                </div>
              </div>
            )
          )}

          {pending && (
            <div aria-live="polite">
              <div className="as-label assistant"><span className="pf-dot pulse" />Assistant</div>
              <div className="as-prose as-thinking">Working — running the tool layer…</div>
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>

      <div className="as-composer">
        <div className="as-composer-inner">
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder="Ask the assistant, or tell it what to hunt…"
            aria-label="Message the assistant"
            disabled={pending}
          />
          <button className="btn btn-primary btn-sm" onClick={send} disabled={pending || !draft.trim()}>
            Send<span className="pf-kbd-dark">↵</span>
          </button>
        </div>
      </div>
    </div>
  );
}
