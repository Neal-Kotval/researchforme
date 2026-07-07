import { useEffect, useMemo, useRef, useState } from "react";
import { listProjects } from "../autonomous/api";
import type { Project } from "../autonomous/types";

export interface PaletteCtx {
  mode: "single" | "autonomous";
  setMode: (m: "single" | "autonomous") => void;
  jumpProject: (pid: string) => void;
  newExploration: () => void;
}

interface Cmd {
  id: string;
  label: string;
  hint?: string;
  icon: string;
  run: () => void;
}

/**
 * ⌘K command palette — the fast way around the app. Fuzzy-filter over quick
 * actions (new exploration, switch modes) and every past exploration (jump
 * straight to its tree). Arrow keys + Enter, Esc to close. Design-system styled.
 */
export default function CommandPalette({
  open, onClose, ctx,
}: {
  open: boolean;
  onClose: () => void;
  ctx: PaletteCtx;
}) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [projects, setProjects] = useState<Project[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  // Load the exploration list whenever the palette opens.
  useEffect(() => {
    if (!open) return;
    setQ("");
    setSel(0);
    listProjects().then(setProjects).catch(() => setProjects([]));
    const id = window.setTimeout(() => inputRef.current?.focus(), 20);
    return () => window.clearTimeout(id);
  }, [open]);

  const commands = useMemo<Cmd[]>(() => {
    const base: Cmd[] = [
      { id: "new", label: "New exploration", hint: "autonomous", icon: "＋", run: ctx.newExploration },
      { id: "single", label: "Single-area search", hint: "mode", icon: "◎", run: () => ctx.setMode("single") },
      { id: "auto", label: "Autonomous explorer", hint: "mode", icon: "✦", run: () => ctx.setMode("autonomous") },
    ];
    const projCmds: Cmd[] = projects.map((p) => ({
      id: `p:${p.id}`,
      label: p.domain,
      hint: `${p.status}${p.stats.gaps ? ` · ${p.stats.gaps} gaps` : ""}${p.stats.stars ? ` · ${p.stats.stars}★` : ""}`,
      icon: "▸",
      run: () => ctx.jumpProject(p.id),
    }));
    return [...base, ...projCmds];
  }, [projects, ctx]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return commands;
    return commands.filter(
      (c) => c.label.toLowerCase().includes(needle) || (c.hint ?? "").toLowerCase().includes(needle)
    );
  }, [commands, q]);

  useEffect(() => {
    if (sel >= filtered.length) setSel(Math.max(0, filtered.length - 1));
  }, [filtered.length, sel]);

  if (!open) return null;

  const runAt = (i: number) => {
    const c = filtered[i];
    if (!c) return;
    onClose();
    c.run();
  };

  return (
    <div className="cmdk-scrim" onClick={onClose}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-label="Command palette" onClick={(e) => e.stopPropagation()}>
        <div className="cmdk-input-row">
          <span className="cmdk-ico" aria-hidden>⌕</span>
          <input
            ref={inputRef}
            className="cmdk-input"
            placeholder="Jump to an exploration or run a command…"
            value={q}
            onChange={(e) => { setQ(e.target.value); setSel(0); }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(filtered.length - 1, s + 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
              else if (e.key === "Enter") { e.preventDefault(); runAt(sel); }
              else if (e.key === "Escape") { e.preventDefault(); onClose(); }
            }}
          />
          <kbd className="cmdk-esc">esc</kbd>
        </div>
        <div className="cmdk-list" role="listbox">
          {filtered.length === 0 ? (
            <div className="cmdk-empty">No matches.</div>
          ) : (
            filtered.map((c, i) => (
              <button
                key={c.id}
                role="option"
                aria-selected={i === sel}
                className={`cmdk-item${i === sel ? " sel" : ""}`}
                onMouseEnter={() => setSel(i)}
                onClick={() => runAt(i)}
              >
                <span className="cmdk-item-ico">{c.icon}</span>
                <span className="cmdk-item-label">{c.label}</span>
                {c.hint && <span className="cmdk-item-hint">{c.hint}</span>}
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
