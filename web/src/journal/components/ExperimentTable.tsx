import type { ReactNode } from "react";
import { Badge } from "../../components/Badge";
import { ColumnHeader } from "../../components/ColumnHeader";
import { ExperimentRow } from "../../components/ExperimentRow";
import { SectionLabel } from "../../components/SectionLabel";
import type { JournalExperiment } from "../data";
import { effectCell, RESULT } from "../format";
import { paths } from "../router";

export function ExperimentTableHead({ effectLabel = "Effect" }: { effectLabel?: string }) {
  return (
    <div aria-hidden className="flex h-7 items-center border-y border-rule bg-raised px-3">
      <ColumnHeader label="ID" className="w-[72px]" />
      <ColumnHeader label="Experiment" className="flex-1" />
      <ColumnHeader label="Design" className="w-[132px]" />
      <ColumnHeader label="Posts" align="right" className="w-[84px]" />
      <ColumnHeader label={effectLabel} align="right" className="w-[150px]" />
      <ColumnHeader label="Result" className="w-[112px] pl-5" />
    </div>
  );
}

/** One experiment as a table row that opens its write-up. */
export function ExperimentTableRow({ experiment: x }: { experiment: JournalExperiment }) {
  const result = RESULT[x.result];
  return (
    <a
      href={paths.experiment(x.slug)}
      className="flex h-[34px] items-center border-b border-grid px-3 hover:bg-raised"
    >
      <ExperimentRow
        id={x.key}
        name={x.name}
        design={x.design_label}
        progress={x.progress_label}
        effect={effectCell(x)}
        className="min-w-0 flex-1"
      />
      <div className="flex w-[112px] shrink-0 pl-5">
        <Badge label={result.label} tone={result.tone} />
      </div>
    </a>
  );
}

export interface ExperimentTableProps {
  experiments: JournalExperiment[];
  effectLabel?: string;
  /** Group label rows (Running / Concluded) for the full list. */
  groups?: { label: string; experiments: JournalExperiment[] }[];
  /** Shown instead of rows when there are none. */
  empty?: ReactNode;
  showHead?: boolean;
}

/** The experiments table from the canvas: id, name, design, posts, effect [80%] and result. */
export function ExperimentTable({ experiments, effectLabel, groups, empty, showHead = true }: ExperimentTableProps) {
  const sets = groups ?? [{ label: "", experiments }];
  const none = sets.every((g) => g.experiments.length === 0);
  return (
    <div className="overflow-x-auto">
      <div className="min-w-[640px]">
        {showHead && !none && <ExperimentTableHead effectLabel={effectLabel} />}
        {sets
          .filter((g) => g.experiments.length > 0)
          .map((g) => (
            <div key={g.label || "all"} role={g.label ? "group" : undefined} aria-label={g.label || undefined}>
              {g.label && <SectionLabel label={g.label} className="w-full pt-3" />}
              {g.experiments.map((x) => (
                <ExperimentTableRow key={x.key} experiment={x} />
              ))}
            </div>
          ))}
      </div>
      {none && empty}
    </div>
  );
}
