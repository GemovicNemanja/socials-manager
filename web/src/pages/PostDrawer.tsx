import { type ReactNode, useEffect } from "react";
import { usePostDetail } from "../api/hooks";
import type { ForecastDetail, PostDetailResponse, PostLabel, ScoreDetail, SnapshotPoint } from "../api/models";
import { Badge, type BadgeTone } from "../components/Badge";
import { ColumnHeader } from "../components/ColumnHeader";
import { icons } from "../components/icons";
import { LabelChip } from "../components/LabelChip";
import { LegendItem } from "../components/LegendItem";
import { MetricRow, type MetricRowTone } from "../components/MetricRow";
import { PanelHeader } from "../components/PanelHeader";
import { RangeStrip } from "../components/RangeStrip";
import { cx } from "../lib/cx";
import { EMPTY, formatAge, formatDateTime, formatDecimal, formatI48, formatInt, formatLabel } from "../lib/format";
import { rangeReading } from "../lib/status";

const CloseIcon = icons.ActionClose;

const KIND_LABEL: Record<ForecastDetail["kind"], string> = {
  live: "Live: frozen before publishing",
  as_of: "As-of: frozen after publishing",
  treatment_blind: "Treatment-blind (experiment slot)",
  prior_only_uncalibrated: "Prior only, uncalibrated",
};

function statusBadge(detail: PostDetailResponse): { label: string; tone: BadgeTone } {
  const pit = detail.score ? ` · p${Math.round(detail.score.pit_mid * 100)}` : "";
  switch (detail.post.status) {
    case "above":
      return { label: `Above expected${pit}`, tone: "positive" };
    case "below":
      return { label: `Below expected${pit}`, tone: "negative" };
    case "as_expected":
      return { label: `As expected${pit}`, tone: "default" };
    case "provisional":
      return { label: "Provisional · under 48h", tone: "accent" };
    default:
      return { label: "No forecast", tone: "default" };
  }
}

function labelSource(label: PostLabel): string {
  if (label.source === "llm" && label.confidence != null) return `LLM ${label.confidence.toFixed(2)}`;
  return label.source;
}

export interface PostDrawerProps {
  /** Post id as in the URL: "129" or "post:129". */
  postId: string;
  tz: string;
  onClose: () => void;
}

/** Posts screen detail drawer: one post, its frozen forecast against the outcome, captures and provenance. */
export function PostDrawer({ postId, tz, onClose }: PostDrawerProps) {
  const detail = usePostDetail(postId);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <aside
      role="dialog"
      aria-modal="false"
      aria-label="Post detail"
      className="flex w-[400px] shrink-0 flex-col overflow-y-auto border-l border-rule font-sans"
    >
      {detail.isPending ? (
        <DrawerMessage onClose={onClose}>Loading post…</DrawerMessage>
      ) : detail.isError ? (
        <DrawerMessage onClose={onClose}>
          {detail.error.message.startsWith("404") ? `Post ${postId} not found.` : `Can’t load the post (${detail.error.message}).`}
        </DrawerMessage>
      ) : (
        <DrawerBody detail={detail.data} tz={tz} onClose={onClose} />
      )}
    </aside>
  );
}

function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button
      type="button"
      aria-label="Close post detail"
      onClick={onClose}
      className="flex size-5 shrink-0 items-center justify-center rounded-sm text-faint hover:bg-raised hover:text-text"
    >
      <CloseIcon aria-hidden className="size-3.5" />
    </button>
  );
}

function DrawerMessage({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <>
      <div className="flex h-11 items-center justify-end border-b border-rule px-4">
        <CloseButton onClose={onClose} />
      </div>
      <p className="px-4 py-16 text-center text-xs leading-normal text-faint">{children}</p>
    </>
  );
}

