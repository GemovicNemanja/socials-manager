import { cx } from "../lib/cx";
import { icons } from "./icons";

export interface AccountSwitcherProps {
  initials?: string;
  handle?: string;
  platform?: string;
  className?: string;
}

/** Top of the sidebar: the tracked account and its platform. */
export function AccountSwitcher({
  initials = "YA",
  handle = "@your_account",
  platform = "X · local install",
  className,
}: AccountSwitcherProps) {
  const SwitchIcon = icons.ActionSwitch;
  return (
    <div
      className={cx(
        "flex items-center gap-2 border-b border-rule bg-ground px-3 font-sans",
        className ?? "h-11 w-[216px] shrink-0",
      )}
    >
      <div className="flex size-[22px] shrink-0 items-center justify-center rounded-sm border border-rule-strong bg-surface">
        <span className="text-[10px] leading-normal font-semibold text-text">{initials}</span>
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-xs leading-[1.16667] font-medium text-text">{handle}</span>
        <span className="truncate text-[10px] leading-[1.2] text-faint">{platform}</span>
      </div>
      <SwitchIcon aria-hidden className="size-3.5 shrink-0 text-faint" />
    </div>
  );
}
