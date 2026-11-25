import { cx } from "../lib/cx";
import { icons, type NavIconName } from "./icons";

export type NavItemCurrent = "default" | "active";

export interface NavItemProps {
  label?: string;
  icon?: NavIconName;
  count?: string;
  showCount?: boolean;
  current?: NavItemCurrent;
  /** Link target (hash route). */
  href?: string;
  className?: string;
}

/** Sidebar navigation row with icon, label and optional count. */
export function NavItem({
  label = "Overview",
  icon = "NavOverview",
  count,
  showCount = true,
  current = "default",
  href,
  className,
}: NavItemProps) {
  const Icon = icons[icon];
  const active = current === "active";
  return (
    <a
      href={href}
      aria-current={active ? "page" : undefined}
      className={cx(
        "flex items-center gap-2 rounded-sm px-2 font-sans",
        active ? "bg-surface text-text" : "text-muted hover:bg-raised",
        className ?? "h-7 w-full shrink-0",
      )}
    >
      <Icon aria-hidden className="size-3.5 shrink-0" />
      <span
        className={cx(
          "flex-1 truncate text-[13px] leading-normal",
          active ? "font-medium text-text" : "font-normal text-muted",
        )}
      >
        {label}
      </span>
      {showCount && count != null && (
        <span className={cx("font-mono text-[11px] leading-normal", active ? "text-muted" : "text-faint")}>
          {count}
        </span>
      )}
    </a>
  );
}
