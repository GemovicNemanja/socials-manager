import type { ChangeEntry, ChartPoint, FollowerForecast } from "../api/models";
import { cx } from "../lib/cx";
import { formatDateTime, formatDay, formatInt } from "../lib/format";
import { LegendItem } from "./LegendItem";
import { PanelHeader } from "./PanelHeader";

const DAY = 24 * 3600 * 1000;
const W = 800;
const H = 220;

/** The axis top: the smallest step whose quarters are round numbers (ticks sit at quarters). */
function niceMax(value: number): number {
  if (value <= 0) return 10;
  const step = 10 ** Math.floor(Math.log10(value));
  for (const m of [1, 2, 2.5, 4, 5, 6, 8, 10]) if (m * step >= value) return m * step;
  return 10 * step;
}

/** Linear interpolation of the band at time t (clamped to its ends). */
function bandAt(band: FollowerForecast["band"], t: number) {
  const at = band.map((p) => Date.parse(p.at));
  if (t <= at[0]!) return band[0]!;
  for (let i = 1; i < band.length; i++) {
    if (t <= at[i]!) {
      const f = (t - at[i - 1]!) / (at[i]! - at[i - 1]! || 1);
      const a = band[i - 1]!;
      const b = band[i]!;
      return { at: new Date(t).toISOString(), q10: a.q10 + f * (b.q10 - a.q10), q50: a.q50 + f * (b.q50 - a.q50), q90: a.q90 + f * (b.q90 - a.q90) };
    }
  }
  return band[band.length - 1]!;
}

export interface FollowerChartProps {
  /** Followers per run. */
  actual: ChartPoint[];
  /** The latest frozen follower forecast, or null before the follower model has run. */
  forecast: FollowerForecast | null;
  annotations?: ChangeEntry[];
  tz: string;
  className?: string;
}

/**
 * "Followers vs forecast": followers per run, then the latest frozen forecast's median and 80% band
 * from today. History takes about 60% of the width; the forecast shows at most as far ahead as the
 * history reaches back (at least two weeks), clipped to its longest horizon.
 */
