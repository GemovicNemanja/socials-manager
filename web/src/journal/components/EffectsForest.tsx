import { LegendItem } from "../../components/LegendItem";
import { cx } from "../../lib/cx";
import type { JournalExperiment } from "../data";
import { effect, isOngoing } from "../format";
import { paths } from "../router";
import { PanelTitle } from "./Layout";

const ROW = 30;

/** Steps of √2 around 1×: 0.5×, 0.71×, 1×, 1.41×, 2×, widened (in whole doublings) to fit every interval. */
function axis(experiments: JournalExperiment[]): { lo: number; hi: number; ticks: number[] } {
  const values = experiments.flatMap((x) => [x.effect_ratio!, ...(x.effect_ci80 ?? [])]).filter((v) => v > 0);
  const lo = Math.min(-1, Math.floor(Math.log2(Math.min(...values, 1))));
  const hi = Math.max(1, Math.ceil(Math.log2(Math.max(...values, 1))));
  const ticks: number[] = [];
  for (let k = lo * 2; k <= hi * 2; k++) ticks.push(2 ** (k / 2));
  return { lo, hi, ticks };
}

function tickLabel(t: number): string {
  if (Math.abs(t - 1) < 1e-9) return "1× no change";
  return `${t >= 10 ? t.toFixed(0) : Number(t.toFixed(2))}×`;
}

/**
 * "Effects at a glance": every experiment with an estimate, as its effect (outcome ÷ frozen forecast,
 * treated vs control) and 80% interval on a log scale. Confirmed effects are green; experiments still
 * running are drawn faint, because their interval is an interim look.
 */
export function EffectsForest({ experiments }: { experiments: JournalExperiment[] }) {
  const rows = experiments.filter((x) => x.effect_ratio != null);
  if (rows.length === 0) return null;
  const { lo, hi, ticks } = axis(rows);
  const pos = (v: number) => ((Math.log2(v) - lo) / (hi - lo)) * 100;
  const ongoing = rows.some(isOngoing);
  const anyConfirmed = rows.some((x) => x.confirmed);
  return (
    <section aria-labelledby="forest-title" className="flex flex-col gap-2">
      <PanelTitle title={ongoing ? "Effects at a glance, including interim looks" : "Effects at a glance"} id="forest-title">
        <div className="flex items-center gap-4">
          <LegendItem label="Estimate and 80% interval" swatch="neutral" />
          {ongoing && <LegendItem label="Still running" swatch="faint" />}
          {anyConfirmed && <LegendItem label="Confirmed" swatch="positive" />}
        </div>
      </PanelTitle>
      <div className="flex gap-3">
        <div className="flex w-[220px] shrink-0 flex-col">
          {rows.map((x) => (
            <a
              key={x.key}
              href={paths.experiment(x.slug)}
              className="truncate text-xs leading-[30px] text-muted hover:text-text"
              style={{ height: ROW }}
              title={x.name}
            >
              {x.key} · {x.name}
            </a>
          ))}
        </div>
        <div
          role="img"
          aria-label={rows.map((x) => `${x.key} ${effect(x)}${x.confirmed ? " confirmed" : isOngoing(x) ? " running" : ""}`).join("; ")}
          className="relative flex-1 border border-grid bg-plot"
          style={{ height: rows.length * ROW }}
        >
          {ticks.map((t) => (
            <div
              key={t}
              className={cx("absolute inset-y-0 w-0", Math.abs(t - 1) < 1e-9 ? "border-l border-dashed border-faint" : "border-l border-grid")}
              style={{ left: `${pos(t)}%` }}
            />
          ))}
          {rows.map((x, i) => {
            const tone = x.confirmed ? "bg-positive" : isOngoing(x) ? "bg-faint" : "bg-neutral";
            const [a, b] = x.effect_ci80 ?? [x.effect_ratio!, x.effect_ratio!];
            const top = i * ROW + ROW / 2;
            return (
              <div key={x.key} title={`${x.key}: ${effect(x)}`}>
                <div
                  className={cx("absolute h-0.5 -translate-y-1/2", tone)}
                  style={{ left: `${pos(a)}%`, width: `${pos(b) - pos(a)}%`, top }}
                />
                <div
                  className={cx("absolute size-2 -translate-x-1/2 -translate-y-1/2", tone)}
                  style={{ left: `${pos(x.effect_ratio!)}%`, top }}
                />
              </div>
            );
          })}
        </div>
      </div>
      <div aria-hidden className="relative ml-[232px] h-4">
        {ticks.map((t, i) => (
          <span
            key={t}
            className={cx(
              "absolute font-mono text-[10px] leading-normal whitespace-nowrap text-faint",
              i === 0 ? "" : i === ticks.length - 1 ? "-translate-x-full" : "-translate-x-1/2",
            )}
            style={{ left: `${pos(t)}%` }}
          >
            {tickLabel(t)}
          </span>
        ))}
      </div>
    </section>
  );
}
