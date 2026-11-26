import { Badge } from "../../components/Badge";
import { SectionLabel } from "../../components/SectionLabel";
import { DRAFT_LABEL, EntryCard } from "../components/EntryCard";
import { ExperimentTable } from "../components/ExperimentTable";
import { BackLink, Column, EmptyState, Kicker, LabelledSection, Note, PanelTitle, ShareRow, Stat, Stats, Title } from "../components/Layout";
import { PostTable } from "../components/PostTable";
import type { Journal, JournalEntry, JournalExperiment } from "../data";
import { findExperiment } from "../data";
import { compact, int, period, plural, signed } from "../format";
import { Markdown } from "../markdown";
import { paths } from "../router";

function followersLabel(entry: JournalEntry): string {
  const range = entry.stats.followers_expected;
  if (!range) return "followers this week";
  return `followers, forecast ${signed(range[0])} to ${signed(range[1])}`;
}

function impressionsLabel(entry: JournalEntry): string {
  const change = entry.stats.impressions_change;
  if (change == null) return "impressions this week";
  const p = Math.round(change * 100);
  return `impressions, ${p > 0 ? "+" : p < 0 ? "−" : "±"}${Math.abs(p)}% on week ${entry.week_number - 1}`;
}

export function EntryPage({ journal, entry }: { journal: Journal; entry: JournalEntry }) {
  const s = entry.stats;
  const index = journal.entries.findIndex((e) => e.slug === entry.slug);
  const previous = journal.entries[index + 1];
  const mentioned = entry.experiment_keys
    .map((k) => findExperiment(journal, k))
    .filter((x): x is JournalExperiment => x != null);
  return (
    <Column label={`Week ${entry.week_number} note`}>
      <article className="flex flex-col gap-10">
        <header className="flex flex-col gap-3">
          <BackLink href={paths.home} label="All weekly notes" />
          <div className="flex flex-wrap items-center gap-3">
            <Kicker tone="faint">
              Week {entry.week_number} · {period(entry.period_start, entry.period_end)}
            </Kicker>
            {entry.draft && <Badge label={DRAFT_LABEL} tone="warn" />}
          </div>
          <Title>{entry.title}</Title>
          <Stats label="This week" className="pt-3">
            <Stat value={signed(s.followers_delta)} label={followersLabel(entry)} />
            <Stat value={compact(s.impressions)} label={impressionsLabel(entry)} />
            <Stat
              value={s.posts_scored ? `${int(s.posts_in_80)} / ${int(s.posts_scored)}` : "—"}
              label={s.posts_scored ? "posts inside their 80% range" : "no posts scored this week yet"}
            />
          </Stats>
        </header>

        <section aria-label="Note text">
          {entry.body_markdown.trim() ? (
            <Markdown source={entry.body_markdown} />
          ) : (
            <p className="text-[15px] text-faint">This note has no text yet.</p>
          )}
        </section>

        <section aria-labelledby="entry-posts" className="flex flex-col">
          <PanelTitle title="My posts this week against their forecast" id="entry-posts">
            {entry.posts.length > 0 && (
              <Note>
                {entry.posts.length < entry.posts_total
                  ? `top ${entry.posts.length} of ${entry.posts_total}, by 48h impressions`
                  : `${plural(entry.posts_total, "post")}, by 48h impressions`}
              </Note>
            )}
          </PanelTitle>
          {entry.posts.length ? <PostTable posts={entry.posts} /> : <EmptyState>No posts went out this week.</EmptyState>}
        </section>

        {entry.experiment_keys.length > 0 && (
          <LabelledSection label="Experiments mentioned">
            <ExperimentTable
              experiments={mentioned}
              showHead={false}
              empty={<EmptyState>{entry.experiment_keys.join(", ")} isn't public yet.</EmptyState>}
            />
          </LabelledSection>
        )}

        <nav aria-label="More notes" className="flex flex-col gap-2 pt-2">
          {previous && (
            <>
              <SectionLabel as="h2" label="Previous note" />
              <EntryCard entry={previous} />
            </>
          )}
          <ShareRow journal={journal} />
        </nav>
      </article>
    </Column>
  );
}
