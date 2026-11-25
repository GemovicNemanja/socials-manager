import type { ButtonHTMLAttributes } from "react";
import { cx } from "../lib/cx";
import { icons, type IconName } from "./icons";

export type ButtonVariant = "default" | "secondary" | "primary" | "ghost";

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  label?: string;
  icon?: IconName;
  showIcon?: boolean;
  variant?: ButtonVariant;
}

const rootByVariant: Record<ButtonVariant, string> = {
  default: "border-rule px-3 text-muted hover:bg-raised disabled:hover:bg-transparent",
  secondary: "border-rule px-3 text-muted hover:bg-raised disabled:hover:bg-transparent",
  primary: "border-text bg-text px-3 text-ground hover:bg-neutral disabled:hover:bg-text",
  ghost: "border-transparent px-2 text-accent hover:bg-accent-subtle disabled:hover:bg-transparent",
};

const labelByVariant: Record<ButtonVariant, string> = {
  default: "text-muted",
  secondary: "text-muted",
  primary: "text-ground",
  ghost: "text-accent",
};

/** Toolbar button. Primary for the one main action per screen, Secondary for the rest. */
export function Button({
  label = "Button",
  icon = "ActionAdd",
  showIcon = true,
  variant = "default",
  className,
  type = "button",
  ...rest
}: ButtonProps) {
  const Icon = icons[icon];
  return (
    <button
      type={type}
      className={cx(
        "flex items-center gap-1.5 rounded-sm border font-sans disabled:cursor-default disabled:opacity-60",
        rootByVariant[variant],
        className ?? "h-7 w-fit",
      )}
      {...rest}
    >
      {showIcon && <Icon aria-hidden className="size-[13px] shrink-0" />}
      <span className={cx("text-xs leading-normal font-medium whitespace-nowrap", labelByVariant[variant])}>
        {label}
      </span>
    </button>
  );
}
