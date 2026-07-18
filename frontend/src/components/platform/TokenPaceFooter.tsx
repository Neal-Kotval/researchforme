import { useEffect, useRef, useState } from "react";
import { getUsage, setConcurrency, setUsagePolicy } from "../../autonomous/api";
import type { GlobalUsage } from "../../autonomous/types";
import { Button } from "../ui";

function fmtTok(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return `${Math.round(n)}`;
}

const QUICK = [8, 16, 32, 64, 100];

/**
 * Token pace footer (v3 §2): one compact row — "Token pace · {mode}" + a thin
 * accent meter + spend caption — that expands to a popover holding every number
 * (tok/min, /day projection, today/cap) AND the pacing controls (parallel-agent
 * concurrency + dynamic daily-cap). Nothing from the old multi-line meter is lost;
 * it is just folded behind one click.
 */
export default function TokenPaceFooter() {
  const [usage, setUsage] = useState<GlobalUsage | null>(null);
  const [open, setOpen] = useState(false);
  const [conc, setConc] = useState(32);
  const [concDirty, setConcDirty] = useState(false);
  const [cap, setCap] = useState("");
  const [pct, setPct] = useState(95);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      getUsage().then((u) => {
        if (!alive) return;
        setUsage(u);
        if (!concDirty && u.max_concurrency) setConc(u.max_concurrency);
      }).catch(() => {});
    tick();
    const id = window.setInterval(tick, 4_000);
    return () => { alive = false; window.clearInterval(id); };
  }, [concDirty]);

  // Seed the editable cap/percent when the popover opens.
  useEffect(() => {
    if (open && usage) {
      setCap(usage.daily_cap != null ? String(usage.daily_cap) : "");
      setPct(usage.limit_pct != null ? Math.round(usage.limit_pct * 100) : 95);
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Dismiss on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const applyConc = (n: number) => { setConc(n); setConcDirty(true); setConcurrency(n).catch(() => {}); };
  const applyPolicy = () => {
    const capN = parseInt(cap, 10);
    setUsagePolicy({
      daily_cap_tokens: Number.isNaN(capN) || capN <= 0 ? null : capN,
      limit_pct: Math.max(5, Math.min(100, pct)) / 100,
    }).then((u) => { setUsage(u); setOpen(false); }).catch(() => {});
  };

  const mode = usage
    ? usage.in_backoff ? `backoff ${Math.ceil(usage.backoff_remaining_s)}s` : usage.mode
    : "";
  const pctFill = usage?.usage_ratio != null ? Math.min(100, Math.round(usage.usage_ratio * 100)) : 0;
  const capText = !usage ? "usage unavailable"
    : usage.effective_cap
      ? <>{fmtTok(usage.daily_spent)} / {fmtTok(usage.effective_cap)} today · {pctFill}%</>
      : <>{fmtTok(usage.daily_spent)} today · no cap</>;

  return (
    <div className="tpf" ref={rootRef}>
      {open && usage && (
        <div className="tpf-pop" role="dialog" aria-label="Token pace detail">
          <div className="tpf-detail">
            <div className="ui-stat"><span className="ui-stat-value">{fmtTok(usage.rate_per_min)}</span><span className="ui-stat-label">tok/min now</span></div>
            <div className="ui-stat"><span className="ui-stat-value">~{fmtTok(usage.projected_24h)}</span><span className="ui-stat-label">/day projected</span></div>
            <div className="ui-stat"><span className="ui-stat-value">{fmtTok(usage.daily_spent)}</span><span className="ui-stat-label">today</span></div>
            <div className="ui-stat"><span className="ui-stat-value">{usage.active_concurrency ?? 0}/{usage.max_concurrency ?? conc}</span><span className="ui-stat-label">agents live</span></div>
          </div>

          <div className="tpf-divider" />
          <div className="tpf-ctl-title">Parallel agents · <b>{conc}</b></div>
          <input type="range" min={1} max={100} step={1} className="tpf-range"
            value={conc}
            onChange={(e) => { setConc(Number(e.target.value)); setConcDirty(true); }}
            onMouseUp={() => applyConc(conc)} onKeyUp={() => applyConc(conc)} onTouchEnd={() => applyConc(conc)} />
          <div className="tpf-quick">
            {QUICK.map((n) => (
              <button key={n} className={`tpf-quick-b${conc === n ? " on" : ""}`} onClick={() => applyConc(n)}>{n}</button>
            ))}
          </div>

          <div className="tpf-divider" />
          <div className="tpf-ctl-title">Daily token cap</div>
          <input type="number" min={0} className="tpf-input" placeholder="e.g. 2000000"
            value={cap} onChange={(e) => setCap(e.target.value)} />
          <div className="tpf-ctl-title">Stay under <b>{pct}%</b></div>
          <input type="range" min={5} max={100} step={5} className="tpf-range"
            value={pct} onChange={(e) => setPct(Number(e.target.value))} />
          <div className="tpf-actions">
            <Button variant="quiet" size="sm" onClick={() => setOpen(false)}>Close</Button>
            <Button variant="primary" size="sm" onClick={applyPolicy}>Apply limit</Button>
          </div>
        </div>
      )}

      <button className="tpf-row" onClick={() => setOpen((o) => !o)} aria-expanded={open} title="Token pace — click for detail and pacing controls">
        <div className="tpf-head">
          <span className="tpf-label">Token pace</span>
          {usage && <span className="tpf-mode">{mode}</span>}
        </div>
        <div className="ui-meter"><div className="ui-meter-fill" style={{ width: `${pctFill}%` }} /></div>
        <div className="tpf-cap">{capText}</div>
      </button>
    </div>
  );
}
