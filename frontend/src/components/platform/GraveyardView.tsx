import { useEffect, useRef, useState } from "react";
import { control, getGraveyard, ApiError } from "../../autonomous/api";
import { TRIAGE_REASON_LABELS, type GraveyardItem } from "../../autonomous/types";

interface Props {
  onOpenNode: (pid: string, nodeId: string) => void;
}

/** Taxonomy slug → label; free-text reasons render as written. */
function reasonLabel(reason: string): string {
  return (TRIAGE_REASON_LABELS as Record<string, string>)[reason] ?? reason;
}

function fmtWhen(iso: string | null): string {
  if (!iso) return "";
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${Math.max(m, 0)}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/**
 * The anti-portfolio (S3): every space that got killed (lens kill or
 * viability ≤ 40) or that the founder passed on, across every run — merged
 * with the curated post-mortem corpus (external items, flagged). Kill reasons
 * expire; the Watch shortcut flags a space so sweeps alert you when its
 * signals materially shift.
 */
export default function GraveyardView({ onOpenNode }: Props) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<GraveyardItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // node_id → watch-shortcut state ("busy" while in flight, "watching" after).
  const [watching, setWatching] = useState<Record<string, "busy" | "watching" | "failed">>({});
  const seq = useRef(0);

  useEffect(() => {
    const mine = ++seq.current;
    setError(null);
    const t = window.setTimeout(() => {
      getGraveyard(q)
        .then((res) => { if (seq.current === mine) setItems(res); })
        .catch((e) => {
          if (seq.current === mine) {
            setError(e instanceof ApiError ? e.message : "Could not read the graveyard.");
          }
        });
    }, q ? 250 : 0);
    return () => window.clearTimeout(t);
  }, [q]);

  const watchForExpiry = async (item: GraveyardItem) => {
    if (!item.project_id) return;
    setWatching((w) => ({ ...w, [item.node_id]: "busy" }));
    try {
      await control(item.project_id, { action: "watch_node", node_id: item.node_id });
      setWatching((w) => ({ ...w, [item.node_id]: "watching" }));
    } catch {
      setWatching((w) => ({ ...w, [item.node_id]: "failed" }));
    }
  };

  return (
    <div className="pf-view w940">
      <section className="pfm">
        <div className="pfm-head">
          <div className="pfm-title">Rejected spaces</div>
          <div className="pfm-sub">
            Kill reasons expire. If a space died for a reason that no longer holds,
            watch it — sweeps alert you when its signals materially shift.
          </div>
        </div>

        <div className="gy-search-bar">
          <input
            className="pf-scout-brief"
            placeholder="Search titles, domains, kill reasons…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Search the graveyard"
          />
        </div>

        {error ? (
          <div className="pf-empty" role="alert">⚠︎ {error}</div>
        ) : items == null ? (
          <div className="pf-empty" aria-busy="true">Digging through the rejects…</div>
        ) : items.length === 0 ? (
          <div className="pf-empty">
            {q
              ? `Nothing here matches “${q}” — every search token must hit. Try fewer words.`
              : "Nothing has been rejected yet. Killed gaps (lens kill or viability ≤ 40) and spaces you pass on land here — the engine checks this list before re-proposing a space."}
          </div>
        ) : (
          <>
            <div className="cmp-intro">
              {items.length} rejected space{items.length === 1 ? "" : "s"}
              {q ? " matching your search" : " across your runs and the post-mortem corpus"}.
            </div>
            <div className="gy-list">
              {items.map((it) => {
                const wstate = watching[it.node_id];
                return (
                  <div className="gy-row" key={it.node_id}>
                    <div className="gy-main">
                      <div className="gy-title-row">
                        <span className="gy-title">{it.title}</span>
                        {it.external && (
                          <span className="gy-ext" title="From the curated startup post-mortem corpus — not one of your runs.">
                            external post-mortem
                          </span>
                        )}
                      </div>
                      {it.thesis_first_line && (
                        <div className="gy-thesis">{it.thesis_first_line}</div>
                      )}
                      <div className="gy-chips">
                        {it.viability != null && (
                          <span className="gy-viab" title="Viability at time of death — market strength after adversarial testing.">
                            viab {it.viability}
                          </span>
                        )}
                        {it.kill_lenses.map((l) => (
                          <span className="gy-kill" key={l} title={`Killed by the ${l.replace(/_/g, " ")} lens.`}>
                            {l.replace(/_/g, " ")}
                          </span>
                        ))}
                        {it.triage_reason && (
                          <span className="gy-reason" title="Why you passed on it.">
                            you: {reasonLabel(it.triage_reason)}
                          </span>
                        )}
                      </div>
                      <div className="gy-meta">
                        {it.project_domain ?? (it.external ? "documented startup failure" : "unknown run")}
                        {it.updated_at ? ` · ${fmtWhen(it.updated_at)}` : ""}
                      </div>
                    </div>
                    {it.project_id && (
                      <div className="gy-actions">
                        <button
                          className="btn btn-sm"
                          onClick={() => onOpenNode(it.project_id!, it.node_id)}
                        >
                          Open
                        </button>
                        <button
                          className="btn btn-sm"
                          disabled={wstate === "busy" || wstate === "watching"}
                          onClick={() => watchForExpiry(it)}
                          title="Flag this space for Space Watch — a sweep alerts you if its kill reason looks expired (new signals, regulatory shifts)."
                        >
                          {wstate === "watching"
                            ? "Watching"
                            : wstate === "busy"
                              ? "Watching…"
                              : wstate === "failed"
                                ? "Retry watch"
                                : "Watch for expiry"}
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
