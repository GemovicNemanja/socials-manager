import type { ReactNode } from "react";
import { cx } from "../lib/cx";

export interface KeyValueProps {
  /** The key ("key2" on the canvas, where `key` is reserved). */
  label?: string;
  value?: ReactNode;
  /** Monospaced value, for numbers and ids. */
  mono?: boolean;
  /** Let a long key or value wrap onto more lines instead of truncating (public pages). */
  wrap?: boolean;
  className?: string;
}

/** Key and value on one 26px row with a grid rule below (protocols, capacity, levers). */
export function KeyValue({ label = "Primary metric", value = "Impressions at 48h", mono = false, wrap = false, className }: KeyValueProps) {
  return (
    <div
      className={cx(
        "flex shrink-0 border-b border-grid font-sans",
        wrap ? "min-h-[26px] items-baseline py-1" : "h-[26px] items-center",
        className ?? "w-[320px]",
      )}
    >
      <span className={cx("w-[140px] shrink-0 text-xs leading-normal text-faint", wrap ? "pr-3" : "whitespace-nowrap")}>{label}</span>
      <span className={cx("min-w-0 flex-1 text-xs leading-normal text-text", !wrap && "truncate", mono && "font-mono")}>{value}</span>
    </div>
  );
}
