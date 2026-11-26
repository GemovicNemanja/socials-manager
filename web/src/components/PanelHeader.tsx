import type { CSSProperties } from "react";
import { cx } from "../lib/cx";

export interface PanelHeaderProps {
  title?: string;
  subtitle?: string;
  action?: string;
  showAction?: boolean;
  /** Link target for the action. */
  actionHref?: string;
  /** Click handler for the action; renders it as a button (opens a dialog) instead of a link. */
  onAction?: () => void;
  /** Horizontal padding; false when the panel already pads its content. */
  inset?: boolean;
  className?: string;
  style?: CSSProperties;
}

const ACTION = "text-xs leading-normal whitespace-nowrap text-accent hover:underline";

/** Title row of a dashboard panel with subtitle and optional link action. */
export function PanelHeader({
  title = "",
  subtitle,
  action,
  showAction = true,
  actionHref,
  onAction,
  inset = true,
  className,
  style,
}: PanelHeaderProps) {
  return (
    <div className={cx("flex h-10 shrink-0 items-center gap-3 font-sans", inset && "px-4", className ?? "w-full")} style={style}>
      <span className="text-[13px] leading-normal font-medium whitespace-nowrap text-text">{title}</span>
      <span className="min-w-0 flex-1 truncate text-xs leading-normal text-faint">{subtitle}</span>
      {showAction &&
        action &&
        (onAction ? (
          <button type="button" onClick={onAction} className={ACTION}>
            {action}
          </button>
        ) : (
          <a href={actionHref} className={ACTION}>
            {action}
          </a>
        ))}
    </div>
  );
}
