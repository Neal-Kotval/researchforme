import { useEffect, useState } from "react";
import { getWatchStatus, ApiError } from "../../autonomous/api";
import type { WatchedNodeStatus } from "../../autonomous/types";

interface Props {
  onOpenNode: (pid: string, nodeId: string) => void;
}

function fmtWhen(iso: string): string {
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${Math.max(m, 0)}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/**
 * The dashboard "Recent signals" block (C2): every watched space and the last
 * material shift a sweep found on it. Answers memo §4's "what changed?" —
 * alerts are only ever built from actual new source items, never invented.
 */
export default function RecentSignals({ onOpenNode }: Props) {
  const [watched, setWatched] = useState<WatchedNodeStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    getWatchStatus()
      .then(setWatched)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not read the watch list."));
  };
  useEffect(load, []);

  return (
    <section className="pfm">
      <div className="pfm-head">
        <div className="pfm-title">Recent signals</div>
        <div className="pfm-sub">
          Watched spaces and what moved. A sweep re-checks each one's sources and
          alerts only on a material shift.
        </div>
      </div>
      {error ? (
        <div className="pf-empty" role="alert">
          ⚠︎ {error}{" "}
          <button className="btn btn-sm" onClick={load} style={{ marginLeft: 8 }}>Retry</button>
        </div>
      ) : watched == null ? (
        <div className="pf-empty" aria-busy="true">Checking the watch list…</div>
      ) : watched.length === 0 ? (
        <div className="pf-empty">
          Nothing is watched yet. Watch a gap or segment from its inspector — or a
          rejected space from the graveyard — and shifts in its signals land here.
        </div>
      ) : (
        <div className="pf-signal-stack">
          {watched.map((w) => (
            <button
              className="pf-signal-row"
              key={`${w.project_id}:${w.node.id}`}
              onClick={() => onOpenNode(w.project_id, w.node.id)}
            >
              <div className="pf-signal-main">
                <div className="pf-signal-name">
                  {w.node.gap?.title ?? w.node.title}
                  <small> · {w.project_domain ?? "unknown run"}</small>
                </div>
                {w.last_alert ? (
                  <div className="pf-signal-alert">
                    {w.last_alert.summary}
                    <span className="pf-signal-meta">
                      {" "}· {w.last_alert.new_items} new item{w.last_alert.new_items === 1 ? "" : "s"}
                      {w.last_alert.regulatory_hit ? " · regulatory hit" : ""}
                      {w.last_alert.at ? ` · ${fmtWhen(w.last_alert.at)}` : ""}
                    </span>
                  </div>
                ) : (
                  <div className="pf-signal-quiet">
                    No material shift yet — each sweep compares against the last snapshot.
                  </div>
                )}
              </div>
              <span className="pf-signal-badge">{w.last_alert ? "moved" : "quiet"}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
