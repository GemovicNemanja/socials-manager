import type { CSSProperties } from "react";
import { cx } from "../lib/cx";

export type ColumnHeaderAlign = "default" | "right";

export interface ColumnHeaderProps {
  label?: string;
  align?: ColumnHeaderAlign;
  /** Width and padding of the column; defaults to w-24. */
  className?: string;
  style?: CSSProperties;
}

/** Table column heading on the raised header row. */
export function ColumnHeader({ label = "Format", align = "default", className, style }: ColumnHeaderProps) {
  return (
    <div
      role="columnheader"
      className={cx("flex h-7 shrink-0 items-center font-sans", className ?? "w-24")}
      style={style}
    >
      <span
        className={cx(
          "flex-1 text-[11px] leading-normal font-normal whitespace-nowrap text-faint",
          align === "right" ? "text-right" : "text-left",
        )}
      >
        {label}
      </span>
    </div>
  );
}