function DrawerBody({ detail, tz, onClose }: { detail: PostDetailResponse; tz: string; onClose: () => void }) {
  const { post, forecast, score } = detail;
  const badge = statusBadge(detail);
  const chips: [string, string][] = [
    ...detail.labels.map((l): [string, string] => [formatLabel(l.value), labelSource(l)]),
    [formatLabel(post.type), "post"],
    [detail.has_url ? "Link" : "No link", "post"],
  ];

  return (
    <>
      <div className="flex flex-col gap-2 border-b border-rule px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] leading-normal whitespace-nowrap text-faint">
            Post {post.id.replace(/^post:/, "")} · {formatDateTime(post.created_at, tz)}
          </span>
          <div className="flex-1" />
          <Badge label={badge.label} tone={badge.tone} />
          <CloseButton onClose={onClose} />
        </div>
        <p className="text-[13px] leading-[1.38462] whitespace-pre-wrap text-text">{post.text || EMPTY}</p>
        <div className="flex flex-wrap gap-1.5" aria-label="Labels">
          {chips.map(([label, source]) => (
            <LabelChip key={`${label}-${source}`} label={label} source={source} />
          ))}
        </div>
      </div>

      <Trajectory snapshots={detail.snapshots} forecast={forecast} i48={formatI48(post.i48)} />
      <ForecastSection detail={detail} />
      <Captures snapshots={detail.snapshots} />
      <Provenance forecast={forecast} tz={tz} />

      <div className="mt-auto flex items-center gap-2 border-t border-rule px-4 py-3">
        <span className="flex-1 text-[11px] leading-[1.36364] text-faint">{readout(detail, score)}</span>
      </div>
    </>
  );
}

function readout(detail: PostDetailResponse, score: ScoreDetail | null): string {
  const curve = detail.forecast?.curve_version ?? "the prior curve";
  if (score) {
    const how = score.exact ? "measured" : `estimated with maturity curve ${curve}`;
    const late = score.late_maturity ? " Late maturity: kept out of calibration." : "";
    return `48h value ${how} from the ${formatAge(score.capture_age_h)} capture.${late}`;
  }
  const last = detail.snapshots[detail.snapshots.length - 1];
  if (!last) return "No captures yet.";
  return `48h estimated from the ${formatAge(last.age_h)} capture with maturity curve ${curve}. Scored at the first capture past 48h.`;
}

/* ------------------------------------------------------------------ trajectory */

const W = 366;
const H = 148;
const MIN_AGE_H = 0.25;
const TICKS: [number, string][] = [
  [0.25, "15m"],
  [1, "1h"],
  [2, "2h"],
  [6, "6h"],
  [24, "24h"],
  [48, "48h"],
  [168, "7d"],
  [720, "30d"],
];

