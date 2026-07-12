import { useEffect, useRef, useState } from "react";

interface Msg {
  role: "you" | "assistant";
  text: string;
}

interface Props {
  onNav: (view: "home" | "pressure" | "compare" | "assistant") => void;
  onNewExploration: () => void;
}

/**
 * The chat control surface (design handoff §5): a centered thread + a fixed
 * composer, with the platform's tool layer rendered inline as command blocks.
 * The conversational backend isn't wired yet — the surface is honest about
 * that and routes you to the working flows instead of pretending.
 */
export default function AssistantView({ onNav, onNewExploration }: Props) {
  const [draft, setDraft] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [msgs.length]);

  const send = () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    setMsgs((m) => [
      ...m,
      { role: "you", text },
      {
        role: "assistant",
        text:
          "The conversational backend isn't wired up yet, so I can't act on that from here. " +
          "Everything I'd do runs through the tool layer below — start a run from Home, or check the shortlist in Compare.",
      },
    ]);
  };

  return (
    <div className="as-wrap">
      <div className="as-thread">
        <div className="as-thread-inner">
          <div>
            <div className="as-label assistant"><span className="pf-dot" />Assistant</div>
            <div className="as-prose">
              I drive the whole platform through its tool layer — starting hunts, red-teaming
              candidates, and ranking survivors. A typical run looks like this:
            </div>
            <div className="as-cmd">
              <span className="as-cmd-chip">tool</span>
              <span>
                <span className="as-cmd-verb">gap.explore.start</span> --space{" "}
                <span className="as-cmd-arg">"independent HVAC"</span> --cap{" "}
                <span className="as-cmd-arg">$10.00</span>
              </span>
            </div>
            <div className="as-cmd">
              <span className="as-cmd-chip">tool</span>
              <span>
                <span className="as-cmd-verb">gap.redteam.run</span> --idea{" "}
                <span className="as-cmd-arg">hvac-pm-copilot</span> --rigor{" "}
                <span className="as-cmd-arg">deep</span>
              </span>
            </div>
            <div className="as-prose">
              Chat-driven control lands soon. Until then:{" "}
              <button className="as-link" onClick={onNewExploration}>New exploration</button> starts a
              hunt, <button className="as-link" onClick={() => onNav("pressure")}>Pressure-test</button>{" "}
              shows the red team's findings, and{" "}
              <button className="as-link" onClick={() => onNav("compare")}>Compare</button> ranks the
              survivors.
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
                <div className="as-prose">{m.text}</div>
              </div>
            )
          )}
          <div ref={endRef} />
        </div>
      </div>

      <div className="as-composer">
        <div className="as-composer-inner">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder="Ask the assistant, or type / for a command…"
            aria-label="Message the assistant"
          />
          <button className="btn btn-primary btn-sm" onClick={send}>
            Send<span className="pf-kbd-dark">↵</span>
          </button>
        </div>
      </div>
    </div>
  );
}
