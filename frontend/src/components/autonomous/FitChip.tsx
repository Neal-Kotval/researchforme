/**
 * The compact founder-fit chip (memo §1: vermillion is the founder, blue is
 * the market). Rendered ONLY when a real fit score exists — a null fit means
 * "no steering / not scored" and must show nothing, never a fake 0.
 * Mirrors ViabChip's compact form so the two read as a pair.
 */
export const FIT_HELP =
  "Founder fit — how attackable this space is for YOU, from your steering. " +
  "Orthogonal to viability.";

interface Props {
  value: number | null;
  title?: string;
  /** Show the tiny "fit" unit label inside the chip (hero cards). */
  labeled?: boolean;
}

export default function FitChip({ value, title, labeled = false }: Props) {
  if (value == null) return null;
  return (
    <span className="fit-chip" title={title ?? FIT_HELP}>
      {value}
      {labeled && <small>fit</small>}
    </span>
  );
}
