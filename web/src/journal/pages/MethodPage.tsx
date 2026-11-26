import { Meter } from "../../components/Meter";
import { SectionLabel } from "../../components/SectionLabel";
import { Column, Facts, Kicker, Lede, Title } from "../components/Layout";
import type { Journal, JournalMethodSection } from "../data";
import { pct } from "../format";

/** A coverage meter: neutral when within 5 points of its target, warn when further off. */
function Coverage({ row }: { row: JournalMethodSection["coverage"][number] }) {
  const off = row.value != null && Math.abs(row.value - row.target) > 0.05;
  return (
    <Meter
      label={row.label}
      value={row.value == null ? "not enough posts yet" : pct(row.value)}
      fraction={row.value ?? 0}
      target={row.target}
      fill={off ? "warn" : "neutral"}
      className="w-full"
    />
  );
}

export function MethodPage({ journal }: { journal: Journal }) {
  const m = journal.method;
  return (
    <Column label="Method">
      <div className="flex flex-col gap-3">
        <Kicker>Method</Kicker>
        <Title>{m.headline}</Title>
        <Lede>{m.intro}</Lede>
      </div>
      {m.sections.map((s) => (
        <section key={s.number} aria-label={s.title} className="flex flex-col gap-2">
          <SectionLabel as="h2" label={`${s.number} · ${s.title}`} />
          <p className="text-[13px] leading-[1.6] text-muted">{s.body}</p>
          {s.coverage.length > 0 && (
            <div role="group" aria-label="Forecast coverage" className="flex flex-col gap-[7px] pt-1">
              {s.coverage.map((c) => (
                <Coverage key={c.label} row={c} />
              ))}
              <p className="text-[11px] leading-normal text-faint">The small tick on each bar marks its target.</p>
            </div>
          )}
          {s.facts.length > 0 && (
            <div className="pt-1">
              <Facts rows={s.facts} label={`${s.title}: facts`} />
            </div>
          )}
        </section>
      ))}
    </Column>
  );
}