function Trajectory({
  snapshots,
  forecast,
  i48,
}: {
  snapshots: SnapshotPoint[];
  forecast: ForecastDetail | null;
  i48: string;
}) {
  const captures = snapshots.filter((s) => s.impressions != null && s.age_h > 0);
  const band = forecast?.by_age ?? [];
  const maxAge = Math.max(168, ...captures.map((s) => s.age_h));
  const lx = (age: number) => (Math.log(Math.max(age, MIN_AGE_H) / MIN_AGE_H) / Math.log(maxAge / MIN_AGE_H)) * W;
  const yMax = Math.max(1, ...captures.map((s) => s.impressions ?? 0), ...band.map((p) => p.q90)) * 1.08;
  const ly = (v: number) => H - (v / yMax) * H;
  const bandPoly = [
    ...band.map((p) => `${lx(p.age_h).toFixed(1)},${ly(p.q90).toFixed(1)}`),
    ...[...band].reverse().map((p) => `${lx(p.age_h).toFixed(1)},${ly(p.q10).toFixed(1)}`),
  ].join(" ");
  const median = band.map((p) => `${lx(p.age_h).toFixed(1)},${ly(p.q50).toFixed(1)}`).join(" ");
  const line = captures.map((s) => `${lx(s.age_h).toFixed(1)},${ly(s.impressions ?? 0).toFixed(1)}`).join(" ");
  const x48 = lx(48);

  return (
    <section aria-label="Impressions by age" className="flex flex-col gap-2 border-b border-rule px-4 py-3">
      <div className="flex items-center gap-3">
        <PanelHeader
          title="Impressions by age"
          subtitle={`${captures.length} run capture${captures.length === 1 ? "" : "s"}`}
          showAction={false}
          inset={false}
          className="h-6 min-w-0 flex-1"
        />
        <LegendItem label="Captures" swatch="positive" />
        <LegendItem label="Forecast" swatch="band" />
      </div>
      <div className="relative h-[150px] w-full border border-rule bg-plot">
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 size-full" role="img" aria-label="Captured impressions by post age against the frozen forecast">
          {band.length > 0 && <polygon points={bandPoly} className="fill-band" fillOpacity={0.85} />}
          {band.length > 0 && (
            <polyline points={median} fill="none" className="stroke-accent" strokeWidth={1.5} strokeDasharray="4 3" />
          )}
          <line x1={x48} y1={0} x2={x48} y2={H} className="stroke-faint" strokeWidth={1} strokeDasharray="2 3" />
          {captures.length > 1 && (
            <polyline points={line} fill="none" className="stroke-positive" strokeWidth={1.5} strokeDasharray="3 3" />
          )}
          {captures.map((s) => (
            <circle key={s.run_id} cx={lx(s.age_h)} cy={ly(s.impressions ?? 0)} r={3} className="fill-positive">
              <title>
                {formatAge(s.age_h)}: {formatInt(s.impressions)} impressions
              </title>
            </circle>
          ))}
        </svg>
        <span
          className="absolute top-1 -ml-7 text-xs leading-normal text-faint"
          style={{ left: `${(x48 / W) * 100}%` }}
        >
          48h
        </span>
        <span
          className="absolute top-5 ml-1.5 font-mono text-xs leading-normal text-positive"
          style={{ left: `${(x48 / W) * 100}%` }}
        >
          {i48}
        </span>
      </div>
      <div className="relative h-4">
        {TICKS.filter(([age]) => age <= maxAge).map(([age, label]) => (
          <span
            key={label}
            className="absolute -translate-x-1/2 font-mono text-[10px] leading-normal text-faint first:translate-x-0"
            style={{ left: `${(lx(age) / W) * 100}%` }}
          >
            {label}
          </span>
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ forecast vs outcome */

function ForecastSection({ detail }: { detail: PostDetailResponse }) {
  const { forecast, score, post } = detail;
  if (!forecast) {
    return (
      <section aria-label="Frozen forecast" className="border-b border-rule px-4 py-3">
        <PanelHeader title="Frozen forecast" subtitle="none for this post" showAction={false} inset={false} className="h-6" />
      </section>
    );
  }
  const q = forecast.quantiles;
  const quantiles: [string, number | null][] = [
    ["p5", q.q05],
    ["p25", q.q25],
    ["p50", q.q50],
    ["p75", q.q75],
    ["p95", q.q95],
  ];
  const pit = score?.pit_mid;
  const tone: MetricRowTone = pit == null ? "default" : pit >= 0.8 ? "above" : pit <= 0.2 ? "below" : "default";
  const reading = rangeReading(post);
  return (
    <section aria-label="Frozen forecast" className="flex flex-col gap-2 border-b border-rule px-4 pt-3 pb-3">
      <PanelHeader
        title="Frozen forecast"
        subtitle={`${forecast.metric} at ${formatDecimal(forecast.horizon_h)}h`}
        showAction={false}
        inset={false}
        className="h-6"
      />
      <div className="grid grid-cols-5" aria-label="Forecast quantiles">
        {quantiles.map(([name, value]) => (
          <div key={name} className="flex flex-col">
            <span className="text-[11px] leading-normal text-faint">{name}</span>
            <span className="font-mono text-xs leading-normal text-text">{formatInt(value)}</span>
          </div>
        ))}
      </div>
      {reading && <RangeStrip reading={reading} className="w-full" />}
      <div className="flex flex-col">
        <div className="flex h-6 items-center border-b border-rule">
          <ColumnHeader label={score ? "At 48h" : "At 48h (est.)"} className="h-6 flex-1" />
          <ColumnHeader label="Actual" align="right" className="h-6 w-16" />
          <ColumnHeader label="Expected 80%" align="right" className="h-6 w-[110px]" />
          <ColumnHeader label="Pctl" align="right" className="h-6 w-12" />
        </div>
        <MetricRow
          metric="Impressions"
          actual={formatI48(post.i48)}
          expected={`${formatInt(q.q10)} – ${formatInt(q.q90)}`}
          percentile={pit != null ? formatInt(pit * 100) : EMPTY}
          tone={tone}
        />
      </div>
      <dl className="grid grid-cols-4 gap-2 pt-1" aria-label="Score">
        <Stat name="PIT (mid)" value={score ? score.pit_mid.toFixed(2) : EMPTY} />
        <Stat name="CRPS (log)" value={score ? score.crps_log.toFixed(3) : EMPTY} />
        <Stat name="In 50%" value={score ? (score.in_50 ? "yes" : "no") : EMPTY} />
        <Stat name="In 80%" value={score ? (score.in_80 ? "yes" : "no") : EMPTY} />
      </dl>
    </section>
  );
}

function Stat({ name, value }: { name: string; value: string }) {
  return (
    <div className="flex flex-col">
      <dt className="text-[11px] leading-normal text-faint">{name}</dt>
      <dd className="font-mono text-xs leading-normal text-text">{value}</dd>
    </div>
  );
}

/* ------------------------------------------------------------------ captures */

function Captures({ snapshots }: { snapshots: SnapshotPoint[] }) {
  return (
    <section aria-label="Captures" className="flex flex-col border-b border-rule px-4 pt-2 pb-3">
      <div role="row" className="flex h-6 items-center border-b border-rule">
        <ColumnHeader label="Age" className="h-6 flex-1" />
        <ColumnHeader label="Impressions" align="right" className="h-6 w-[84px]" />
        <ColumnHeader label="Likes" align="right" className="h-6 w-14" />
        <ColumnHeader label="Replies" align="right" className="h-6 w-14" />
        <ColumnHeader label="Profile" align="right" className="h-6 w-14" />
      </div>
      <div role="rowgroup" aria-label="Captures by age">
        {snapshots.length === 0 && <p className="py-3 text-xs text-faint">No captures yet.</p>}
        {snapshots.map((s) => (
          <div role="row" key={s.run_id} className="flex h-[26px] items-center border-b border-grid font-mono text-xs">
            <span role="cell" className="flex-1 text-muted">
              {formatAge(s.age_h)}
            </span>
            <span role="cell" className="w-[84px] text-right text-text">
              {formatInt(s.impressions)}
            </span>
            <span role="cell" className="w-14 text-right text-muted">
              {formatInt(s.likes)}
            </span>
            <span role="cell" className="w-14 text-right text-muted">
              {formatInt(s.replies)}
            </span>
            <span role="cell" className="w-14 text-right text-muted">
              {formatInt(s.profile_clicks)}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ provenance */

function Provenance({ forecast, tz }: { forecast: ForecastDetail | null; tz: string }) {
  if (!forecast) return null;
  const rows: [string, string, string?][] = [
    ["Forecast", forecast.id],
    ["Kind", KIND_LABEL[forecast.kind]],
    ["Model version", forecast.model_version],
    ["Maturity curve", forecast.curve_version],
    ["Frozen at", formatDateTime(forecast.frozen_at, tz)],
    ["Snapshot run", forecast.snapshot_run_id ?? EMPTY],
    ["Manifest hash", `${forecast.manifest_hash.slice(0, 16)}…`, forecast.manifest_hash],
    ["Draws hash", `${forecast.draws_hash.slice(0, 16)}…`, forecast.draws_hash],
  ];
  return (
    <section aria-label="Provenance" className="flex flex-col gap-1 px-4 py-3">
      <PanelHeader title="Provenance" subtitle="frozen, append-only" showAction={false} inset={false} className="h-6" />
      <dl className="flex flex-col">
        {rows.map(([name, value, full]) => (
          <div key={name} className="flex h-[22px] items-center gap-2">
            <dt className="w-[104px] shrink-0 text-[11px] leading-normal text-faint">{name}</dt>
            <dd
              title={full ?? value}
              className={cx("min-w-0 flex-1 truncate text-xs leading-normal text-muted", name !== "Kind" && "font-mono")}
            >
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
