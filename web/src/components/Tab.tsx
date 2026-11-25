import { cx } from "../lib/cx";

export type TabCurrent = "default" | "active";

export interface TabProps {
  label?: string;
  count?: string;
  showCount?: boolean;
  current?: TabCurrent;
  onSelect?: () => void;
  className?: string;
}

/** Underlined tab with an optional count (Experiments: Running / Suggested / Concluded / All). */
export function Tab({ label = "Active", count = "4", showCount = true, current = "default", onSelect, className }: TabProps) {
  const active = current === "active";
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onSelect}
      className={cx(
        "flex items-center border-b-2 px-1 font-sans",
        active ? "border-text" : "border-transparent hover:border-rule-strong",
        className ?? "h-9 w-fit",
      )}
    >
      <span className={cx("text-[13px] leading-normal whitespace-nowrap", active ? "font-medium text-text" : "font-normal text-muted")}>
        {label}
      </span>
      {showCount && (
        <span className={cx("ml-1.5 font-mono text-[11px] leading-normal", active ? "text-muted" : "text-faint")}>{count}</span>
      )}
    </button>
  );
}
