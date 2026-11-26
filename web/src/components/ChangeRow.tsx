import { cx } from "../lib/cx";

export interface ChangeRowProps {
  date?: string;
  source?: string;
  text?: string;
  impact?: string;
  className?: string;
}

/** A dated change that can shift performance: X ranker parameter changes, account settings, model releases. */
export function ChangeRow({
  date = "Nov 18",
  source = "X ranker repo",
  text = "Copy-link share weight restored 0 → 20",
  impact = "change-point set",
  className,
}: ChangeRowProps) {
  return (
    <div role="row" className={cx("flex h-[34px] items-center font-sans", className ?? "w-[640px] shrink-0")}>
      <span role="cell" className="w-[72px] shrink-0 font-mono text-[11px] leading-normal whitespace-nowrap text-faint">
        {date}
      </span>
      <span role="cell" className="w-[120px] shrink-0 text-xs leading-normal whitespace-nowrap text-muted">
        {source}
      </span>
      <span role="cell" title={text} className="w-0 min-w-0 flex-1 truncate pr-4 text-xs leading-normal text-text">
        {text}
      </span>
      <span role="cell" className="w-[176px] shrink-0 truncate text-right font-mono text-xs leading-normal text-muted">
        {impact}
      </span>
    </div>
  );
}
