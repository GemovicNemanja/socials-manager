import { cx } from "../lib/cx";

export interface ExperimentRowProps {
  id?: string;
  name?: string;
  design?: string;
  progress?: string;
  effect?: string;
  className?: string;
}

/** The cells of one experiments-table row: id, name, design, progress and effect [80%]. */
export function ExperimentRow({
  id = "EXP-012",
  name = "Narrated vs silent demos",
  design = "Per-post, day-blocked",
  progress = "41 / 84",
  effect = "1.38× [1.02, 1.86]",
  className,
}: ExperimentRowProps) {
  return (
    <div className={cx("flex h-[34px] items-center font-sans", className ?? "w-[640px] shrink-0")}>
      <span className="w-[72px] shrink-0 font-mono text-[11px] leading-normal whitespace-nowrap text-faint">{id}</span>
      <span className="w-0 min-w-0 flex-1 truncate pr-4 text-xs leading-normal text-text">{name}</span>
      <span className="w-[132px] shrink-0 text-xs leading-normal whitespace-nowrap text-muted">{design}</span>
      <span className="w-[84px] shrink-0 text-right font-mono text-xs leading-normal whitespace-nowrap text-muted">{progress}</span>
      <span className="w-[150px] shrink-0 text-right font-mono text-xs leading-normal whitespace-nowrap text-text">{effect}</span>
    </div>
  );
}
