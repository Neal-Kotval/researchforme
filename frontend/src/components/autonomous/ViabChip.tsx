import type { CSSProperties } from "react";
import { viabilityRamp, type TrustLevel } from "../../autonomous/types";

interface Props {
  value: number | null;
  trust: TrustLevel;
  star?: boolean;
  title?: string;
}

/**
 * The compact viability chip, trust-encoded (memo §2): earned trust gets the
 * solid ramp fill; provisional gets an outlined numeral; unverified gets a
 * dashed, desaturated, smaller chip. The ramp color rides a CSS custom
 * property so index.css owns each treatment.
 */
export default function ViabChip({ value, trust, star, title }: Props) {
  const ramp = viabilityRamp(value);
  const style: CSSProperties =
    trust === "earned"
      ? { background: ramp }
      : ({ "--chip-ramp": ramp } as CSSProperties);
  return (
    <span className={`viab-chip trust-${trust}${star ? " star" : ""}`} style={style} title={title}>
      {star && <span className="vc-star">★</span>}
      {value ?? "—"}
    </span>
  );
}
