import { MODELS } from "../types";

interface Props {
  value: string;
  onChange: (id: string) => void;
  /** Compact row variant for the top bar; default is the full labelled control. */
  compact?: boolean;
  disabled?: boolean;
}

/**
 * Segmented control for choosing which Claude model synthesizes the gaps.
 * All options run on the user's Claude subscription; the choice trades depth
 * for speed. Styled entirely from design tokens (see .seg-control in index.css).
 */
export default function ModelPicker({ value, onChange, compact = false, disabled = false }: Props) {
  return (
    <div className={`model-picker${compact ? " compact" : ""}`}>
      {!compact && (
        <label className="field-label" style={{ marginBottom: 8 }}>
          Synthesis model
        </label>
      )}
      <div className="seg-control" role="radiogroup" aria-label="Synthesis model">
        {MODELS.map((m) => {
          const active = m.id === value;
          return (
            <button
              key={m.id}
              type="button"
              className="seg-option"
              role="radio"
              aria-checked={active}
              aria-pressed={active}
              disabled={disabled}
              onClick={() => onChange(m.id)}
              title={`${m.label} — ${m.sub}`}
            >
              <span className="so-label">{m.label}</span>
              <span className="so-sub">{m.sub}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
