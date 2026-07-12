import { useEffect, useState } from "react";
import {
  distillPreferences,
  getPreferences,
  updatePreferences,
  ApiError,
} from "../../autonomous/api";
import type { PreferencesState } from "../../autonomous/types";

/** The distill card appears once this many triage verdicts have accumulated. */
const DISTILL_THRESHOLD = 8;

/**
 * The H3 "distill what your passes say" card. Quiet by design: it appears only
 * once ≥8 triage verdicts exist and no confirmed preferences are active. The
 * whole flow is review-before-apply — a distilled proposal is clearly marked
 * pending and steers NOTHING until the founder confirms (possibly edited)
 * text. The distill call is one user-initiated cheap-model pass; a backend
 * that can't distill honestly answers 503, surfaced as-is.
 */
export default function PreferenceDistillCard() {
  const [state, setState] = useState<PreferencesState | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<"distill" | "confirm" | "dismiss" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Session-local closure after confirm/dismiss so the card doesn't nag.
  const [closed, setClosed] = useState<"applied" | "dismissed" | null>(null);

  useEffect(() => {
    getPreferences()
      .then((s) => {
        setState(s);
        if (s.preferences?.status === "pending") setText(s.preferences.learned_preferences);
      })
      .catch(() => setState(null)); // quiet card — a failed read shows nothing
  }, []);

  const distill = async () => {
    setBusy("distill");
    setError(null);
    try {
      const prefs = await distillPreferences();
      setText(prefs.learned_preferences);
      setState((s) => (s ? { ...s, preferences: prefs } : s));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Distillation failed — the backend did not answer.");
    } finally {
      setBusy(null);
    }
  };

  const save = async (status: "active" | "dismissed") => {
    setBusy(status === "active" ? "confirm" : "dismiss");
    setError(null);
    try {
      const prefs = await updatePreferences({ learned_preferences: text, status });
      setState((s) => (s ? { ...s, preferences: prefs } : s));
      setClosed(status === "active" ? "applied" : "dismissed");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save preferences.");
    } finally {
      setBusy(null);
    }
  };

  if (state == null) return null;
  if (closed) {
    return (
      <div className="pf-pref-card done">
        {closed === "applied"
          ? "Preferences applied — future runs carry them as user-confirmed steering."
          : "Dismissed. The card returns as your triage record grows."}
      </div>
    );
  }

  const prefs = state.preferences;
  const pending = prefs?.status === "pending";
  // Contract H3: the card appears at ≥8 triage events with no ACTIVE prefs.
  if (!pending && (prefs?.status === "active" || state.triage_count < DISTILL_THRESHOLD)) {
    return null;
  }

  return (
    <section className="pfm">
      <div className="pf-pref-card">
        {pending ? (
          <>
            <div className="pf-pref-head">
              <span className="pf-pref-title">Learned preferences — proposed</span>
              <span className="pf-pref-pending">pending · not applied yet</span>
            </div>
            <div className="pf-pref-sub">
              Distilled from your {state.triage_count} triage verdict{state.triage_count === 1 ? "" : "s"}.
              Edit freely — nothing steers a run until you confirm.
            </div>
            <textarea
              className="pf-pref-text"
              rows={4}
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-label="Proposed learned preferences"
            />
            <div className="pf-pref-actions">
              <button
                className="btn btn-primary btn-sm"
                disabled={busy != null || text.trim() === ""}
                onClick={() => save("active")}
              >
                {busy === "confirm" ? "Applying…" : "Confirm & apply"}
              </button>
              <button
                className="btn btn-sm"
                disabled={busy != null}
                onClick={() => save("dismissed")}
              >
                {busy === "dismiss" ? "Dismissing…" : "Dismiss"}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="pf-pref-head">
              <span className="pf-pref-title">Distill what your passes say</span>
            </div>
            <div className="pf-pref-sub">
              {state.triage_count} triage verdicts logged. One cheap model pass proposes
              steering preferences from them — you review and confirm before anything applies.
            </div>
            <div className="pf-pref-actions">
              <button className="btn btn-sm" disabled={busy != null} onClick={distill}>
                {busy === "distill" ? "Distilling…" : "Distill preferences"}
              </button>
            </div>
          </>
        )}
        {error && (
          <div className="pf-pref-error" role="alert">⚠︎ {error}</div>
        )}
      </div>
    </section>
  );
}
