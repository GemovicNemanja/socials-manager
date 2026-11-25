import type { ButtonHTMLAttributes } from "react";
import { cx } from "../lib/cx";
import { icons } from "./icons";

export interface FilterChipProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  filterKey?: string;
  filterValue?: string;
}

/** A filter key and its current value; opens a menu. */
export function FilterChip({
  filterKey = "Format",
  filterValue = "All",
  className,
  type = "button",
  ...rest
}: FilterChipProps) {
  const ExpandIcon = icons.ActionExpand;
  return (
    <button
      type={type}
      className={cx(
        "flex items-center gap-[5px] rounded-sm border border-rule bg-raised px-2 font-sans enabled:hover:border-rule-strong disabled:cursor-default",
        className ?? "h-6 w-fit",
      )}
      {...rest}
    >
      <span className="text-[11px] leading-normal whitespace-nowrap text-faint">{filterKey}</span>
      <span className="text-xs leading-normal whitespace-nowrap text-text">{filterValue}</span>
      <ExpandIcon aria-hidden className="size-3 shrink-0 text-faint" />
    </button>
  );
}
