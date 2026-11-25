import { useState } from "react";
import { cx } from "../lib/cx";
import { Button } from "./Button";
import { icons } from "./icons";

export interface CollectionErrorBannerProps {
  title: string;
  detail: string;
  /** A terminal command that fixes it, shown with a copy button. */
  command?: string | null;
  /** Shown when there is no command, e.g. "Start run again". */
  actionLabel?: string;
  onAction?: () => void;
  onDismiss?: () => void;
  tone?: "negative" | "warn";
  className?: string;
}

const DismissIcon = icons.ActionClose;

/** Full-width banner under the top bar when a run stopped, saying why and what fixes it. */
export function CollectionErrorBanner({
  title,
  detail,
  command,
  actionLabel,
  onAction,
  onDismiss,
  tone = "negative",
  className,
}: CollectionErrorBannerProps) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    if (!command) return;
    void navigator.clipboard?.writeText(command).then(() => setCopied(true), () => setCopied(false));
  };
  return (
    <div
      role="alert"
      className={cx(
        "flex w-full shrink-0 items-center gap-3 border-b border-rule px-4 py-3 font-sans",
        tone === "negative" ? "bg-negative-subtle" : "bg-warn-subtle",
        className,
      )}
    >
      <span className={cx("block size-2 shrink-0 rounded-full", tone === "negative" ? "bg-negative" : "bg-warn")} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-[13px] leading-normal font-medium text-text">{title}</span>
        <span className="text-xs leading-[1.45] text-muted">{detail}</span>
      </div>
      {command ? (
        <>
          <code className="rounded-sm bg-surface px-2 py-1 font-mono text-xs leading-[1.33333] text-text">{command}</code>
          <Button label={copied ? "Copied" : "Copy command"} showIcon={false} variant="secondary" onClick={copy} />
        </>
      ) : (
        actionLabel && <Button label={actionLabel} showIcon={false} variant="secondary" onClick={onAction} />
      )}
      {onDismiss && (
        <button type="button" aria-label="Dismiss" onClick={onDismiss} className="size-4 shrink-0 text-faint hover:text-muted">
          <DismissIcon aria-hidden className="size-4" />
        </button>
      )}
    </div>
  );
}