export function FollowerChart({ actual, forecast, annotations = [], tz, className }: FollowerChartProps) {
  const first = actual[0];
  const last = actual[actual.length - 1];
  const band = forecast?.band ?? [];
  const t0 = first ? Date.parse(first.at) : 0;
  const t1 = last ? Date.parse(last.at) : 0;
  const span = Math.max(DAY, t1 - t0);
  const wanted = Math.max((span * 2) / 3, 14 * DAY);
  const bandEnd = band.length ? Date.parse(band[band.length - 1]!.at) : null;
  const tEnd = bandEnd != null && bandEnd > t1 ? t1 + Math.min(wanted, bandEnd - t1) : t1 + wanted;
  const visible = band.filter((p) => Date.parse(p.at) <= tEnd);
  const end = band.length ? bandAt(band, tEnd) : null;
  const shown = end ? [...visible, end] : [];
  const yMax = niceMax(Math.max(...actual.map((p) => p.value), ...shown.map((p) => p.q90), 0) * 1.05);
  const x = (t: number) => ((t - t0) / (tEnd - t0 || 1)) * W;
  const y = (v: number) => H - (v / yMax) * H;
  const pt = (t: number, v: number) => `${x(t).toFixed(1)},${y(v).toFixed(1)}`;
  const line = actual.map((p) => pt(Date.parse(p.at), p.value)).join(" ");
  const upper = shown.map((p) => pt(Date.parse(p.at), p.q90));
  const lower = shown.map((p) => pt(Date.parse(p.at), p.q10)).reverse();
  const median = shown.map((p) => pt(Date.parse(p.at), p.q50)).join(" ");
  const todayAt = x(t1) / W;
  const xTicks = Array.from({ length: 6 }, (_, i) => t0 + ((tEnd - t0) * i) / 5);
  const marks = annotations.filter((a) => {
    const t = Date.parse(a.at);
    return t >= t0 && t <= tEnd;
  });

  return (
    <section aria-label="Followers vs forecast" className={cx("flex min-w-0 flex-col px-4 py-3", className)}>
      <div className="flex items-center gap-3">
        <PanelHeader
          title="Followers vs forecast"
          subtitle={
            forecast
              ? `${forecast.kind === "prior_only" ? "Prior-only follower model" : "Account model"} · issued ${formatDateTime(forecast.made_at, tz)}`
              : last
                ? `per run · last ${formatDateTime(last.at, tz)}`
                : "no snapshots yet"
          }
          showAction={false}
          inset={false}
          className="h-6 min-w-0 flex-1"
        />
        <div className="flex items-center gap-4">
          <LegendItem label="Actual, per run" swatch="neutral" />
          <LegendItem label="Median" swatch="accent" />
          <LegendItem label="80% range" swatch="band" />
        </div>
      </div>
      <div className="flex flex-1 gap-2 pt-3">
        <div className="flex w-8 flex-col items-end justify-between pb-5">
          {[1, 0.75, 0.5, 0.25, 0].map((f) => (
            <span key={f} className="font-mono text-[10px] leading-none text-faint">
              {formatInt(yMax * f)}
            </span>
          ))}
        </div>
        <div className="flex flex-1 flex-col">
          <div className="relative flex-1 border border-rule bg-plot">
            <div className="absolute inset-0 flex flex-col justify-between">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="h-px w-full bg-grid" />
              ))}
            </div>
            {actual.length > 0 && (
              <svg
                viewBox={`0 0 ${W} ${H}`}
                preserveAspectRatio="none"
                className="absolute inset-0 size-full"
                role="img"
                aria-label={`Followers per run, ${formatInt(first?.value)} to ${formatInt(last?.value)}${
                  end ? `; forecast median ${formatInt(end.q50)} on ${formatDay(end.at, tz)}` : ""
                }`}
              >
                {shown.length > 1 && (
                  <>
                    <polygon points={[...upper, ...lower].join(" ")} className="fill-band" fillOpacity={0.85} />
                    <polyline
                      points={median}
                      fill="none"
                      className="stroke-accent"
                      strokeWidth={2}
                      strokeDasharray="5 4"
                      vectorEffect="non-scaling-stroke"
                    />
                  </>
                )}
                <polyline points={line} fill="none" className="stroke-neutral" strokeWidth={2} vectorEffect="non-scaling-stroke" />
                {marks.map((m) => (
                  <line
                    key={`${m.at}-${m.text}`}
                    x1={x(Date.parse(m.at))}
                    y1={0}
                    x2={x(Date.parse(m.at))}
                    y2={H}
                    className="stroke-rule-strong"
                    strokeWidth={1}
                    strokeDasharray="2 3"
                    vectorEffect="non-scaling-stroke"
                  />
                ))}
                <line x1={x(t1)} y1={0} x2={x(t1)} y2={H} className="stroke-faint" strokeWidth={1} vectorEffect="non-scaling-stroke" />
              </svg>
            )}
            {marks.map((m, i) => (
              <span
                key={`${m.at}-${m.text}`}
                title={`${formatDay(m.at, tz)} · ${m.source}: ${m.text}`}
                className="absolute -translate-x-full rounded-[3px] border border-rule-strong bg-surface px-[5px] py-px text-[10px] leading-normal whitespace-nowrap text-muted"
                style={{ left: `${(x(Date.parse(m.at)) / W) * 100}%`, top: 40 + (i % 3) * 22 }}
              >
                {m.text}
              </span>
            ))}
            {last && (
              <span
                className="absolute top-1.5 ml-1 font-mono text-[10px] leading-normal text-text"
                style={{ left: `${todayAt * 100}%` }}
              >
                Today {formatInt(last.value)}
              </span>
            )}
            {end && shown.length > 1 && (
              <div
                className="absolute right-1.5 flex -translate-y-full flex-col items-end pb-1"
                style={{ top: `${(y(end.q90) / H) * 100}%` }}
              >
                <span className="font-mono text-[11px] leading-normal text-accent">{formatInt(end.q50)}</span>
                <span className="font-mono text-[10px] leading-normal text-faint">
                  {formatInt(end.q10)}–{formatInt(end.q90)}
                </span>
              </div>
            )}
            {!forecast && actual.length > 0 && (
              <div className="absolute inset-y-0 right-0 flex items-center justify-center px-6" style={{ left: `${todayAt * 100}%` }}>
                <p className="max-w-60 text-center text-xs leading-normal text-faint">
                  No follower forecast frozen yet. Each run freezes one once the follower model has run; until then,
                  actuals only.
                </p>
              </div>
            )}
            {actual.length === 0 && (
              <div className="absolute inset-0 flex items-center justify-center px-6">
                <p className="text-center text-xs leading-normal text-faint">The first run sets the follower baseline.</p>
              </div>
            )}
          </div>
          <div className="flex h-5 items-end justify-between">
            {xTicks.map((t) => (
              <span key={t} className="font-mono text-[10px] leading-normal text-faint">
                {actual.length ? formatDay(new Date(t).toISOString(), tz) : ""}
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
