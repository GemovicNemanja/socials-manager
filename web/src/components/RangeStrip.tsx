import { cx } from "../lib/cx";

export type RangeStripReading = "default" | "above" | "high" | "low" | "below" | "live";

export interface RangeStripProps {
  reading?: RangeStripReading;
  className?: string;
}

/** Tick position and colour per reading, as on the canvas main. */
const tickByReading: Record<RangeStripReading, string> = {
  default: "left-[52%] bg-text",
  low: "left-[32%] bg-text",
  high: "left-[70%] bg-text",
  below: "left-[10%] bg-negative",
  above: "left-[90%] bg-positive",
  live: "left-[60%] bg-faint",
};

const label: Record<RangeStripReading, string> = {
  default: "Inside the expected 80% range",
  low: "Low in the expected range",
  high: "High in the expected range",
  below: "Below the expected range",
  above: "Above the expected range",
  live: "Still tracking (under 48h)",
};

/**
 * The expected 80% range (band), its median and where the observed value
 * landed. The tick sits at a fixed spot per percentile band (reading).
 */
export function RangeStrip({ reading = "default", className }: RangeStripProps) {
  const live = reading === "live";
  return (
    <div role="img" aria-label={label[reading]} className={cx("relative h-2", className ?? "w-[120px] shrink-0")}>
      <div className="absolute top-[3px] right-0 left-0 h-0.5 bg-rule" />
      <div className={cx("absolute top-0.5 left-[20%] h-1 w-[60%]", live ? "bg-heat-2" : "bg-band")} />
      <div className={cx("absolute top-px left-1/2 h-1.5 w-px", live ? "bg-faint" : "bg-accent")} />
      <div className={cx("absolute top-0 h-2 w-0.5", tickByReading[reading])} />
    </div>
  );
}
