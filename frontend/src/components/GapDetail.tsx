import { useEffect } from "react";
import {
  SCORE_KEYS,
  SCORE_LABELS,
  SOURCE_LABEL,
  type RankedGap,
} from "../types";

interface Props {
  ranked: RankedGap | null;
  onClose: () => void;
}

function scoreColor(v: number): string {
  const stops = ["#6366f1", "#22d3ee", "#4ade80", "#ffcb47", "#f97316"];
  return stops[Math.min(stops.length - 1, Math.max(0, Math.round(v) - 1))];
}

function fmtDate(d: string | null): string | null {
  if (!d) return null;
  const t = new Date(d);
  if (Number.isNaN(t.getTime())) return d; // sources vary; show raw if unparseable
  return t.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/**
 * Right-side drawer with the full case for a gap: thesis, the four narrative
 * pillars, competitor blind-spots, and clickable evidence. Escape / scrim close.
 */
export default function GapDetail({ ranked, onClose }: Props) {
  useEffect(() => {
    if (!ranked) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ranked, onClose]);

  if (!ranked) return null;
  const g = ranked.gap;

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={`Details for ${g.title}`}>
        <div className="drawer-head">
          <div>
            <div className="detail-rank">
              {ranked.rank === 1 && <span style={{ color: "var(--gold)" }}>★ Top pick · </span>}
              Rank #{ranked.rank} · Composite {ranked.composite.toFixed(2)}
            </div>
            <h3 className="detail-title">{g.title}</h3>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Close details">
            ×
          </button>
        </div>

        <div className="drawer-body">
          <p className="detail-thesis">{g.thesis}</p>

          {/* score row */}
          <div className="detail-scorebar">
            {SCORE_KEYS.map((k) => {
              const v = g.scores[k];
              return (
                <div className="dsb-item" key={k}>
                  <div className="dsb-val" style={{ color: scoreColor(v) }}>
                    {v}
                  </div>
                  <div className="dsb-lab">{SCORE_LABELS[k]}</div>
                </div>
              );
            })}
          </div>

          {g.empty_for_a_reason && (
            <div className="empty-flag">
              <span className="ef-ico">⚠︎</span>
              <div>
                <div className="ef-t">Possibly empty for a reason</div>
                <div className="ef-d">{g.empty_reason || "This opening may be structurally unattractive — validate before committing."}</div>
              </div>
            </div>
          )}

          {/* narrative pillars */}
          <div className="detail-block">
            <h4>The case</h4>
            <div className="kv-grid">
              {g.why_now && (
                <div className="kv accent">
                  <div className="kv-k">Why now</div>
                  <div className="kv-v">{g.why_now}</div>
                </div>
              )}
              <div className="kv">
                <div className="kv-k">Wedge</div>
                <div className="kv-v">{g.wedge}</div>
              </div>
              <div className="kv warn">
                <div className="kv-k">Riskiest assumption</div>
                <div className="kv-v">{g.riskiest_assumption}</div>
              </div>
              <div className="kv warn">
                <div className="kv-k">Weakest link</div>
                <div className="kv-v">{g.weakest_link}</div>
              </div>
            </div>
          </div>

          {/* competitors */}
          {g.competitors.length > 0 && (
            <div className="detail-block">
              <h4>Top competitors &amp; their blind spots</h4>
              <div className="table-scroll">
                <table className="comp-table">
                  <thead>
                    <tr>
                      <th>Player</th>
                      <th>Segment</th>
                      <th>Tier</th>
                      <th>Blind spot</th>
                    </tr>
                  </thead>
                  <tbody>
                    {g.competitors.map((c, i) => (
                      <tr key={c.name + i}>
                        <td>
                          {c.url ? (
                            <a className="comp-name" href={c.url} target="_blank" rel="noreferrer">
                              {c.name} <span className="ext">↗</span>
                            </a>
                          ) : (
                            <span className="comp-name">{c.name}</span>
                          )}
                          {c.positioning && (
                            <div style={{ color: "var(--text-faint)", fontSize: 11, marginTop: 3 }}>{c.positioning}</div>
                          )}
                        </td>
                        <td className="comp-seg">{c.segment}</td>
                        <td>
                          <span className="price-tier">{c.price_tier || "—"}</span>
                        </td>
                        <td className="comp-weak">{c.weakness}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* evidence */}
          {g.evidence.length > 0 && (
            <div className="detail-block">
              <h4>Evidence ({g.evidence.length})</h4>
              <div className="evidence-list">
                {g.evidence.map((e, i) => {
                  const date = fmtDate(e.date);
                  return (
                    <div className="ev-item" key={i}>
                      <span className={`ev-src ${e.source}`}>{SOURCE_LABEL[e.source]}</span>
                      <div className="ev-content">
                        <div className="ev-quote">{e.quote}</div>
                        <div className="ev-foot">
                          {date && <span>{date}</span>}
                          {e.url && (
                            <a href={e.url} target="_blank" rel="noreferrer">
                              View source ↗
                            </a>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {g.tags.length > 0 && (
            <div className="detail-block">
              <h4>Tags</h4>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {g.tags.map((t) => (
                  <span className="tagpill" key={t}>
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
