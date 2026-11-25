import { type ReactNode, useEffect, useId } from "react";
import { cx } from "../lib/cx";
import { icons } from "./icons";

const CloseIcon = icons.ActionClose;
const ChevronIcon = icons.ActionExpand;

export interface DialogFrameProps {
  title: string;
  subtitle?: ReactNode;
  onClose: () => void;
  footer: ReactNode;
  children: ReactNode;
  /** Tailwind width class; defaults to the canvas dialog's 560px. */
  width?: string;
}

/** Modal dialog on the raised surface: header with title and close, scrolling body, footer actions. */
export function DialogFrame({ title, subtitle, onClose, footer, children, width = "w-[560px]" }: DialogFrameProps) {
  const titleId = useId();
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ground/70 p-6" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(e) => e.stopPropagation()}
        className={cx("flex max-h-full flex-col overflow-hidden rounded-lg border border-rule-strong bg-raised font-sans", width)}
      >
        <div className="flex items-start gap-3 border-b border-rule px-5 pt-5 pb-4">
          <div className="flex flex-1 flex-col gap-1">
            <h2 id={titleId} className="text-base leading-normal font-semibold text-text">
              {title}
            </h2>
            {subtitle && <p className="text-xs leading-[1.45] text-muted">{subtitle}</p>}
          </div>
          <button type="button" aria-label="Close" onClick={onClose} className="mt-0.5 size-4 shrink-0 text-faint hover:text-muted">
            <CloseIcon aria-hidden className="size-4" />
          </button>
        </div>
        <div className="flex flex-col gap-4 overflow-y-auto px-5 py-4">{children}</div>
        <div className="flex items-center justify-end gap-2 border-t border-rule px-5 py-3.5">{footer}</div>
      </div>
    </div>
  );
}

/** A labelled form field; the label points at the control through the id it hands to children. */
export function Field({ label, children }: { label: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs leading-[1.33333] text-muted">
        {label}
      </label>
      {children(id)}
    </div>
  );
}

/** A native select in the dialog field style. */
export function SelectField({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0 flex-1">
      <Field label={label}>
        {(id) => (
          <div className="relative flex h-8 items-center rounded-sm border border-rule bg-ground focus-within:border-accent">
            <select
              id={id}
              value={value}
              onChange={(e) => onChange(e.target.value)}
              className="h-full w-full appearance-none bg-transparent pr-7 pl-2.5 text-[13px] leading-normal text-text outline-none [color-scheme:dark]"
            >
              {children}
            </select>
            <ChevronIcon aria-hidden className="pointer-events-none absolute right-2.5 size-3.5 text-faint" />
          </div>
        )}
      </Field>
    </div>
  );
}

/** A warning or error line inside a dialog or panel. */
export function Notice({ tone, children }: { tone: "warn" | "negative"; children: ReactNode }) {
  return (
    <div
      role={tone === "negative" ? "alert" : "status"}
      className={cx("flex items-start gap-2 rounded-sm p-2.5", tone === "warn" ? "bg-warn-subtle" : "bg-negative-subtle")}
    >
      <span className={cx("mt-1.5 block size-1.5 shrink-0 rounded-full", tone === "warn" ? "bg-warn" : "bg-negative")} />
      <span className="text-xs leading-[1.45] text-text">{children}</span>
    </div>
  );
}
