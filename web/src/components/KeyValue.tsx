import type { ReactNode } from "react";
import { cx } from "../lib/cx";

export interface KeyValueProps {
  /** The key ("key2" on the canvas, where `key` is reserved). */
  label?: string;
  value?: ReactNode;
  /** Monospaced value, for numbers and ids. */
  mono?: boolean;
  className?: string;
}

/** Key and value on one 26px row with a grid rule below (protocols, capacity, levers). */
export function KeyValue({ label = "Primary metric", value = "Impressions at 48h", mono = false, className }: KeyValueProps) {
  return (
    <div className={cx("flex h-[26px] shrink-0 items-center border-b border-grid font-sans", className ?? "w-[320px]")}>
      <span className="w-[140px] shrink-0 text-xs leading-normal whitespace-nowrap text-faint">{label}</span>
      <span className={cx("min-w-0 flex-1 truncate text-xs leading-normal text-text", mono && "font-mono")}>{value}</span>
    </div>
  );
}
