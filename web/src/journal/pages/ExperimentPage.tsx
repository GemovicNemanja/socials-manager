import { Badge } from "../../components/Badge";
import { ArmDotChart } from "../components/ArmDotChart";
import { ExperimentTable } from "../components/ExperimentTable";
import { BackLink, Column, Facts, Kicker, LabelledSection, Lede, ShareRow, Stat, Stats, Title } from "../components/Layout";
import type { Journal, JournalExperiment } from "../data";
import { findEntry, findExperiment } from "../data";
import { armLabel, int, isOngoing, pct, period, ratio, RESULT } from "../format";
import { Markdown } from "../markdown";
import { paths } from "../router";

const NOTE = "text-[15px] leading-[1.65] text-faint";

export function ExperimentPage({ journal, experiment: x }: { journal: Journal; experiment: JournalExperiment }) {
  const result = RESULT[x.result];
  const ongoing = isOngoing(x);
  const [control, treatment] = x.arms;
  const change = treatment ? armLabel(treatment.label) : "the change";
  const base = control ? armLabel(control.label) : "control";
  const ci = x.effect_ci80;
  const note = x.note_slug ? findEntry(journal, x.note_slug) : undefined;
  const related = x.related_keys.map((k) => findExperiment(journal, k)).filter((r): r is JournalExperiment => r != null);
  const when = x.started_at ? period(x.started_at.slice(0, 10), x.concluded_at?.slice(0, 10) ?? null) : "not started yet";
  return (
    <Column label={`${x.key} write-up`}>
      <article className="flex flex-col gap-10">
        <header className="flex flex-col gap-3">
          <BackLink href={paths.experiments} label="All experiments" />
          <div className="flex flex-wrap items-center gap-3">
            <Kicker tone="faint">
              {x.key} · {when}
            </Kicker>
            <Badge label={result.label} tone={result.tone} />
          </div>
          <Title>{x.question}</Title>
          {x.answer && <Lede>{x.answer}</Lede>}
          <Stats label="Result" className="pt-3">
            <Stat
              value={ratio(x.effect_ratio)}
              label={
                x.effect_ratio == null
                  ? "no estimate yet"
                  : `reach with ${change} vs ${base}${ci ? `, 80%: ${ci[0].toFixed(2)}–${ci[1].toFixed(2)}` : ""}${ongoing ? " (interim)" : ""}`
              }
            />
            <Stat
              value={int(x.posts_matured)}
              label={
                ongoing
                  ? `posts matured of ${int(x.posts_planned)} assigned`
                  : x.arms.length
                    ? `posts, ${x.arms.map((a) => `${a.n} ${armLabel(a.label)}`).join(" / ")}`
                    : "posts"
              }
            />
            <Stat value={pct(x.p_better)} label={x.p_better == null ? "chance it does better, once estimated" : `chance ${change} does better`} />
          </Stats>
        </header>

        <LabelledSection label="Why I tested it" className="gap-3">
          {x.why_markdown ? <Markdown source={x.why_markdown} /> : <p className={NOTE}>Not written up yet.</p>}
        </LabelledSection>

        <ArmDotChart experiment={x} />

        <LabelledSection label="How it was set up, written before the first post">
          <Facts rows={x.setup} label="Setup" />
        </LabelledSection>

        <LabelledSection label="What I changed" className="gap-3">
          {x.changed_markdown ? (
            <Markdown source={x.changed_markdown} />
          ) : (
            <p className={NOTE}>{ongoing ? "Written once the experiment concludes." : "Not written up yet."}</p>
          )}
          <Facts
            label="How sure"
            rows={[
              {
                label: "Randomization test",
                value: x.randomization_p != null ? `p = ${x.randomization_p.toFixed(2)}` : "Not run yet",
              },
              {
                label: "Counted as confirmed",
                value: x.confirmed
                  ? "Yes, within my running error budget"
                  : ongoing
                    ? "Not yet: it's still running"
                    : "No",
              },
              {
                label: "Weekly note",
                value: note ? (
                  <a href={paths.entry(note.slug)} className="text-accent hover:underline">
                    Week {note.week_number} · {note.title}
                  </a>
                ) : (
                  "None yet"
                ),
              },
            ]}
          />
        </LabelledSection>

        {related.length > 0 && (
          <LabelledSection label="Related experiments">
            <ExperimentTable experiments={related} showHead={false} />
          </LabelledSection>
        )}

        <ShareRow journal={journal} />
      </article>
    </Column>
  );
}
