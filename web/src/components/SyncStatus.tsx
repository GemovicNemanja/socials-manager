import { cx } from "../lib/cx";

export type SyncStatusTone = "positive" | "warn" | "negative" | "faint";

export interface SyncStatusProps {
  syncText?: string;
  spendLabel?: string;
  spendValue?: string;
  /** Share of the monthly cap spent, 0..1; sets the meter width. */
  spendFraction?: number;
  /** Dot colour: positive for a finished run, warn while running, negative on failure. */
  syncTone?: SyncStatusTone;
  className?: string;
}

const dotByTone: Record<SyncStatusTone, string> = {
  positive: "bg-positive",
  warn: "bg-warn",
  negative: "bg-negative",
  faint: "bg-rule-strong",
};

/** Sidebar footer: last run and monthly data spend against the cap. */
export function SyncStatus({
  syncText = "No run yet",
  spendLabel = "X API spend",
  spendValue = "—",
  spendFraction = 0,
  syncTone = "positive",
  className,
}: SyncStatusProps) {
  const pct = Math.min(100, Math.max(0, spendFraction * 100));
  return (
    <div
      className={cx(
        "flex flex-col gap-2 border-t border-rule bg-ground p-3 font-sans",
        className ?? "w-[216px] shrink-0",
      )}
    >
      <div className="flex items-center gap-2">
        <div className={cx("size-1.5 shrink-0 rounded-full", dotByTone[syncTone])} />
        <span className="text-[11px] leading-normal text-muted">{syncText}</span>
      </div>
      <div className="flex flex-col gap-1">
        <div className="flex justify-between gap-2">
          <span className="text-[11px] leading-normal text-faint">{spendLabel}</span>
          <span className="font-mono text-[11px] leading-normal text-muted">{spendValue}</span>
        </div>
        <div
          className="h-[3px] w-full rounded-full bg-surface"
          role="meter"
          aria-label={spendLabel}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pct)}
        >
          <div
            className={cx("h-[3px] rounded-full", pct >= 100 ? "bg-negative" : "bg-neutral")}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    </div>
  );
}
