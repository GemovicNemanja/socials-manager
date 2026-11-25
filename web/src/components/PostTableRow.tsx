import type { PostItem } from "../api/models";
import { cx } from "../lib/cx";
import { formatAge, formatDateTime, formatDecimal, formatI48, formatLabel } from "../lib/format";
import { POST_STATUS, rangeReading } from "../lib/status";
import { PostRow } from "./PostRow";
import { RangeStrip } from "./RangeStrip";
import { StatusPill } from "./StatusPill";

export interface PostTableRowProps {
  post: PostItem;
  tz: string;
  /** Highlights the row whose detail drawer is open. */
  selected?: boolean;
  /** Makes the row a button that opens the post. */
  onSelect?: (post: PostItem) => void;
}

/** A posts-table row: PostRow cells, the range strip and the status pill. */
export function PostTableRow({ post, tz, selected = false, onSelect }: PostTableRowProps) {
  const status = POST_STATUS[post.status];
  const reading = rangeReading(post);
  return (
    <div
      role="row"
      aria-selected={onSelect ? selected : undefined}
      tabIndex={onSelect ? 0 : undefined}
      title={`${post.id} · ${formatDateTime(post.created_at, tz)}`}
      onClick={onSelect ? () => onSelect(post) : undefined}
      onKeyDown={
        onSelect
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelect(post);
              }
            }
          : undefined
      }
      className={cx(
        "flex h-[34px] items-center border-b border-grid px-4",
        selected ? "bg-surface" : "hover:bg-raised",
        onSelect && "cursor-pointer",
      )}
    >
      <PostRow
        text={post.text}
        format={formatLabel(post.format)}
        topic={formatLabel(post.topic)}
        early={formatAge(post.age_h)}
        mature={formatI48(post.i48)}
        rate={formatDecimal(post.likes_per_1k)}
        className="min-w-0 flex-1"
      />
      <div role="cell" className="flex w-[92px] shrink-0 items-center pr-2 pl-4">
        {reading && <RangeStrip reading={reading} className="w-full" />}
      </div>
      <div role="cell" className="flex w-[92px] shrink-0 items-center">
        <StatusPill label={status.label} status={status.pill} />
      </div>
    </div>
  );
}
