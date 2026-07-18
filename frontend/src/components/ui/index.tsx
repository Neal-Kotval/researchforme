/* ============================================================================
   v3 shared primitives — Button · Card · Chip · ScoreBadge · StatRow · Meter
   · EmptyState · SectionHeader · KbdChip · Table · Segmented.
   The vocabulary every migrated page reuses. Styles live in ./ui.css.
   ========================================================================== */
import type { ButtonHTMLAttributes, ReactNode } from "react";

/* -------------------------------------------------------------- Button ---- */
type BtnVariant = "primary" | "secondary" | "quiet" | "danger";
interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BtnVariant;
  size?: "md" | "sm";
  iconLeft?: ReactNode;
  kbd?: string;
  loading?: boolean;
  loadingLabel?: string;
}
const VARIANT_CLASS: Record<BtnVariant, string> = {
  primary: "btn btn-primary",
  secondary: "btn",
  quiet: "btn btn-quiet",
  danger: "btn btn-danger",
};
export function Button({
  variant = "secondary", size = "md", iconLeft, kbd, loading, loadingLabel,
  children, className = "", disabled, ...rest
}: ButtonProps) {
  const cls = [VARIANT_CLASS[variant], size === "sm" ? "btn-sm" : "", className]
    .filter(Boolean).join(" ");
  return (
    <button className={cls} disabled={disabled || loading} {...rest}>
      {iconLeft && !loading ? <span className="btn-ico" aria-hidden="true">{iconLeft}</span> : null}
      <span>{loading ? (loadingLabel ?? children) : children}</span>
      {kbd ? <KbdChip onDark={variant === "primary"}>{kbd}</KbdChip> : null}
    </button>
  );
}

/* ---------------------------------------------------------------- Card ---- */
interface CardProps {
  as?: "div" | "button" | "section";
  interactive?: boolean;
  pad?: boolean;
  className?: string;
  children?: ReactNode;
  onClick?: () => void;
  [k: string]: any;
}
export function Card({ as = "div", interactive, pad, className = "", children, ...rest }: CardProps) {
  const Tag = as as any;
  const cls = ["ui-card", pad ? "ui-card--pad" : "", interactive ? "ui-card--interactive" : "", className]
    .filter(Boolean).join(" ");
  return <Tag className={cls} {...rest}>{children}</Tag>;
}

