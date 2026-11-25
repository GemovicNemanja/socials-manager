import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePosts, useShell } from "../api/hooks";
import type { PostItem, PostsStatusFilter } from "../api/models";
import { Button } from "../components/Button";
import { ColumnHeader } from "../components/ColumnHeader";
import { FilterChip } from "../components/FilterChip";
import { icons } from "../components/icons";
import { PostTableRow } from "../components/PostTableRow";
import { SearchField } from "../components/SearchField";
import { cx } from "../lib/cx";
import { downloadText, postsToCsv } from "../lib/csv";
import { formatDateTime, formatInt } from "../lib/format";
import { navigate, useRouteSub } from "../lib/router";
import { STATUS_FILTERS } from "../lib/status";
import { PostDrawer } from "./PostDrawer";

const LATER = "Arrives in M1";
const FilterIcon = icons.ActionFilter;
const CheckIcon = icons.ActionCheck;

/** Display-only chips in M0: their filters land with the M1 Posts screen. */
const DISPLAY_CHIPS: [string, string][] = [
  ["Format", "All"],
  ["Topic", "All"],
];
const DISPLAY_CHIPS_AFTER: [string, string][] = [
  ["Metric", "Impressions"],
  ["Age", "48h"],
  ["Experiment", "Any"],
];

function matches(post: PostItem, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [post.text, post.id, post.external_id, post.format, post.topic]
    .some((field) => field?.toLowerCase().includes(q));
}

