import { cx } from "../lib/cx";

export interface ToggleProps {
  label?: string;
  checked?: boolean;
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
  className?: string;
}

/** Small on/off switch with a label. */
export function Toggle({ label = "Share with agents", checked = false, onChange, disabled, className }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange?.(!checked)}
      className={cx("flex items-center gap-2 font-sans disabled:opacity-60", className ?? "w-fit")}
    >
      <span
        className={cx(
          "relative block h-3.5 w-[26px] rounded-full border",
          checked ? "border-accent bg-accent-subtle" : "border-rule-strong bg-surface",
        )}
      >
        <span
          className={cx(
            "absolute top-0.5 block size-2 rounded-full transition-[left]",
            checked ? "left-3.5 bg-accent" : "left-0.5 bg-faint",
          )}
        />
      </span>
      <span className="text-xs leading-normal whitespace-nowrap text-muted">{label}</span>
    </button>
  );
}
