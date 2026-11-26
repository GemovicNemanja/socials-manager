import { cx } from "../lib/cx";

export interface SectionLabelProps {
  label?: string;
  /** The element: a div in the dashboard, a heading where the label titles a page section. */
  as?: "div" | "h2" | "h3";
  className?: string;
}

/** Small uppercase label above a group (sidebar groups, form sections). */
export function SectionLabel({ label = "Monitor", as: Tag = "div", className }: SectionLabelProps) {
  return (
    <Tag className={cx("block px-2 pb-1 font-sans font-normal", className ?? "w-full shrink-0")}>
      <span className="text-[10px] leading-normal tracking-[0.8px] whitespace-nowrap text-faint uppercase">
        {label}
      </span>
    </Tag>
  );
}
