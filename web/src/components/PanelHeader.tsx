import type { CSSProperties } from "react";
import { cx } from "../lib/cx";

export interface PanelHeaderProps {
  title?: string;
  subtitle?: string;
  action?: string;
  showAction?: boolean;
  /** Link target for the action. */
  actionHref?: string;
  className?: string;
  style?: CSSProperties;
}

/** Title row of a dashboard panel with subtitle and optional link action. */
export function PanelHeader({
  title = "",
  subtitle,
  action,
  showAction = true,
  actionHref,
  className,
  style,
}: PanelHeaderProps) {
  return (
    <div className={cx("flex h-10 shrink-0 items-center gap-3 px-4 font-sans", className ?? "w-full")} style={style}>
      <span className="text-[13px] leading-normal font-medium whitespace-nowrap text-text">{title}</span>
      <span className="min-w-0 flex-1 truncate text-xs leading-normal text-faint">{subtitle}</span>
      {showAction && action && (
        <a href={actionHref} className="text-xs leading-normal whitespace-nowrap text-accent hover:underline">
          {action}
        </a>
      )}
    </div>
  );
}
