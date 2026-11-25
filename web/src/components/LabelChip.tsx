import { cx } from "../lib/cx";

export interface LabelChipProps {
  label?: string;
  /** Where the label came from: rule, LLM 0.93, owner, provider, assigned. */
  source?: string;
  className?: string;
}

/** A post label with its source, outlined. */
export function LabelChip({ label = "Demo", source = "rule", className }: LabelChipProps) {
  return (
    <div className={cx("flex h-[22px] w-fit items-center gap-[5px] rounded-[3px] border border-rule px-1.5 font-sans", className)}>
      <span className="text-[11px] leading-normal whitespace-nowrap text-text">{label}</span>
      {source && <span className="font-mono text-[10px] leading-normal whitespace-nowrap text-faint">{source}</span>}
    </div>
  );
}
