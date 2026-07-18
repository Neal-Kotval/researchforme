/**
 * motion — tiny, dependency-free entrance animations.
 *
 * Hand-rolled CSS-transition wrappers (no `motion`/framer dependency): fade a
 * view in when it mounts, reveal a section, or stagger a list. Everything honors
 * the OS "reduce motion" setting by rendering statically.
 *
 * Exports: <Mounted> (view crossfade, re-runs on key change), <Reveal>
 * (fade + rise on mount), <Stagger>/<StaggerItem> (a list that reveals in order).
 */
import {
  Children,
  cloneElement,
  isValidElement,
  useEffect,
  useState,
} from "react";
import type { CSSProperties, ElementType, ReactElement, ReactNode } from "react";

/** True when the viewer has asked the OS to minimize motion. */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
}

/** Fade + gentle rise, once, on mount — after an optional delay (seconds). */
function useEntrance(delay = 0): CSSProperties {
  const reduced = usePrefersReducedMotion();
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(id);
  }, []);
  if (reduced) return {};
  return {
    opacity: shown ? 1 : 0,
    transform: shown ? "none" : "translateY(8px)",
    transition: `opacity .32s ease ${delay}s, transform .34s ease ${delay}s`,
    willChange: "opacity, transform",
  };
}

/**
 * Polymorphic props for the reveal wrappers. `any`-valued passthrough keeps the
 * `as`-driven element spread simple; callers pass className/role/aria-*.
 */
type PolyProps = {
  as?: ElementType;
  className?: string;
  delay?: number;
  children?: ReactNode;
  [prop: string]: any;
};

/** Shared engine for <Reveal> and <StaggerItem>: render `as`, animate in. */
function FadeRise({ as = "div", className, delay = 0, children, ...rest }: PolyProps) {
  const style = useEntrance(delay);
  const Tag = as as ElementType;
  return (
    <Tag className={className} {...rest} style={{ ...rest.style, ...style }}>
      {children}
    </Tag>
  );
}

/**
 * Crossfade the mounted view. Pass a `k` that changes when the content should
 * re-animate (e.g. the active route) and the fade replays. Opacity only — no
 * transform — so it never becomes a containing block for `position:fixed` UI.
 */
export function Mounted({
  k,
  className,
  children,
}: {
  k?: string | number;
  className?: string;
  children: ReactNode;
}) {
  const reduced = usePrefersReducedMotion();
  const [shown, setShown] = useState(false);
  useEffect(() => {
    setShown(false);
    const id = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(id);
  }, [k]);
  const style: CSSProperties = reduced
    ? {}
    : { opacity: shown ? 1 : 0, transition: "opacity .3s ease" };
  return (
    <div className={className} style={style}>
      {children}
    </div>
  );
}

/** Fade + rise a block into view on mount. Accepts `as` and an optional `delay`. */
export function Reveal(props: PolyProps) {
  return <FadeRise {...props} />;
}

/**
 * Container whose direct children reveal in sequence. Each child's delay is its
 * index × `delay` (seconds); pair with <StaggerItem> for the per-item animation.
 */
export function Stagger({ className, delay = 0.05, children, ...rest }: PolyProps) {
  let i = 0;
  return (
    <div className={className} {...rest}>
      {Children.map(children, (child) =>
        isValidElement(child)
          ? cloneElement(child as ReactElement<{ _delay?: number }>, { _delay: i++ * delay })
          : child,
      )}
    </div>
  );
}

/** A single staggered child. Its reveal delay is injected by the parent <Stagger>. */
export function StaggerItem({ _delay = 0, delay: _ignore, ...rest }: PolyProps & { _delay?: number }) {
  return <FadeRise delay={_delay} {...rest} />;
}
