import { JournalFooter } from "../components/JournalFooter";
import { Column, EmptyState, Kicker, LabelledSection, Lede, Note, PanelTitle, Stat, Stats, Title } from "../components/Layout";
import { EntryCard } from "../components/EntryCard";
import { ExperimentTable } from "../components/ExperimentTable";
import { FollowersChart } from "../components/FollowersChart";
import type { Journal } from "../data";
import { day, int, pct, plural } from "../format";
import { paths } from "../router";

const RECENT_EXPERIMENTS = 3;

export function HomePage({ journal }: { journal: Journal }) {
  const { site, stats } = journal;
  const startLabel =
    stats.followers_start != null && site.started_on
      ? `followers, from ${int(stats.followers_start)} on ${day(site.started_on)}`
      : "followers";
  const experimentsLabel =
    stats.experiments_total === 0
      ? "experiments so far"
      : `${stats.experiments_total === 1 ? "experiment" : "experiments"}, ${int(stats.experiments_with_effect)} found an effect`;
  return (
    <Column label="Journal">
      <div className="flex flex-col gap-3">
        <Kicker>
          {site.started_on ? `Week ${site.week_number} of growing my account in public` : "Growing my account in public"}
        </Kicker>
        <Title>{site.headline}</Title>
        <Lede>{site.intro}</Lede>
        <Stats label="Totals so far">
          <Stat value={int(stats.followers_now)} label={startLabel} />
          <Stat value={int(stats.posts_scored)} label="of my posts scored against forecast" />
          <Stat value={pct(stats.hit_80)} label={stats.hit_80 == null ? "inside the 80% range, once posts are scored" : "inside the 80% range"} />
          <Stat value={int(stats.experiments_total)} label={experimentsLabel} />
        </Stats>
      </div>

      <FollowersChart followers={journal.followers} markers={journal.markers} />

      <section aria-labelledby="recent-experiments" className="flex flex-col">
        <PanelTitle title="My recent experiments" id="recent-experiments">
          {journal.experiments.length > 0 && (
            <Note>
              <a href={paths.experiments} className="hover:text-muted">
                {journal.experiments.length > RECENT_EXPERIMENTS ? `all ${journal.experiments.length} → · ` : "open one for the full write-up · "}
              </a>
              effect = outcome ÷ frozen forecast
            </Note>
          )}
        </PanelTitle>
        <ExperimentTable
          experiments={journal.experiments.slice(0, RECENT_EXPERIMENTS)}
          empty={<EmptyState>No experiments yet — the first one starts once I post daily.</EmptyState>}
        />
      </section>

      <LabelledSection label="Weekly notes">
        {journal.entries.length ? (
          journal.entries.map((e) => <EntryCard key={e.slug} entry={e} />)
        ) : (
          <EmptyState>
            No weekly notes yet. The first one goes up at the end of week {site.week_number}
            {stats.posts_scored ? `, with ${plural(stats.posts_scored, "scored post")} behind it` : ""}.
          </EmptyState>
        )}
      </LabelledSection>

      <JournalFooter journal={journal} />
    </Column>
  );
}
