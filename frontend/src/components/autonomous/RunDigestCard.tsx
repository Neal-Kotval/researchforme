import { useMemo } from "react";
import { normalizeDigest, type Project } from "../../autonomous/types";

interface Props {
  project: Project;
  /** "start a follow-up run" — prefill the new-exploration dialog with this question. */
  onFollowUp?: (question: string) => void;
}

/**
 * End-of-run digest (H4): the ONE-call summary the service writes on terminal
 * transition — top spaces, the kill pattern, and the open questions worth a
 * follow-up run. Renders nothing until a digest exists; a deterministic
 * fallback digest is honestly badged, never dressed up as model judgment.
 */
export default function RunDigestCard({ project, onFollowUp }: Props) {
  const digest = useMemo(() => normalizeDigest(project.digest), [project.digest]);
  if (!digest) return null;

  return (
    <div className="card run-digest">
      <div className="rd-head">
        <div>
          <div className="eyebrow">End-of-run digest</div>
          <div className="mod-sub">
            What this run concluded — three spaces worth your next hour, the pattern that
            killed the rest, and what to ask next.
          </div>
        </div>
        {digest.degraded && (
          <span
            className="rd-degraded"
            title="The model was unavailable when this run ended — this digest was computed deterministically (top viability + most common kill lens), not written by the model."
          >
            deterministic fallback
          </span>
        )}
      </div>

      {digest.top_spaces.length > 0 && (
        <div className="rd-sec">
          <div className="rd-sec-h">Top spaces</div>
          <ol className="rd-spaces">
            {digest.top_spaces.map((s, i) => (
              <li key={s.title + i}>
                <span className="rd-space-title">{s.title}</span>
                {s.why && <span className="rd-space-why"> — {s.why}</span>}
              </li>
            ))}
          </ol>
        </div>
      )}

      {digest.kill_pattern && (
        <div className="rd-sec">
          <div className="rd-sec-h">Kill pattern</div>
          <div className="rd-kill">{digest.kill_pattern}</div>
        </div>
      )}

      {digest.next_questions.length > 0 && (
        <div className="rd-sec">
          <div className="rd-sec-h">Open questions</div>
          <div className="rd-questions">
            {digest.next_questions.map((q, i) => (
              <div className="rd-q" key={i}>
                <span className="rd-q-text">{q}</span>
                {onFollowUp && (
                  <button
                    className="btn btn-sm"
                    onClick={() => onFollowUp(q)}
                    title="Open a new exploration prefilled to chase this question"
                  >
                    Start follow-up run ▸
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
