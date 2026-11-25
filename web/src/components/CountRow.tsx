import { cx } from "../lib/cx";
import { swatchBg, type Swatch } from "./swatch";

export interface CountRowProps {
  swatch?: Swatch;
  name?: string;
  definition?: string;
  value?: string;
  className?: string;
}

/** Legend row with swatch, name, definition and count (post status breakdowns). */
export function CountRow({
  swatch = "positive",
  name = "Working",
  definition = "outcome above p80 of its forecast",
  value = "19",
  className,
}: CountRowProps) {
  return (
    <div className={cx("flex items-center gap-2 border-b border-grid font-sans", className ?? "h-[26px] w-[368px] shrink-0")}>
      <div className={cx("size-2 shrink-0 rounded-xs", swatchBg[swatch])} />
      <span className="w-[104px] shrink-0 text-xs leading-normal whitespace-nowrap text-text">{name}</span>
      <span className="min-w-0 flex-1 truncate text-[11px] leading-normal text-faint">{definition}</span>
      <span className="font-mono text-xs leading-normal text-text">{value}</span>
    </div>
  );
}