export function PostsPage() {
  const [status, setStatus] = useState<PostsStatusFilter>("all");
  const [search, setSearch] = useState("");

  const shell = useShell();
  const posts = usePosts(status);
  const selected = useRouteSub();
  const selectedId = selected ? `post:${selected.replace(/^post:/, "")}` : null;
  const closeDrawer = useCallback(() => navigate("posts"), []);

  const tz = shell.data?.account?.tz ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
  const firstPage = posts.data?.pages[0];
  const total = firstPage?.total;
  const loaded = useMemo(() => posts.data?.pages.flatMap((page) => page.items) ?? [], [posts.data]);
  const visible = useMemo(() => loaded.filter((post) => matches(post, search)), [loaded, search]);
  const caveats = firstPage
    ? [firstPage.data_sufficiency.reason, ...firstPage.caveats].filter((c): c is string => !!c)
    : [];

  const counts = firstPage?.counts_by_status;
  const context = shell.data
    ? [
        `${formatInt(shell.data.counts.posts)} tracked`,
        counts ? `${formatInt(counts.above + counts.as_expected + counts.below)} matured` : null,
        counts ? `${formatInt(counts.provisional)} provisional (under 48h)` : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "";

  const resultCount =
    total == null
      ? ""
      : search.trim()
        ? `${formatInt(visible.length)} of ${formatInt(loaded.length)} loaded · newest first`
        : `${formatInt(total)} posts · newest first`;

  const exportCsv = () => {
    const stamp = new Date().toISOString().slice(0, 10);
    downloadText(`posts-${status}-${stamp}.csv`, postsToCsv(visible));
  };

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {/* Top bar */}
      <header className="flex h-11 w-full shrink-0 items-center gap-3 border-b border-rule px-4 font-sans">
        <h1 className="text-sm leading-normal font-medium text-text">Posts</h1>
        <span
          className="text-xs leading-normal whitespace-nowrap text-faint"
          title={firstPage?.as_of ? `As of ${formatDateTime(firstPage.as_of, tz)}` : undefined}
        >
          {context}
        </span>
        <div className="flex-1" />
        <SearchField placeholder="Filter by text, label or id" value={search} onChange={setSearch} />
        <Button label="Columns" icon="ActionColumns" disabled title={LATER} />
        <Button
          label="CSV"
          icon="ActionDownload"
          onClick={exportCsv}
          disabled={visible.length === 0}
          title="Download the loaded rows as CSV"
        />
      </header>

      {/* Filter bar */}
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-rule px-4">
        <FilterIcon aria-hidden className="size-[13px] shrink-0 text-faint" />
        {DISPLAY_CHIPS.map(([key, value]) => (
          <FilterChip key={key} filterKey={key} filterValue={value} disabled title={LATER} />
        ))}
        <StatusFilter value={status} onChange={setStatus} />
        {DISPLAY_CHIPS_AFTER.map(([key, value]) => (
          <FilterChip key={key} filterKey={key} filterValue={value} disabled title={LATER} />
        ))}
        <div className="flex-1" />
        <span className="font-mono text-[11px] leading-normal whitespace-nowrap text-faint">{resultCount}</span>
      </div>

      <div className="flex min-h-0 min-w-0 flex-1">
        {/* Posts table */}
        <div role="table" aria-label="Posts" className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div role="row" className="flex h-7 shrink-0 items-center border-b border-rule bg-raised px-4">
            <div className="flex min-w-0 flex-1 items-center">
              <ColumnHeader label="Post" className="min-w-0 flex-1" />
              <ColumnHeader label="Format" className="w-[88px]" />
              <ColumnHeader label="Topic" className="w-20" />
              <ColumnHeader label="Age" align="right" className="w-12" />
              <ColumnHeader label="48h est." align="right" className="w-14" />
              <ColumnHeader label="Likes/1k" align="right" className="w-[60px]" />
            </div>
            <ColumnHeader label="vs expected" className="w-[92px] pl-4" />
            <ColumnHeader label="Status" className="w-[92px]" />
          </div>

          <div role="rowgroup" className={cx("min-h-0 flex-1 overflow-y-auto", posts.isPlaceholderData && "opacity-60")}>
            {posts.isPending ? (
              <TableMessage>Loading posts…</TableMessage>
            ) : posts.isError ? (
              <TableMessage
                action={<Button label="Retry" showIcon={false} onClick={() => void posts.refetch()} />}
              >
                Can’t reach the API ({posts.error.message}). Is <code className="font-mono text-muted">socialmgr serve</code>{" "}
                running?
              </TableMessage>
            ) : visible.length === 0 ? (
              <TableMessage>
                {search.trim()
                  ? "No loaded posts match the filter."
                  : status === "all"
                    ? "No posts yet. Run socialmgr run to collect them."
                    : `No posts with status ${STATUS_FILTERS.find((s) => s.value === status)?.label ?? status}.`}
              </TableMessage>
            ) : (
              <>
                {visible.map((post) => (
                  <PostTableRow
                    key={post.id}
                    post={post}
                    tz={tz}
                    selected={post.id === selectedId}
                    onSelect={(p) => navigate("posts", p.id.replace(/^post:/, ""))}
                  />
                ))}
                {posts.hasNextPage && (
                  <div className="flex h-[34px] items-center justify-center border-b border-grid">
                    <Button
                      variant="ghost"
                      showIcon={false}
                      label={posts.isFetchingNextPage ? "Loading…" : `Load ${formatInt(Math.min(50, (total ?? 0) - loaded.length))} more`}
                      disabled={posts.isFetchingNextPage}
                      onClick={() => void posts.fetchNextPage()}
                    />
                  </div>
                )}
                {caveats.length > 0 && (
                  <ul className="flex flex-col gap-0.5 px-4 py-3 text-[11px] leading-normal text-faint">
                    {caveats.map((c) => (
                      <li key={c}>{c}</li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        </div>
        {selected && <PostDrawer key={selected} postId={selected} tz={tz} onClose={closeDrawer} />}
      </div>
    </div>
  );
}

function TableMessage({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-4 py-16 text-center">
      <p className="text-xs leading-normal text-faint">{children}</p>
      {action}
    </div>
  );
}

/** The one live filter in M0: maps to ?status= on /api/posts. */
function StatusFilter({
  value,
  onChange,
}: {
  value: PostsStatusFilter;
  onChange: (value: PostsStatusFilter) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const current = STATUS_FILTERS.find((s) => s.value === value)?.label ?? "All";

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={root} className="relative">
      <FilterChip
        filterKey="Status"
        filterValue={current}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      />
      {open && (
        <ul
          role="listbox"
          aria-label="Status"
          className="absolute top-full left-0 z-10 mt-1 min-w-36 rounded-sm border border-rule bg-raised py-1"
        >
          {STATUS_FILTERS.map((option) => {
            const selected = option.value === value;
            return (
              <li key={option.value} role="option" aria-selected={selected}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(option.value);
                    setOpen(false);
                  }}
                  className="flex h-7 w-full items-center gap-2 px-2 text-left text-xs text-text hover:bg-surface"
                >
                  <CheckIcon aria-hidden className={cx("size-3 shrink-0 text-accent", !selected && "invisible")} />
                  {option.label}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
