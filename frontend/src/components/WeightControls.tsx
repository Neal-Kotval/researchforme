import { SCORE_KEYS, SCORE_LABELS, DEFAULT_WEIGHTS, type Weights } from "../types";

interface Props {
  weights: Weights;
  onChange: (w: Weights) => void;
  reweighting: boolean;
}

const HINTS: Record<string, string> = {
  demand_strength: "How loud is the unmet need",
  competitive_openness: "How wide open the field is",
  trend_tailwind: "Momentum making it newly feasible",
  feasibility: "How buildable it is now",
  willingness_to_pay: "How ready buyers are to pay",
};

/**
 * Five weight sliders that re-rank the report without re-fetching. Changes are
 * pushed up immediately; App debounces the /api/rerank round-trip.
 */
export default function WeightControls({ weights, onChange, reweighting }: Props) {
  function setOne(key: keyof Weights, value: number) {
    onChange({ ...weights, [key]: value });
  }

  const isDefault = SCORE_KEYS.every((k) => Math.abs(weights[k] - DEFAULT_WEIGHTS[k]) < 1e-6);

  return (
    <div className="card weights-card">
      <div className="weights-head">
        <div>
          <div className="eyebrow">Priorities</div>
          <h3 className="card-title">Weighting</h3>
        </div>
        {reweighting ? (
          <span className="wc-reweighting">
            <span className="spin" /> re-ranking
          </span>
        ) : (
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => onChange({ ...DEFAULT_WEIGHTS })}
            disabled={isDefault}
            title="Reset to defaults"
          >
            Reset
          </button>
        )}
      </div>
      <p className="card-desc">Tune what matters. The map and ranking update live.</p>

      {SCORE_KEYS.map((k) => (
        <div className="slider-row" key={k}>
          <div className="slider-top">
            <label className="slider-label" htmlFor={`w-${k}`}>
              {SCORE_LABELS[k]}
            </label>
            <span className="slider-val">{weights[k].toFixed(1)}×</span>
          </div>
          <input
            id={`w-${k}`}
            type="range"
            min={0}
            max={2}
            step={0.1}
            value={weights[k]}
            onChange={(e) => setOne(k, parseFloat(e.target.value))}
            aria-label={`${SCORE_LABELS[k]} weight — ${HINTS[k]}`}
          />
        </div>
      ))}
    </div>
  );
}