/* --------------------------------------------------------- SectionHeader -- */
interface SectionHeaderProps {
  title: ReactNode;
  sub?: ReactNode;
  aside?: ReactNode;
  howItWorks?: { open: boolean; onToggle: () => void; label?: string };
}
export function SectionHeader({ title, sub, aside, howItWorks }: SectionHeaderProps) {
  return (
    <div className="ui-section">
      <div className="ui-section-main">
        <div className="ui-section-title">{title}</div>
        {sub ? <div className="ui-section-sub">{sub}</div> : null}
      </div>
      {(aside || howItWorks) && (
        <div className="ui-section-aside">
          {aside}
          {howItWorks && (
            <button className="ui-howto-btn" onClick={howItWorks.onToggle}
              aria-expanded={howItWorks.open}>
              {howItWorks.label ?? "How it works"} {howItWorks.open ? "▲" : "▾"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- Chip ---- */
type ChipTone = "tint" | "slate" | "outline" | "danger";
type DotTone = "accent" | "slate" | "ink" | "danger" | "none";
export function Chip({ tone = "tint", dot = "none", pulse, children }:
  { tone?: ChipTone; dot?: DotTone; pulse?: boolean; children: ReactNode }) {
  const toneCls = tone === "slate" ? "ui-chip--slate" : tone === "outline" ? "ui-chip--outline"
    : tone === "danger" ? "ui-chip--danger" : "";
  const dotCls = dot === "none" ? "" :
    ["ui-chip-dot", `ui-chip-dot--${dot}`, pulse ? "ui-chip-dot--pulse" : ""].filter(Boolean).join(" ");
  return (
    <span className={["ui-chip", toneCls].filter(Boolean).join(" ")}>
      {dot !== "none" ? <span className={dotCls} /> : null}
      {children}
    </span>
  );
}

/* -------------------------------------------------------------- KbdChip --- */
export function KbdChip({ children, onDark }: { children: ReactNode; onDark?: boolean }) {
  return <span className={onDark ? "ui-kbd ui-kbd--onDark" : "ui-kbd"}>{children}</span>;
}

/* ------------------------------------------------------------ ScoreBadge -- */
/* verified → solid tint pill; unverified/null → dashed slate outline
   (the app's one dashed border); zero → slate outline solid. */
export function ScoreBadge({ value, verified = true, size }:
  { value: number | null | undefined; verified?: boolean; size?: "lg" }) {
  const unproven = value == null || !verified;
  const cls = ["ui-score",
    unproven ? "ui-score--unverified" : value === 0 ? "ui-score--zero" : "",
    size === "lg" ? "ui-score--lg" : ""].filter(Boolean).join(" ");
  return <span className={cls}>{value == null ? "—" : value}</span>;
}

/* --------------------------------------------------------------- StatRow -- */
export function StatRow({ stats }: { stats: { label: string; value: ReactNode }[] }) {
  return (
    <div className="ui-statrow">
      {stats.map((s, i) => (
        <div className="ui-stat" key={i}>
          <span className="ui-stat-value">{s.value}</span>
          <span className="ui-stat-label">{s.label}</span>
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- Meter --- */
export function Meter({ pct, caption }: { pct: number; caption?: ReactNode }) {
  return (
    <div>
      <div className="ui-meter"><div className="ui-meter-fill" style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} /></div>
      {caption ? <div className="ui-meter-cap">{caption}</div> : null}
    </div>
  );
}

/* ----------------------------------------------------------- EmptyState --- */
/* Conversational, Claude-style: a headline, one body line, one CTA — words,
   not icon theatrics. Icon is opt-in only. */
export function EmptyState({ icon, title, body, action }:
  { icon?: ReactNode; title: ReactNode; body?: ReactNode;
    action?: { label: string; onClick: () => void; iconLeft?: ReactNode } }) {
  return (
    <div className="ui-empty">
      {icon ? <div className="ui-empty-icon">{icon}</div> : null}
      <div className="ui-empty-title">{title}</div>
      {body ? <div className="ui-empty-body">{body}</div> : null}
      {action ? (
        <div className="ui-empty-cta">
          <Button variant="primary" iconLeft={action.iconLeft} onClick={action.onClick}>{action.label}</Button>
        </div>
      ) : null}
    </div>
  );
}

/* --------------------------------------------------------------- Table ---- */
export function Table({ head, children, ariaLabel }:
  { head: ReactNode; children: ReactNode; ariaLabel?: string }) {
  return (
    <div className="ui-table-scroll">
      <table className="ui-table" aria-label={ariaLabel}>
        <thead><tr>{head}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------ Segmented --- */
export function Segmented<T extends string>({ items, value, onChange, ariaLabel }:
  { items: { id: T; label: ReactNode }[]; value: T; onChange: (id: T) => void; ariaLabel?: string }) {
  return (
    <div className="ui-seg" role="tablist" aria-label={ariaLabel}>
      {items.map((it) => (
        <button key={it.id} role="tab" aria-selected={value === it.id}
          className={value === it.id ? "ui-seg-item on" : "ui-seg-item"}
          onClick={() => onChange(it.id)}>{it.label}</button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------ Composer ---- */
const SendIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5" /><path d="m6 11 6-6 6 6" /></svg>
);
const SlidersIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 8h10M18 8h2M4 16h2M10 16h10" /><circle cx="16" cy="8" r="2" /><circle cx="8" cy="16" r="2" /></svg>
);

interface ComposerProps {
  value: string;
  onChange: (v: string) => void;
  onSubmit?: () => void;
  placeholder?: string;
  leftIcon?: ReactNode;
  onSliders?: () => void;
  slidersTitle?: string;
  submit?: { disabled?: boolean; busy?: boolean; icon?: ReactNode; title?: string };
  size?: "hero" | "compact";
  disabled?: boolean;
  ariaLabel?: string;
}

/**
 * The signature composer: a pill input with an embedded circular ink action
 * button (Send / Launch / arrow), an optional inline sliders trigger, and an
 * optional left icon. Enter submits. This is what makes the app read as an AI
 * product — reuse it for every primary text input.
 */
export function Composer({
  value, onChange, onSubmit, placeholder, leftIcon, onSliders, slidersTitle, submit, size, disabled, ariaLabel,
}: ComposerProps) {
  const cls = ["ui-composer", size === "hero" ? "ui-composer--hero" : "", size === "compact" ? "ui-composer--compact" : ""]
    .filter(Boolean).join(" ");
  return (
    <div className={cls}>
      {leftIcon ? <span className="ui-composer-left">{leftIcon}</span> : null}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && onSubmit) { e.preventDefault(); onSubmit(); } }}
        placeholder={placeholder}
        disabled={disabled}
        aria-label={ariaLabel}
      />
      {onSliders ? (
        <button type="button" className="ui-composer-sliders" onClick={onSliders}
          title={slidersTitle} aria-label={slidersTitle ?? "Options"}><SlidersIcon /></button>
      ) : null}
      {submit ? (
        <button type="button" className="ui-composer-submit" onClick={onSubmit}
          disabled={submit.disabled || submit.busy} title={submit.title} aria-label={submit.title ?? "Submit"}>
          {submit.icon ?? <SendIcon />}
        </button>
      ) : null}
    </div>
  );
}
