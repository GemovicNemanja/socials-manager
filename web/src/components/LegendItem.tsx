import { cx } from "../lib/cx";
import { swatchBg, type Swatch } from "./swatch";

export interface LegendItemProps {
  label?: string;
  swatch?: Swatch;
  className?: string;
}

/** Chart legend entry: a short line in the series colour and its name. */
export function LegendItem({ label = "Actual", swatch = "neutral", className }: LegendItemProps) {
  return (
    <div className={cx("flex items-center gap-1.5 font-sans", className ?? "w-fit")}>
      <div className={cx("h-0.5 w-3", swatchBg[swatch])} />
      <span className="text-[11px] leading-normal whitespace-nowrap text-muted">{label}</span>
    </div>
  );
}
