import { cx } from "../lib/cx";

export interface SectionLabelProps {
  label?: string;
  className?: string;
}

/** Small uppercase label above a group (sidebar groups, form sections). */
export function SectionLabel({ label = "Monitor", className }: SectionLabelProps) {
  return (
    <div className={cx("block px-2 pb-1 font-sans", className ?? "w-full shrink-0")}>
      <span className="text-[10px] leading-normal tracking-[0.8px] whitespace-nowrap text-faint uppercase">
        {label}
      </span>
    </div>
  );
}
