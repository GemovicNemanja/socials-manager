import { cx } from "../lib/cx";
import { swatchBg, type Swatch } from "./swatch";

export interface MeterProps {
  label?: string;
  value?: string;
  /** Filled share, 0..1. */
  fraction?: number;
  fill?: Swatch;
  className?: string;
}

/** Labelled thin meter: name and value above a 3px bar. */
export function Meter({ label = "80% range hit rate", value = "78% n=63", fraction = 0.5, fill = "neutral", className }: MeterProps) {
  const pct = Math.min(100, Math.max(0, fraction * 100));
  return (
    <div className={cx("flex flex-col gap-[5px] font-sans", className ?? "w-[360px] shrink-0")}>
      <div className="flex justify-between gap-2">
        <span className="text-xs leading-[1.33333] whitespace-nowrap text-muted">{label}</span>
        <span className="font-mono text-xs leading-[1.33333] whitespace-pre text-text">{value}</span>
      </div>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        className="relative h-[3px] w-full bg-surface"
      >
        <div className={cx("absolute top-0 left-0 h-[3px]", swatchBg[fill])} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
