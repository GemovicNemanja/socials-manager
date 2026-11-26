import { ColumnHeader } from "../../components/ColumnHeader";
import { PostRow } from "../../components/PostRow";
import { RangeStrip, type RangeStripReading } from "../../components/RangeStrip";
import { StatusPill } from "../../components/StatusPill";
import { formatDecimal, formatInt, formatLabel } from "../../lib/format";
import { POST_STATUS } from "../../lib/status";
import type { JournalPost } from "../data";

const READING: Record<JournalPost["outcome"], RangeStripReading> = {
  above: "above",
  as_expected: "default",
  below: "below",
  provisional: "live",
};

/** Where a value sits on the strip: the 80% range spans 0.2..0.8, on a log scale like the forecast. */
function stripAt(post: JournalPost, value: number | null): number | undefined {
  const { q10, q90 } = post;
  if (value == null || q10 == null || q90 == null || q90 <= q10) return undefined;
  const f = (Math.log1p(value) - Math.log1p(q10)) / (Math.log1p(q90) - Math.log1p(q10));
  return Math.min(0.98, Math.max(0.01, 0.2 + 0.6 * f));
}

function rangeLabel(post: JournalPost): string {
  if (post.q10 == null || post.q90 == null) return "No frozen forecast";
  const range = `forecast ${formatInt(post.q10)}–${formatInt(post.q90)} (median ${formatInt(post.q50)})`;
  return post.i48 == null ? range : `${formatInt(post.i48)} at 48h; ${range}`;
}

export function PostTable({ posts }: { posts: JournalPost[] }) {
  return (
    <div className="overflow-x-auto">
      <div className="min-w-[700px]">
        <div aria-hidden className="flex h-7 items-center border-y border-rule bg-raised px-3">
          <div className="flex min-w-0 flex-1 items-center">
            <ColumnHeader label="Post" className="flex-1" />
            <ColumnHeader label="Format" className="w-[88px]" />
            <ColumnHeader label="Topic" className="w-20" />
            <ColumnHeader label="2h" align="right" className="w-12" />
            <ColumnHeader label="48h" align="right" className="w-14" />
            <ColumnHeader label="Likes/1k" align="right" className="w-[60px]" />
          </div>
          <ColumnHeader label="vs forecast" className="w-[100px] pl-4" />
          <ColumnHeader label="Status" className="w-24" />
        </div>
        <ul aria-label="Posts against their forecast">
          {posts.map((p, i) => {
            const status = POST_STATUS[p.outcome];
            const position = stripAt(p, p.i48);
            const row = (
              <>
                <PostRow
                  text={p.text}
                  format={formatLabel(p.format)}
                  topic={formatLabel(p.topic)}
                  early={formatInt(p.i2h)}
                  mature={formatInt(p.i48)}
                  rate={formatDecimal(p.likes_per_1k)}
                  className="h-[34px] min-w-0 flex-1"
                />
                <div className="flex w-[100px] shrink-0 items-center pr-2 pl-4" title={rangeLabel(p)}>
                  {p.q10 != null && p.q90 != null ? (
                    <RangeStrip
                      reading={READING[p.outcome]}
                      position={position}
                      median={stripAt(p, p.q50)}
                      className="w-full"
                    />
                  ) : (
                    <span className="text-xs text-faint">—</span>
                  )}
                </div>
                <div className="flex w-24 shrink-0 items-center">
                  <StatusPill label={status.label} status={status.pill} />
                </div>
              </>
            );
            const classes = "flex h-[34px] items-center border-b border-grid px-3";
            return (
              <li key={`${i}-${p.text}`}>
                {p.url ? (
                  <a href={p.url} target="_blank" rel="noopener noreferrer" className={`${classes} hover:bg-raised`}>
                    {row}
                  </a>
                ) : (
                  <div className={classes}>{row}</div>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
