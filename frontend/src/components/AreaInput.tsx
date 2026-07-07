import { useState, type KeyboardEvent } from "react";
import ModelPicker from "./ModelPicker";

interface Props {
  onSubmit: (area: string, subSegments: string[]) => void;
  loading: boolean;
  model: string;
  onModelChange: (id: string) => void;
}

const EXAMPLES = [
  "personal finance for freelancers",
  "AI note-taking",
  "tools for solo devs",
  "sleep tracking for shift workers",
  "compliance for small clinics",
];

/**
 * Hero entry surface: an area query plus optional sub-segment chips, with
 * one-tap example seeds. Sub-segments are managed as tokens (Enter / comma
 * commits, Backspace on empty removes the last).
 */
export default function AreaInput({ onSubmit, loading, model, onModelChange }: Props) {
  const [area, setArea] = useState("");
  const [segDraft, setSegDraft] = useState("");
  const [segments, setSegments] = useState<string[]>([]);

  function addSegment(raw: string) {
    const v = raw.trim().replace(/,$/, "").trim();
    if (v && !segments.includes(v)) setSegments((s) => [...s, v]);
    setSegDraft("");
  }

  function onSegKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addSegment(segDraft);
    } else if (e.key === "Backspace" && !segDraft && segments.length) {
      setSegments((s) => s.slice(0, -1));
    }
  }

  function submit() {
    const a = area.trim();
    if (a.length < 2 || loading) return;
    // fold any half-typed segment into the list
    const finalSegs = segDraft.trim() ? [...segments, segDraft.trim()] : segments;
    onSubmit(a, finalSegs);
  }

  function useExample(ex: string) {
    setArea(ex);
  }

  return (
    <section className="hero">
      <span className="hero-eyebrow">
        <span className="spark">✦</span> Signal-driven market discovery
      </span>
      <h1>
        Find the gaps <span className="grad">worth building</span> in any market
      </h1>
      <p className="hero-lead">
        We mine Reddit + Hacker News demand, arXiv + GitHub momentum, and tech newsletters — then
        synthesize the ranked, defensible openings that competitors have left wide open.
      </p>

      <div className="input-card">
        <label className="field-label" htmlFor="area-input">
          Market area <span className="req">*</span>
        </label>
        <div className="big-input-wrap">
          <input
            id="area-input"
            className="big-input"
            placeholder="e.g. personal finance for freelancers"
            value={area}
            onChange={(e) => setArea(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            autoFocus
            aria-label="Market area to analyze"
          />
        </div>

        <div className="subseg">
          <label className="field-label" htmlFor="seg-input">
            Sub-segments <span style={{ color: "var(--text-ghost)", fontWeight: 400 }}>(optional — narrows the hunt)</span>
          </label>
          <div className="chips-input">
            {segments.map((s) => (
              <span className="seg-chip" key={s}>
                {s}
                <button
                  type="button"
                  aria-label={`Remove ${s}`}
                  onClick={() => setSegments((prev) => prev.filter((x) => x !== s))}
                >
                  ×
                </button>
              </span>
            ))}
            <input
              id="seg-input"
              value={segDraft}
              placeholder={segments.length ? "" : "e.g. invoicing, taxes, retirement…"}
              onChange={(e) => setSegDraft(e.target.value)}
              onKeyDown={onSegKey}
              onBlur={() => segDraft.trim() && addSegment(segDraft)}
              aria-label="Add a sub-segment"
            />
          </div>
        </div>

        <div className="examples">
          <div className="examples-label">Try one of these</div>
          <div className="example-chips">
            {EXAMPLES.map((ex) => (
              <button key={ex} type="button" className="example-chip" onClick={() => useExample(ex)}>
                {ex}
              </button>
            ))}
          </div>
        </div>

        <ModelPicker value={model} onChange={onModelChange} disabled={loading} />

        <div className="input-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={submit}
            disabled={area.trim().length < 2 || loading}
          >
            {loading ? "Scanning the market…" : "Find market gaps →"}
          </button>
        </div>
      </div>
    </section>
  );
}
