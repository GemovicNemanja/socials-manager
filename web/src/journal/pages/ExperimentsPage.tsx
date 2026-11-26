import { EffectsForest } from "../components/EffectsForest";
import { ExperimentTable } from "../components/ExperimentTable";
import { JournalFooter } from "../components/JournalFooter";
import { Column, EmptyState, Facts, Kicker, LabelledSection, Lede, Note, PanelTitle, Stat, Stats, Title } from "../components/Layout";
import type { Journal, JournalExperiment } from "../data";
import { effect, int, isOngoing, plural } from "../format";
import { paths } from "../router";

function headline(all: JournalExperiment[]): string {
  if (all.length === 0) return "No experiments yet";
  const concluded = all.filter((x) => !isOngoing(x));
  const found = all.filter((x) => x.result === "effect_found").length;
  const lead = `${plural(all.length, "experiment")} so far`;
  if (concluded.length === 0) return `${lead}, none finished yet`;
  if (found === 0) return `${lead}, no effect found yet`;
  return `${lead}, ${found} found an effect`;
}

/** The latest concluded experiment with a result, written up in short. */
function FeaturedReadout({ experiment: x }: { experiment: JournalExperiment }) {
  const per = x.arms.map((a) => `${a.n} ${a.label.replace(/_/g, " ")}`).join(", ");
  const rows = [
    { label: "Posts", value: `${int(x.posts_matured)}${per ? `, ${per}` : ""}` },
    { label: "Outcome", value: `${x.metric_label.charAt(0).toUpperCase()}${x.metric_label.slice(1)} ÷ frozen forecast` },
    { label: "Effect", value: x.effect_ratio != null ? `${effect(x)}, 80% interval` : "—" },
    ...(x.randomization_p != null ? [{ label: "Randomization test", value: `p = ${x.randomization_p.toFixed(2)}` }] : []),
    {
      label: "Status",
      value: x.confirmed ? "Confirmed under the running error budget" : x.result === "stopped" ? "Stopped early" : "Not confirmed",
    },
  ];
  return (
    <LabelledSection label={`Featured readout · ${x.key}`} className="gap-3">
      <h3 className="text-lg leading-[1.33333] font-medium text-text">
        <a href={paths.experiment(x.slug)} className="hover:underline">
          {x.question}
        </a>
      </h3>
      {x.answer && <p className="text-[13px] leading-[1.6] text-muted">{x.answer}</p>}
      <Facts rows={rows} label={`${x.key} in short`} />
    </LabelledSection>
  );
}

export function ExperimentsPage({ journal }: { journal: Journal }) {
  const all = journal.experiments;
  const running = all.filter(isOngoing);
  const concluded = all.filter((x) => !isOngoing(x));
  const featured = concluded.find((x) => x.result !== "stopped" && x.effect_ratio != null);
  const count = (r: JournalExperiment["result"]) => all.filter((x) => x.result === r).length;
  const withEstimate = all.filter((x) => x.effect_ratio != null);
  return (
    <Column label="Experiments">
      <div className="flex flex-col gap-3">
        <Kicker>
          {all.length ? <>Every experiment I&rsquo;ve run, including the ones that found nothing</> : "Experiments"}
        </Kicker>
        <Title>{headline(all)}</Title>
        <Lede>
          I write each one down before the first post is assigned. A coin flip decides which of my posts get the
          change, and I compare every post with its own forecast, so a good topic week can&rsquo;t pass for a good
          idea.
        </Lede>
        <Stats label="Experiments by result">
          <Stat value={int(count("running"))} label="running now" />
          <Stat value={int(count("effect_found"))} label="found an effect" />
          <Stat value={int(count("no_effect"))} label="no effect" />
          <Stat value={int(count("stopped"))} label="stopped early" />
        </Stats>
      </div>

      {withEstimate.length > 0 && <EffectsForest experiments={withEstimate} />}

      <section aria-labelledby="all-experiments" className="flex flex-col">
        <PanelTitle title="All experiments" id="all-experiments">
          {all.length > 0 && <Note>Open any experiment for its full write-up · effect = outcome ÷ frozen forecast</Note>}
        </PanelTitle>
        <ExperimentTable
          experiments={all}
          effectLabel="Effect [80%]"
          groups={[
            { label: "Running", experiments: running },
            { label: "Concluded", experiments: concluded },
          ]}
          empty={
            <EmptyState>
              No experiments yet — the first one starts once I post daily. Each one is written down here before its first
              post{journal.stats.posts_scored ? `; ${plural(journal.stats.posts_scored, "post")} scored so far` : ""}.
            </EmptyState>
          }
        />
        {running.length > 0 && concluded.length === 0 && (
          <p className="pt-3 text-xs leading-[1.6] text-faint">
            Effects shown for running experiments are interim looks; a result counts only once an experiment
            concludes.
          </p>
        )}
      </section>

      {featured && <FeaturedReadout experiment={featured} />}

      <JournalFooter journal={journal} />
    </Column>
  );
}
