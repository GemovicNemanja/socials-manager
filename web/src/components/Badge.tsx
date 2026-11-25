import { cx } from "../lib/cx";

export type BadgeTone = "default" | "warn" | "positive" | "negative" | "accent";

export interface BadgeProps {
  label?: string;
  tone?: BadgeTone;
  className?: string;
}

const rootByTone: Record<BadgeTone, string> = {
  default: "bg-surface",
  warn: "bg-warn-subtle",
  positive: "bg-positive-subtle",
  negative: "bg-negative-subtle",
  accent: "bg-accent-subtle",
};

const textByTone: Record<BadgeTone, string> = {
  default: "text-muted",
  warn: "text-warn",
  positive: "text-positive",
  negative: "text-negative",
  accent: "text-accent",
};

/** Small tinted tag for counts and states such as Needs approval or Draft. */
export function Badge({ label = "Draft", tone = "default", className }: BadgeProps) {
  return (
    <div className={cx("flex items-center rounded-[3px] px-1.5 font-sans", rootByTone[tone], className ?? "h-[18px] w-fit")}>
      <span className={cx("text-[10px] leading-normal font-normal whitespace-nowrap", textByTone[tone])}>{label}</span>
    </div>
  );
}
