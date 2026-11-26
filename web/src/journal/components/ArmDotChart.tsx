import { LegendItem } from "../../components/LegendItem";
import type { Swatch } from "../../components/swatch";
import { log2Ticks } from "../../lib/chart";
import { cx } from "../../lib/cx";
import type { JournalExperiment } from "../data";
import { armLabel, ratio } from "../format";
import { PanelTitle } from "./Layout";

/** Control first in neutral, the change in accent; further arms (factorials) after them. */
const ARM_SWATCH: Swatch[] = ["neutral", "accent", "positive", "warn"];
const DOT_BG: Record<Swatch, string> = {
  neutral: "bg-neutral",
  accent: "bg-accent",
  positive: "bg-positive",
  warn: "bg-warn",
  negative: "bg-negative",
  faint: "bg-faint",
  band: "bg-band",
};

/** A stable pseudo-random spread in [-1, 1] per post, so dots don't sit on one vertical line. */
function jitter(i: number): number {
  const s = Math.sin(i * 12.9898 + 78.233) * 43758.5453;
  return (s - Math.floor(s)) * 2 - 1;
}

/**
 * "Every post, as a share of its own forecast": one column per arm, each matured post a dot at
 * outcome ÷ frozen forecast on a log scale (so 2× and 0.5× sit equally far from 1×), with a line at
 * each arm's typical (median) post. Dots carry a tooltip; columns carry direct labels, so arm
 * identity never rests on colour alone.
 */
export function ArmDotChart({ experiment: x }: { experiment: JournalExperiment }) {
  const arms = x.arms;
  const ratios = x.points.map((p) => p.ratio).filter((r) => r > 0);
  const ticks = log2Ticks(Math.min(...ratios, 1), Math.max(...ratios, 1));
  const top = Math.log2(ticks[0]!);
  const bottom = Math.log2(ticks[ticks.length - 1]!);
  const yPct = (r: number) => ((top - Math.log2(Math.max(r, 2 ** bottom))) / (top - bottom || 1)) * 100;
  const column = (label: string) => Math.max(0, arms.findIndex((a) => a.label === label));
  const width = 100 / Math.max(1, arms.length);

  return (
    <section aria-labelledby="arm-chart-title" className="flex flex-col gap-2">
      <PanelTitle title="Every post, as a share of its own forecast" id="arm-chart-title">
        {arms.length > 0 && (
          <div className="flex items-center gap-4">
            {arms.map((a, i) => (
              <LegendItem key={a.label} label={armLabel(a.label)} swatch={ARM_SWATCH[i % ARM_SWATCH.length]} />
            ))}
          </div>
        )}
      </PanelTitle>
      {x.points.length === 0 ? (
        <p className="border border-grid bg-plot px-6 py-10 text-center text-xs leading-normal text-faint">
          No matured posts yet: each post joins the chart 48 hours after it goes out.
        </p>
      ) : (
        <>
          <div className="flex gap-2">
            <div aria-hidden className="relative w-8 shrink-0">
              {ticks.map((t) => (
                <span
                  key={t}
                  className="absolute right-0 -translate-y-1/2 font-mono text-[10px] leading-normal text-faint"
                  style={{ top: `${yPct(t)}%` }}
                >
                  {`${t}×`}
                </span>
              ))}
            </div>
            <div
              role="img"
              aria-label={`${x.points.length} posts as a share of their forecast: ${arms
                .map((a) => `${armLabel(a.label)} typical ${ratio(a.typical_ratio)} over ${a.n} posts`)
                .join("; ")}`}
              className="relative h-[240px] flex-1 border border-grid bg-plot"
            >
              {ticks.map((t) => (
                <div
                  key={t}
                  className={cx("absolute inset-x-0 h-0", t === 1 ? "border-t border-dashed border-faint" : "border-t border-grid")}
                  style={{ top: `${yPct(t)}%` }}
                />
              ))}
              {x.points.map((p, i) => {
                const c = column(p.arm);
                const left = c * width + width / 2 + jitter(i) * width * 0.28;
                return (
                  <div
                    key={i}
                    title={`${armLabel(p.arm)} · ${ratio(p.ratio)} its forecast`}
                    className={cx(
                      "absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-plot",
                      DOT_BG[ARM_SWATCH[c % ARM_SWATCH.length]!],
                    )}
                    style={{ left: `${left}%`, top: `${yPct(p.ratio)}%` }}
                  />
                );
              })}
              {arms.map((a, c) =>
                a.typical_ratio == null ? null : (
                  <div
                    key={a.label}
                    title={`${armLabel(a.label)}: typical post ${ratio(a.typical_ratio)} its forecast`}
                    className="absolute h-0.5 -translate-y-1/2 bg-text"
                    style={{ left: `${c * width + width * 0.15}%`, width: `${width * 0.7}%`, top: `${yPct(a.typical_ratio)}%` }}
                  />
                ),
              )}
            </div>
          </div>
          <div className="flex pl-10">
            {arms.map((a) => (
              <span key={a.label} className="flex-1 text-center text-xs leading-[1.33333] text-muted">
                {armLabel(a.label)} · typical post {ratio(a.typical_ratio)} forecast · {a.n} posts
              </span>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
