import { cx } from "../lib/cx";

/** "none" is a code-only reading for a KPI without a forecast yet: the track alone. */
export type ExpectationTileReading = "default" | "above" | "below" | "plan" | "none";

export interface ExpectationTileProps {
  label?: string;
  value?: string;
  delta?: string;
  percentile?: string;
  range?: string;
  reading?: ExpectationTileReading;
  /**
   * Where the actual sits on the strip, 0..1; the band spans 0.2–0.8 (the 80% range).
   * Defaults to the canvas tick spot for the reading.
   */
  position?: number;
  className?: string;
}

const tickByReading: Record<ExpectationTileReading, { left: number; colour: string } | null> = {
  default: { left: 0.58, colour: "bg-text" },
  above: { left: 0.88, colour: "bg-positive" },
  below: { left: 0.15, colour: "bg-negative" },
  plan: { left: 0.9, colour: "bg-text" },
  none: null,
};

const percentileColour: Record<ExpectationTileReading, string> = {
  default: "text-text",
  above: "text-positive",
  below: "text-negative",
  plan: "text-text",
  none: "text-faint",
};

/** A KPI against its expected range: value, change, strip, percentile and range text. */
export function ExpectationTile({
  label = "Followers",
  value = "412",
  delta = "+61 in 7d",
  percentile = "p58",
  range = "exp. +38 to +74",
  reading = "default",
  position,
  className,
}: ExpectationTileProps) {
  const tick = tickByReading[reading];
  const left = position ?? tick?.left ?? 0;
  const pct = `${Math.min(98, Math.max(1, left * 100))}%`;
  const banded = reading !== "plan" && reading !== "none";
  return (
    <div className={cx("flex flex-col gap-1.5 border-r border-rule px-4 py-3 font-sans", className ?? "w-[204px] shrink-0")}>
      <span className="text-xs leading-normal whitespace-nowrap text-muted">{label}</span>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[22px] leading-[1.18182] text-text">{value}</span>
        <span className="truncate text-[11px] leading-normal text-faint">{delta}</span>
      </div>
      <div className="relative h-2 w-full" aria-hidden>
        <div className="absolute top-[3px] right-0 left-0 h-0.5 bg-surface" />
        {banded && <div className="absolute top-0.5 left-[20%] h-1 w-[60%] bg-band" />}
        {banded && <div className="absolute top-px left-1/2 h-1.5 w-px bg-accent" />}
        {reading === "plan" && <div className="absolute top-[3px] left-0 h-0.5 bg-neutral" style={{ width: pct }} />}
        {tick && <div className={cx("absolute top-0 h-2 w-0.5", tick.colour)} style={{ left: pct }} />}
      </div>
      <div className="flex min-w-0 gap-1.5">
        <span className={cx("font-mono text-[11px] leading-normal", percentileColour[reading])}>{percentile}</span>
        <span className="truncate text-[11px] leading-normal text-faint">{range}</span>
      </div>
    </div>
  );
}
