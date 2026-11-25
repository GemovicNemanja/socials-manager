import { cx } from "../lib/cx";

export interface PostRowProps {
  text?: string;
  format?: string;
  topic?: string;
  /** Age at the latest capture, e.g. "21h" or "2.9d". */
  early?: string;
  /** Estimated 48h impressions, "≈" prefixed while provisional. */
  mature?: string;
  /** Likes per 1k impressions. */
  rate?: string;
  className?: string;
}

/**
 * The data cells of a post in a posts table: text, format, topic, age,
 * estimated 48h impressions and like rate. Sits in a row next to a
 * RangeStrip and a StatusPill.
 */
export function PostRow({ text = "", format = "—", topic = "—", early = "—", mature = "—", rate = "—", className }: PostRowProps) {
  return (
    <div className={cx("flex items-center font-sans", className ?? "h-[34px] w-[560px] shrink-0")}>
      <span
        role="cell"
        title={text}
        className="w-0 min-w-0 flex-1 overflow-hidden pr-4 text-xs leading-normal text-ellipsis whitespace-nowrap text-text"
      >
        {text}
      </span>
      <span role="cell" className="w-[88px] shrink-0 truncate text-xs leading-normal text-muted">
        {format}
      </span>
      <span role="cell" className="w-20 shrink-0 truncate text-xs leading-normal text-muted">
        {topic}
      </span>
      <span role="cell" className="w-12 shrink-0 text-right font-mono text-xs leading-normal text-muted">
        {early}
      </span>
      <span role="cell" className="w-14 shrink-0 text-right font-mono text-xs leading-normal text-text">
        {mature}
      </span>
      <span role="cell" className="w-[60px] shrink-0 text-right font-mono text-xs leading-normal text-muted">
        {rate}
      </span>
    </div>
  );
}
