import type { ReactNode } from "react";
import { cx } from "../lib/cx";

export interface SuggestionCardProps {
  author?: string;
  lever?: string;
  title?: string;
  hypothesis?: string;
  metric?: string;
  power?: string;
  /** Extra rows under Detectable (e.g. the live three-number power preview). */
  children?: ReactNode;
  className?: string;
}

/** A proposed experiment: source, lever, title, hypothesis, metric and detectability. */
export function SuggestionCard({
  author = "Claude Code · 12m ago",
  lever = "reply strategy",
  title = "Reply to 5k–50k peers instead of mega accounts",
  hypothesis = "",
  metric = "follows per reply, 48h",
  power = "2× in 4 weeks, 80%",
  children,
  className,
}: SuggestionCardProps) {
  return (
    <div className={cx("flex flex-col gap-2 rounded-sm border border-rule bg-raised p-3 font-sans", className ?? "w-[380px] shrink-0")}>
      <div className="flex items-center gap-2">
        <span className="text-[11px] leading-normal whitespace-nowrap text-faint">{author}</span>
        <span className="ml-auto font-mono text-[11px] leading-normal whitespace-nowrap text-muted">{lever}</span>
      </div>
      <span className="text-[13px] leading-normal font-medium text-text">{title}</span>
      <span className="text-xs leading-[1.41667] text-muted">{hypothesis}</span>
      <div className="flex flex-col gap-0.5 border-t border-grid pt-1">
        <div className="flex justify-between gap-3">
          <span className="text-[11px] leading-normal text-faint">Primary metric</span>
          <span className="text-right text-[11px] leading-normal text-text">{metric}</span>
        </div>
        <div className="flex justify-between gap-3">
          <span className="text-[11px] leading-normal text-faint">Detectable</span>
          <span className="text-right font-mono text-[11px] leading-normal text-text">{power}</span>
        </div>
        {children}
      </div>
    </div>
  );
}
