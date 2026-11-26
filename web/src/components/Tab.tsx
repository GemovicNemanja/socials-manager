import { cx } from "../lib/cx";

export type TabCurrent = "default" | "active";

export interface TabProps {
  label?: string;
  count?: string;
  showCount?: boolean;
  current?: TabCurrent;
  onSelect?: () => void;
  /** Render a navigation link instead of a tab button (the public journal's top bar). */
  href?: string;
  className?: string;
}

/** Underlined tab with an optional count (Experiments: Running / Suggested / Concluded / All). */
export function Tab({ label = "Active", count = "4", showCount = true, current = "default", onSelect, href, className }: TabProps) {
  const active = current === "active";
  const classes = cx(
    "flex items-center border-b-2 px-1 font-sans",
    active ? "border-text" : "border-transparent hover:border-rule-strong",
    className ?? "h-9 w-fit",
  );
  const content = (
    <>
      <span className={cx("text-[13px] leading-normal whitespace-nowrap", active ? "font-medium text-text" : "font-normal text-muted")}>
        {label}
      </span>
      {showCount && (
        <span className={cx("ml-1.5 font-mono text-[11px] leading-normal", active ? "text-muted" : "text-faint")}>{count}</span>
      )}
    </>
  );
  if (href != null) {
    return (
      <a href={href} aria-current={active ? "page" : undefined} className={classes}>
        {content}
      </a>
    );
  }
  return (
    <button type="button" role="tab" aria-selected={active} onClick={onSelect} className={classes}>
      {content}
    </button>
  );
}
