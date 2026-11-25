import { cx } from "../lib/cx";

export type MetricRowTone = "default" | "above" | "below";

export interface MetricRowProps {
  metric?: string;
  actual?: string;
  expected?: string;
  percentile?: string;
  tone?: MetricRowTone;
  className?: string;
}

const percentileByTone: Record<MetricRowTone, string> = {
  default: "text-text",
  above: "text-positive",
  below: "text-negative",
};

/** One metric against its expected 80% range, with its percentile. */
export function MetricRow({
  metric = "Impressions",
  actual = "—",
  expected = "—",
  percentile = "—",
  tone = "default",
  className,
}: MetricRowProps) {
  return (
    <div className={cx("flex h-[26px] shrink-0 items-center border-b border-grid font-sans", className ?? "w-full")}>
      <span className="w-0 min-w-0 flex-1 truncate pr-2 text-xs leading-[1.33333] text-muted">{metric}</span>
      <span className="w-16 text-right font-mono text-xs leading-[1.33333] text-text">{actual}</span>
      <span className="w-[110px] text-right font-mono text-xs leading-[1.33333] text-faint">{expected}</span>
      <span className={cx("w-12 text-right font-mono text-xs leading-[1.33333]", percentileByTone[tone])}>{percentile}</span>
    </div>
  );
}
