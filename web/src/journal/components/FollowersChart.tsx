import { useState, type MouseEvent } from "react";
import { LegendItem } from "../../components/LegendItem";
import { niceMax } from "../../lib/chart";
import type { Journal } from "../data";
import { day, int } from "../format";
import { PanelTitle } from "./Layout";

const W = 800;
const H = 220;

type Point = Journal["followers"][number];
type Marker = Journal["markers"][number];

/**
 * "My followers since launch": the daily follower count from the first run, from zero on the y axis,
 * with a dashed line where each experiment started. Hover reads the count on any day. Built like the
 * dashboard's FollowerChart (same SVG approach and axis helper) without its forecast band.
 */
export function FollowersChart({ followers, markers }: { followers: Point[]; markers: Marker[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const times = followers.map((p) => Date.parse(p.at));
  const t0 = times[0] ?? 0;
  const t1 = times[times.length - 1] ?? 0;
  const span = t1 - t0 || 1;
  const yMax = niceMax(Math.max(...followers.map((p) => p.value), 0) * 1.05);
  const x = (t: number) => ((t - t0) / span) * W;
  const y = (v: number) => H - (v / yMax) * H;
  const line = followers.map((p, i) => `${x(times[i]!).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  // Experiments that start together share one line.
  const starts = new Map<number, string[]>();
  for (const m of markers) {
    const t = Date.parse(m.at);
    if (t < t0 || t > t1) continue;
    starts.set(t, [...(starts.get(t) ?? []), m.label]);
  }
  const ticks = followers.length > 1 ? [0, 1 / 3, 2 / 3, 1].map((f) => t0 + f * span) : [];
  const last = followers[followers.length - 1];
  const first = followers[0];
  const enough = followers.length > 1;

  const onMove = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const t = t0 + ((event.clientX - box.left) / box.width) * span;
    let best = 0;
    for (let i = 1; i < times.length; i++) if (Math.abs(times[i]! - t) < Math.abs(times[best]! - t)) best = i;
    setHover(best);
  };
  const hovered = hover != null ? followers[hover] : undefined;

  return (
    <section aria-labelledby="followers-chart-title" className="flex flex-col gap-2">
      <PanelTitle title="My followers since launch" id="followers-chart-title">
        {enough && (
          <div className="flex items-center gap-4">
            <LegendItem label="Followers" swatch="neutral" />
            {starts.size > 0 && <LegendItem label="Experiment started" swatch="accent" />}
          </div>
        )}
      </PanelTitle>
      <div
        className="relative h-[220px] border border-grid bg-plot"
        onMouseMove={enough ? onMove : undefined}
        onMouseLeave={() => setHover(null)}
      >
        {enough ? (
          <>
            <svg
              viewBox={`0 0 ${W} ${H}`}
              preserveAspectRatio="none"
              className="absolute inset-0 size-full"
              role="img"
              aria-label={`Followers per day, ${int(first?.value)} on ${day(first!.at)} to ${int(last?.value)} on ${day(last!.at)}${
                starts.size ? `; ${markers.length} experiment start${markers.length === 1 ? "" : "s"} marked` : ""
              }`}
            >
              {[0.25, 0.5, 0.75].map((f) => (
                <line key={f} x1={0} x2={W} y1={H * f} y2={H * f} className="stroke-grid" strokeWidth={1} vectorEffect="non-scaling-stroke" />
              ))}
              {[...starts.keys()].map((t) => (
                <line
                  key={t}
                  x1={x(t)}
                  x2={x(t)}
                  y1={0}
                  y2={H}
                  className="stroke-accent"
                  strokeOpacity={0.5}
                  strokeWidth={1}
                  strokeDasharray="2 3"
                  vectorEffect="non-scaling-stroke"
                />
              ))}
              <polyline points={line} fill="none" className="stroke-neutral" strokeWidth={2} vectorEffect="non-scaling-stroke" />
              {hovered && (
                <line
                  x1={x(times[hover!]!)}
                  x2={x(times[hover!]!)}
                  y1={0}
                  y2={H}
                  className="stroke-faint"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              )}
            </svg>
            {[...starts.entries()].map(([t, labels], i) => (
              <span
                key={t}
                title={`${day(new Date(t).toISOString())}: ${labels.join(", ")}`}
                className="absolute ml-1 font-mono text-[10px] leading-normal text-accent"
                style={{ left: `${(x(t) / W) * 100}%`, top: 6 + (i % 3) * 16 }}
              >
                {labels.length === 1 ? labels[0] : `${labels.length} experiments started`}
              </span>
            ))}
            {hovered && (
              <div
                role="tooltip"
                className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-[3px] border border-rule-strong bg-surface px-2 py-1 font-mono text-[11px] leading-normal whitespace-nowrap text-text"
                style={{
                  left: `${Math.min(92, Math.max(8, (x(times[hover!]!) / W) * 100))}%`,
                  top: `${Math.max(4, (y(hovered.value) / H) * 100 - 18)}%`,
                }}
              >
                {day(hovered.at)} · {int(hovered.value)}
              </div>
            )}
          </>
        ) : (
          <div className="absolute inset-0 flex items-center justify-center px-6">
            <p className="max-w-80 text-center text-xs leading-normal text-faint">
              {followers.length === 0
                ? "No follower counts yet: the first daily run sets the starting point."
                : `${int(first?.value)} followers on ${day(first!.at)}. The line starts after the second daily run.`}
            </p>
          </div>
        )}
      </div>
      {enough && (
        <div aria-hidden className="flex justify-between">
          {ticks.map((t, i) => (
            <span key={t} className="font-mono text-[10px] leading-normal text-faint">
              {day(new Date(t).toISOString())}
              {i === ticks.length - 1 ? ` · ${int(last?.value)}` : ""}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
