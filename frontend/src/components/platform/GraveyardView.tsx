import { useEffect, useRef, useState } from "react";
import { control, getGraveyard, ApiError } from "../../autonomous/api";
import { TRIAGE_REASON_LABELS, type GraveyardItem } from "../../autonomous/types";
import { Button, Card, Chip, Composer, SectionHeader } from "../ui";

interface Props {
  onOpenNode: (pid: string, nodeId: string) => void;
}

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

const SearchIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
);

/**
 * Graveyard (v3): the anti-portfolio — killed or passed spaces plus the curated
 * post-mortem corpus. Compact search composer, flat rows with danger kill-tags,
 * a quiet "Post-mortem" chip for external items, and ghost Open / Watch actions.
 * The watch/expiry shortcut stays fully intact.
 */
export default function GraveyardView({ onOpenNode }: Props) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<GraveyardItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [watching, setWatching] = useState<Record<string, "busy" | "watching" | "failed">>({});
  const seq = useRef(0);

  useEffect(() => {
    const mine = ++seq.current;
    setError(null);
    const t = window.setTimeout(() => {
      getGraveyard(q)
        .then((res) => { if (seq.current === mine) setItems(res); })
        .catch((e) => {
          if (seq.current === mine) setError(e instanceof ApiError ? e.message : "Could not read the graveyard.");
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
    <div className="pf-view ui-page">
      <section>
        <SectionHeader title="Rejected spaces"
          sub="Kill reasons expire. If a space died for a reason that no longer holds, watch it — sweeps alert you when its signals materially shift." />

        <div style={{ marginBottom: 16 }}>
          <Composer size="compact" value={q} onChange={setQ}
            leftIcon={<SearchIcon />} placeholder="Search titles, domains, kill reasons…"
            ariaLabel="Search the graveyard" />
        </div>

        {error ? (
          <Card pad><div className="ui-empty-body" role="alert" style={{ textAlign: "center", maxWidth: "none" }}>⚠︎ {error}</div></Card>
        ) : items == null ? (
          <Card pad><div className="ui-empty-body" style={{ textAlign: "center", maxWidth: "none" }}>Digging through the rejects…</div></Card>
        ) : items.length === 0 ? (
          <Card pad>
            <div className="ui-empty">
              <div className="ui-empty-title">{q ? "No matches" : "Nothing has been rejected yet"}</div>
              <div className="ui-empty-body">
                {q ? <>Nothing here matches “{q}” — every search token must hit. Try fewer words.</>
                  : "Killed gaps (a lens kill or viability ≤ 40) and spaces you pass on land here — the engine checks this list before re-proposing a space."}
              </div>
            </div>
          </Card>
        ) : (
          <>
            <div className="gv-count">
              {items.length} rejected space{items.length === 1 ? "" : "s"}
              {q ? " matching your search" : " across your runs and the post-mortem corpus"}.
            </div>
            <div className="gv-list">
              {items.map((it) => {
                const wstate = watching[it.node_id];
                return (
                  <div className="gv-row" key={it.node_id}>
                    <div className="gv-main">
                      <div className="gv-title-row">
                        <span className="gv-title">{it.title}</span>
                        {it.external && <Chip tone="outline">Post-mortem</Chip>}
                      </div>
                      {it.thesis_first_line && <div className="gv-thesis">{it.thesis_first_line}</div>}
                      <div className="ui-chiprow" style={{ marginTop: 8 }}>
                        {it.viability != null && (
                          <span className="ui-chip ui-chip--slate ui-chip--sm" title="Viability at time of death — market strength after adversarial testing.">viab {it.viability}</span>
                        )}
                        {it.kill_lenses.map((l) => (
                          <span className="ui-chip ui-chip--danger ui-chip--sm" key={l} title={`Killed by the ${l.replace(/_/g, " ")} lens.`}>{l.replace(/_/g, " ")}</span>
                        ))}
                        {it.triage_reason && (
                          <span className="ui-chip ui-chip--slate ui-chip--sm" title="Why you passed on it.">you: {reasonLabel(it.triage_reason)}</span>
                        )}
                      </div>
                      <div className="gv-meta">
                        {it.project_domain ?? (it.external ? "documented startup failure" : "unknown run")}
                        {it.updated_at ? ` · ${fmtWhen(it.updated_at)}` : ""}
                      </div>
                    </div>
                    {it.project_id && (
                      <div className="gv-actions">
                        <Button variant="quiet" size="sm" onClick={() => onOpenNode(it.project_id!, it.node_id)}>Open</Button>
                        <Button variant="quiet" size="sm"
                          disabled={wstate === "busy" || wstate === "watching"}
                          onClick={() => watchForExpiry(it)}
                          title="Flag this space for Space Watch — a sweep alerts you if its kill reason looks expired (new signals, regulatory shifts).">
                          {wstate === "watching" ? "Watching" : wstate === "busy" ? "Watching…" : wstate === "failed" ? "Retry watch" : "Watch for expiry"}
                        </Button>
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
