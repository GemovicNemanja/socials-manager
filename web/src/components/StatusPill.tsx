import { cx } from "../lib/cx";

export type StatusPillStatus = "default" | "working" | "not-working" | "tracking" | "insufficient";

export interface StatusPillProps {
  label?: string;
  status?: StatusPillStatus;
  className?: string;
}

const dotByStatus: Record<StatusPillStatus, string> = {
  default: "bg-warn",
  working: "bg-positive",
  "not-working": "bg-negative",
  tracking: "border border-faint bg-surface",
  insufficient: "bg-rule-strong",
};

const labelByStatus: Record<StatusPillStatus, string> = {
  default: "text-muted leading-normal",
  working: "text-text leading-[1.33333]",
  "not-working": "text-text leading-[1.33333]",
  tracking: "text-faint leading-[1.33333]",
  insufficient: "text-faint leading-[1.33333]",
};

/** Working / near threshold / not working / tracking / insufficient data, as a dot and label. */
export function StatusPill({ label = "Near", status = "default", className }: StatusPillProps) {
  return (
    <div className={cx("flex items-center gap-1.5 font-sans", className ?? "h-5 w-fit")}>
      <div className={cx("size-1.5 shrink-0 rounded-full", dotByStatus[status])} />
      <span className={cx("text-xs font-normal whitespace-nowrap", labelByStatus[status])}>{label}</span>
    </div>
  );
}
