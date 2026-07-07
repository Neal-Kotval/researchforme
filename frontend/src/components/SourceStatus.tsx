import type { SourceName, SourceReport, SourceStatusKind } from "../types";

interface Props {
  reports: SourceReport[];
  loading: boolean;
}

const ORDER: SourceName[] = ["reddit", "hackernews", "arxiv", "github", "newsletter"];

const SOURCE_META: Record<SourceName, { glyph: string; label: string }> = {
  reddit: { glyph: "r/", label: "Reddit" },
  hackernews: { glyph: "HN", label: "Hacker News" },
  arxiv: { glyph: "aX", label: "arXiv" },
  github: { glyph: "GH", label: "GitHub" },
  newsletter: { glyph: "NL", label: "Newsletter" },
};

const STATUS_LABEL: Record<SourceStatusKind, string> = {
  live: "Live",
  mock: "Mock",
  unavailable: "Down",
  empty: "Empty",
};

/** Relative "freshness" from an ISO timestamp, e.g. "3d ago". */
function freshness(iso: string | null): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.round(days / 30)}mo ago`;
}

/**
 * Per-source telemetry strip: which pipes fired, live vs. mock, how many items
 * and how fresh. Renders shimmer skeletons while a run is in flight.
 */
export default function SourceStatus({ reports, loading }: Props) {
  if (loading && reports.length === 0) {
    return (
      <div className="sources-strip">
        {ORDER.map((name) => (
          <div className="source-chip" key={name} aria-hidden>
            <div className="src-ico skel" style={{ background: "var(--surface-3)" }}>
              {SOURCE_META[name].glyph}
            </div>
            <div className="src-body">
              <div className="skel" style={{ width: "60%", height: 13, marginBottom: 7 }} />
              <div className="skel" style={{ width: "80%", height: 10 }} />
            </div>
          </div>
        ))}
      </div>
    );
  }

  const byName = new Map(reports.map((r) => [r.name, r]));

  return (
    <div className="sources-strip" role="list" aria-label="Data sources">
      {ORDER.map((name) => {
        const r = byName.get(name);
        const meta = SOURCE_META[name];
        const status: SourceStatusKind = r?.status ?? "empty";
        const fresh = freshness(r?.freshest ?? null);
        return (
          <div className="source-chip" key={name} role="listitem">
            <div className={`src-ico ${name}`}>{meta.glyph}</div>
            <div className="src-body">
              <div className="src-top">
                <span className="src-name">{meta.label}</span>
                <span className={`src-badge ${status}`}>{STATUS_LABEL[status]}</span>
              </div>
              <div className="src-meta">
                <span>
                  <b>{r?.item_count ?? 0}</b> items
                </span>
                {fresh && <span>· freshest {fresh}</span>}
              </div>
              {r?.note && (
                <div className="src-note" title={r.note}>
                  {r.note}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
