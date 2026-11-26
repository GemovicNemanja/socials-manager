import { Badge } from "../../components/Badge";
import type { JournalEntry } from "../data";
import { compact, dayYear, pct, period, signed } from "../format";
import { paths } from "../router";

export const DRAFT_LABEL = "Draft — not public";

/** The three numbers under a weekly note in a list: "+58 followers  39.1k impressions  81% hit rate". */
function entryStats(entry: JournalEntry): string[] {
  const s = entry.stats;
  const out: string[] = [];
  if (s.followers_delta != null) out.push(`${signed(s.followers_delta)} followers`);
  if (s.impressions != null) out.push(`${compact(s.impressions)} impressions`);
  if (s.hit_80 != null) out.push(`${pct(s.hit_80)} forecast hit rate`);
  return out;
}

/** A weekly note in a list (home page, "Previous note"): date, title, summary and its numbers. */
export function EntryCard({ entry }: { entry: JournalEntry }) {
  // A published note carries its publish date; a draft its week.
  const date = entry.published_at ? dayYear(entry.published_at) : period(entry.period_start, entry.period_end);
  const stats = entryStats(entry);
  return (
    <article className="flex w-full flex-col gap-2 border-b border-rule py-5">
      <div className="flex items-center gap-3">
        <span className="font-mono text-[11px] leading-normal text-faint">
          Week {entry.week_number} · {date}
        </span>
        {entry.draft && <Badge label={DRAFT_LABEL} tone="warn" />}
      </div>
      <h3 className="text-lg leading-[1.33333] font-medium text-text">
        <a href={paths.entry(entry.slug)} className="hover:underline">
          {entry.title}
        </a>
      </h3>
      {entry.summary && <p className="text-[13px] leading-[1.53846] text-muted">{entry.summary}</p>}
      {stats.length > 0 && (
        <div className="flex flex-wrap gap-x-6 pt-1">
          {stats.map((s) => (
            <span key={s} className="font-mono text-xs leading-normal text-text">
              {s}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}
