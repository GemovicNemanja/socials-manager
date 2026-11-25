import type { ReactNode } from "react";
import { useOverview } from "../api/hooks";
import type { FollowerPoint, Kpi, OverviewResponse } from "../api/models";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { ColumnHeader } from "../components/ColumnHeader";
import { CountRow } from "../components/CountRow";
import { ExpectationTile, type ExpectationTileProps, type ExpectationTileReading } from "../components/ExpectationTile";
import { icons } from "../components/icons";
import { LegendItem } from "../components/LegendItem";
import { Meter } from "../components/Meter";
import { PanelHeader } from "../components/PanelHeader";
import { PostTableRow } from "../components/PostTableRow";
import { cx } from "../lib/cx";
import {
  EMPTY,
  formatChange,
  formatCompact,
  formatDateTime,
  formatDay,
  formatDecimal,
  formatInt,
  formatPercent,
  formatPercentile,
  formatSigned,
  formatUsd,
} from "../lib/format";
import { href, navigate } from "../lib/router";

const RUN_FROM_CLI = "Runs start from the terminal for now: socialmgr run";
const CheckIcon = icons.ActionCheck;

/** MODEL §4.1 thresholds, applied to a KPI's percentile under its predictive distribution. */
function readingOf(percentile: number): ExpectationTileReading {
  if (percentile >= 0.8) return "above";
  if (percentile <= 0.2) return "below";
  return "default";
}

/** Percentile 0.1..0.9 maps onto the band (0.2..0.8 of the strip). */
function stripPosition(percentile: number): number {
  return 0.2 + (percentile - 0.1) * 0.75;
}

function rangeTile(label: string, kpi: Kpi, value: string, delta: string, empty: string): ExpectationTileProps {
  const e = kpi.expected;
  if (kpi.value == null || !e) {
    return { label, value: kpi.value == null ? EMPTY : value, delta, percentile: EMPTY, range: empty, reading: "none" };
  }
  return {
    label,
    value,
    delta,
    percentile: formatPercentile(e.percentile),
    range: `exp. ${formatCompact(e.q10)}–${formatCompact(e.q90)}`,
    reading: readingOf(e.percentile),
    position: stripPosition(e.percentile),
  };
}

function buildTiles(ov: OverviewResponse): ExpectationTileProps[] {
  const t = ov.tiles;
  const noPosts = ov.total_posts === 0;
  const f = t.followers;
  const counts = ov.status_7d;
  return [
    {
      label: "Followers",
      value: formatInt(f.now),
      delta: f.change_7d != null ? `${formatSigned(f.change_7d)} in 7d` : f.now != null ? "at first run" : "no run yet",
      percentile: EMPTY,
      range:
        f.change_28d != null
          ? `${formatSigned(f.change_28d)} in 28d · forecast in M3`
          : f.now != null
            ? "forecast in M3"
            : "first run sets the baseline",
      reading: "none",
    },
    rangeTile(
      "Impressions, 7d",
      t.impressions_7d,
      formatCompact(t.impressions_7d.value),
      noPosts ? "no posts yet" : `${formatChange(t.impressions_7d.value, t.impressions_7d.previous)} vs prior 7d`,
      "needs forecasts",
    ),
    rangeTile(
      "Median 48h impressions",
      t.median_i48,
      formatInt(t.median_i48.value),
      noPosts ? "prior only" : `last ${formatInt(t.median_i48.n_posts)} matured`,
      "needs matured posts",
    ),
    {
      label: "Profile visits, 7d",
      value: formatInt(t.profile_visits_7d.value),
      delta:
        t.profile_visit_share_7d.value != null ? `${formatPercent(t.profile_visit_share_7d.value)} of impr.` : "no posts yet",
      percentile: EMPTY,
      range: noPosts ? "needs matured posts" : "no forecast yet",
      reading: "none",
    },
    {
      label: "Follows per 1k impr.",
      value: t.follows_per_1k_7d.value != null ? t.follows_per_1k_7d.value.toFixed(2) : EMPTY,
      delta:
        t.visit_to_follow_7d.value != null ? `visit→follow ${formatPercent(t.visit_to_follow_7d.value)}` : "no posts yet",
      percentile: EMPTY,
      range: noPosts ? "needs matured posts" : "no forecast yet",
      reading: "none",
    },
    {
      label: "Posts, 7d",
      value: formatInt(t.posts_7d.value),
      delta: noPosts ? "none yet" : `${formatInt(t.posts_7d.previous)} the 7d before`,
      percentile: EMPTY,
      range: noPosts
        ? "planned posts arrive in M1"
        : `${counts.above} above · ${counts.below} below · ${counts.provisional} under 48h`,
      reading: "none",
    },
  ];
}

export function OverviewPage() {
  const overview = useOverview();
  const ov = overview.data;
  const tz = ov?.account?.tz ?? "UTC";

  const context = ov
    ? ov.total_posts === 0
      ? `Day ${ov.days_tracked ?? 0} · no posts yet`
      : `Day ${ov.days_tracked ?? 0} since launch · ${formatInt(ov.total_posts)} posts tracked`
    : "";

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex h-11 w-full shrink-0 items-center gap-3 border-b border-rule px-4 font-sans">
        <h1 className="text-sm leading-normal font-medium text-text">Overview</h1>
        <span
          className="text-xs leading-normal whitespace-nowrap text-faint"
          title={ov?.as_of ? `As of ${formatDateTime(ov.as_of, tz)}` : undefined}
        >
          {context}
        </span>
        <div className="flex-1" />
        {ov?.as_of && (
          <span className="font-mono text-[11px] leading-normal whitespace-nowrap text-faint">
            as of {formatDateTime(ov.as_of, tz)}
          </span>
        )}
        <Button label="Start run" icon="ActorSystem" variant="primary" disabled title={RUN_FROM_CLI} />
      </header>

      {overview.isPending ? (
        <Message>Loading overview…</Message>
      ) : overview.isError ? (
        <Message action={<Button label="Retry" showIcon={false} onClick={() => void overview.refetch()} />}>
          Can’t reach the API ({overview.error.message}). Is <code className="font-mono text-muted">socialmgr serve</code>{" "}
          running?
        </Message>
      ) : (
        <OverviewBody ov={overview.data} tz={tz} />
      )}
    </div>
  );
}

function OverviewBody({ ov, tz }: { ov: OverviewResponse; tz: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <section aria-label="Expectations" className="flex w-full shrink-0 border-b border-rule">
        {buildTiles(ov).map((tile) => (
          <ExpectationTile key={tile.label} {...tile} className="min-w-0 flex-1" />
        ))}
      </section>
      {ov.total_posts === 0 ? <DayZero ov={ov} /> : <Dashboard ov={ov} tz={tz} />}
    </div>
  );
}

function Dashboard({ ov, tz }: { ov: OverviewResponse; tz: string }) {
  return (
    <>
      <div className="flex h-[330px] w-full shrink-0 border-b border-rule font-sans">
        <FollowerPanel series={ov.follower_series} tz={tz} />
        <div className="flex w-[400px] shrink-0 flex-col">
          <StatusSection ov={ov} />
          <HealthSection ov={ov} />
        </div>
      </div>
      <div className="flex w-full flex-1 font-sans">
        <section aria-label="Recent posts" className="flex min-w-0 flex-1 flex-col border-r border-rule">
          <PanelHeader
            title="Recent posts"
            subtitle="48h impressions, estimated from each post’s capture age, against its frozen forecast"
            action="Open posts"
            actionHref={href("posts")}
          />
          <div role="table" aria-label="Recent posts" className="flex flex-col">
            <div role="row" className="flex h-7 items-center border-y border-rule bg-raised px-4">
              <div className="flex min-w-0 flex-1 items-center">
                <ColumnHeader label="Post" className="min-w-0 flex-1" />
                <ColumnHeader label="Format" className="w-[88px]" />
                <ColumnHeader label="Topic" className="w-20" />
                <ColumnHeader label="Age" align="right" className="w-12" />
                <ColumnHeader label="48h est." align="right" className="w-14" />
                <ColumnHeader label="Likes/1k" align="right" className="w-[60px]" />
              </div>
              <ColumnHeader label="vs expected" className="w-[92px] pl-4" />
              <ColumnHeader label="Status" className="w-[92px]" />
            </div>
            <div role="rowgroup">
              {ov.recent_posts.map((post) => (
                <PostTableRow
                  key={post.id}
                  post={post}
                  tz={tz}
                  onSelect={(p) => navigate("posts", p.id.replace(/^post:/, ""))}
                />
              ))}
            </div>
          </div>
        </section>
        <RunPanel ov={ov} tz={tz} />
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ follower chart */

function niceMax(value: number): number {
  if (value <= 0) return 10;
  const step = 10 ** Math.floor(Math.log10(value));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * step >= value) return m * step;
  return 10 * step;
}

/** Share of the plot width given to history; the rest is where the M3 forecast band goes. */
const TODAY_AT = 0.6;

function FollowerPanel({ series, tz }: { series: FollowerPoint[]; tz: string }) {
  const first = series[0];
  const last = series[series.length - 1];
  const t0 = first ? Date.parse(first.at) : 0;
  const t1 = last ? Date.parse(last.at) : 0;
  const span = Math.max(1, t1 - t0);
  const tEnd = t1 + (span * (1 - TODAY_AT)) / TODAY_AT;
  const yMax = niceMax(Math.max(...series.map((p) => p.followers), 0) * 1.05);
  const x = (t: number) => ((t - t0) / (tEnd - t0)) * 800;
  const y = (v: number) => 220 - (v / yMax) * 220;
  const points = series.map((p) => `${x(Date.parse(p.at)).toFixed(1)},${y(p.followers).toFixed(1)}`).join(" ");
  const xTicks = Array.from({ length: 6 }, (_, i) => t0 + ((tEnd - t0) * i) / 5);

  return (
    <section aria-label="Followers" className="flex min-w-0 flex-1 flex-col border-r border-rule px-4 py-3">
      <div className="flex items-center gap-3">
        <PanelHeader
          title="Followers"
          subtitle={last ? `per run · last ${formatDateTime(last.at, tz)}` : "no snapshots yet"}
          showAction={false}
          inset={false}
          className="h-6 min-w-0 flex-1"
        />
        <div className="flex items-center gap-4">
          <LegendItem label="Actual, per run" swatch="neutral" />
          <LegendItem label="Median · M3" swatch="faint" />
          <LegendItem label="80% range · M3" swatch="band" />
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
            {series.length > 0 && (
              <svg
                viewBox="0 0 800 220"
                preserveAspectRatio="none"
                className="absolute inset-0 size-full"
                role="img"
                aria-label={`Followers per run, ${formatInt(first?.followers)} to ${formatInt(last?.followers)}`}
              >
                <polyline
                  points={points}
                  fill="none"
                  className="stroke-neutral"
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                />
                <line
                  x1={x(t1)}
                  y1={0}
                  x2={x(t1)}
                  y2={220}
                  className="stroke-faint"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              </svg>
            )}
            {last && (
              <span
                className="absolute top-1.5 ml-1 font-mono text-[10px] leading-normal text-text"
                style={{ left: `${TODAY_AT * 100}%` }}
              >
                Today {formatInt(last.followers)}
              </span>
            )}
            <div
              className="absolute inset-y-0 right-0 flex items-center justify-center px-6"
              style={{ left: `${TODAY_AT * 100}%` }}
            >
              <p className="max-w-56 text-center text-xs leading-normal text-faint">
                Follower forecast band arrives in <span className="font-mono text-muted">M3</span>.
              </p>
            </div>
          </div>
          <div className="flex h-5 items-end justify-between">
            {xTicks.map((t) => (
              <span key={t} className="font-mono text-[10px] leading-normal text-faint">
                {series.length ? formatDay(new Date(t).toISOString(), tz) : ""}
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ status + health */

function StatusSection({ ov }: { ov: OverviewResponse }) {
  const c = ov.status_28d;
  const matured = c.above + c.as_expected + c.below;
  const share = (n: number) => (matured ? `${(n / matured) * 100}%` : "0%");
  return (
    <section aria-label="Posts vs forecast" className="flex flex-col gap-2.5 border-b border-rule px-4 py-3">
      <PanelHeader
        title="Posts vs forecast, last 28 days"
        subtitle={`${formatInt(matured)} matured (captured at 48h+)`}
        showAction={false}
        inset={false}
        className="h-6 w-full"
      />
      <div className="flex h-2.5 w-full gap-0.5" aria-hidden>
        <div className="h-full rounded-l-xs bg-positive" style={{ width: share(c.above) }} />
        <div className="h-full bg-faint" style={{ width: share(c.as_expected) }} />
        <div className="h-full rounded-r-xs bg-negative" style={{ width: share(c.below) }} />
      </div>
      <div className="flex flex-col">
        <CountRow
          swatch="positive"
          name="Above expected"
          definition="mid-PIT ≥ 0.8 under its frozen forecast"
          value={formatInt(c.above)}
          className="h-[26px] w-full"
        />
        <CountRow
          swatch="faint"
          name="As expected"
          definition="mid-PIT between 0.2 and 0.8"
          value={formatInt(c.as_expected)}
          className="h-[26px] w-full"
        />
        <CountRow
          swatch="negative"
          name="Below expected"
          definition="mid-PIT ≤ 0.2 under its frozen forecast"
          value={formatInt(c.below)}
          className="h-[26px] w-full"
        />
      </div>
    </section>
  );
}

function HealthSection({ ov }: { ov: OverviewResponse }) {
  const cal = ov.calibration;
  const priorOnly = ov.data_sufficiency.reason === "prior_only_uncalibrated";
  return (
    <section aria-label="Calibration" className="flex flex-1 flex-col gap-[7px] px-4 pt-2.5 pb-2">
      <div className="flex items-center gap-2">
        <PanelHeader
          title="Are the expectations right?"
          subtitle="forecast calibration"
          showAction={false}
          inset={false}
          className="h-6 min-w-0 flex-1"
        />
        <Badge label={priorOnly ? "Prior only" : "Monitors in M3"} tone={priorOnly ? "warn" : "default"} />
      </div>
      <Meter
        label="80% range hit rate"
        value={`${formatPercent(cal.in_80_rate, 0)}  n=${formatInt(cal.n)}`}
        fraction={cal.in_80_rate ?? 0}
        className="w-full"
      />
      <Meter
        label="50% range hit rate"
        value={`${formatPercent(cal.in_50_rate, 0)}  n=${formatInt(cal.n)}`}
        fraction={cal.in_50_rate ?? 0}
        className="w-full"
      />
      <span className="text-[11px] leading-[1.45455] text-faint">
        Near 80% and 50% means the ranges are about right. Drift monitors (CUSUM) and goal status arrive in{" "}
        <span className="font-mono text-muted">M3</span>.
      </span>
    </section>
  );
}

/* ------------------------------------------------------------------ last run (briefing slot) */

function BriefingHeading({ children }: { children: ReactNode }) {
  return <span className="font-mono text-[11px] leading-normal tracking-[1px] text-faint uppercase">{children}</span>;
}

function BriefingLine({ children }: { children: ReactNode }) {
  return <span className="text-[13px] leading-[1.45] text-text">{children}</span>;
}

function RunPanel({ ov, tz }: { ov: OverviewResponse; tz: string }) {
  const run = ov.last_run;
  const spend = ov.spend;
  return (
    <section aria-label="Last run" className="flex w-[400px] shrink-0 flex-col">
      <div className="flex h-10 items-center border-b border-rule pr-4">
        <PanelHeader
          title="Last run"
          subtitle={run?.finished_at ? `finished ${formatDateTime(run.finished_at, tz)}` : run ? run.status : "no run yet"}
          showAction={false}
          className="flex-1"
        />
        <Badge label="Briefing in M1" tone="default" />
      </div>
      <div className="flex flex-col gap-4 px-4 py-3">
        <div className="flex flex-col gap-1.5">
          <BriefingHeading>What happened</BriefingHeading>
          {run ? (
            <>
              <BriefingLine>
                Run {run.id.replace(/^run:/, "")} {run.status}
                {run.gap_since_prev_h != null ? `, ${formatDecimal(run.gap_since_prev_h)}h after the previous one` : ""}
              </BriefingLine>
              <BriefingLine>
                {ov.status_7d.above + ov.status_7d.as_expected + ov.status_7d.below} of the last 7 days’ posts matured:{" "}
                {ov.status_7d.above} above, {ov.status_7d.as_expected} as, {ov.status_7d.below} below expected
              </BriefingLine>
            </>
          ) : (
            <BriefingLine>No run yet.</BriefingLine>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <BriefingHeading>Spend</BriefingHeading>
          <Meter
            label={`X API, ${spend.month_label}`}
            value={`${formatUsd(spend.spent_usd)} / ${formatUsd(spend.cap_usd)}`}
            fraction={spend.cap_usd > 0 ? spend.spent_usd / spend.cap_usd : 0}
            fill={spend.spent_usd >= spend.cap_usd ? "negative" : "neutral"}
            className="w-full"
          />
          {run && <span className="text-xs leading-[1.45] text-muted">Last run cost {formatUsd(run.spend_usd)}.</span>}
        </div>
        <p className="text-xs leading-[1.45] text-faint">
          Briefings arrive in <span className="font-mono text-muted">M1</span>: each run will write what happened, what is
          on track and what needs a decision.
        </p>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ day zero */

interface Step {
  title: string;
  body: string;
  done: boolean;
  action?: { label: string; primary?: boolean; title: string };
}

function DayZero({ ov }: { ov: OverviewResponse }) {
  const steps: Step[] = [
    {
      title: "Connect your X account",
      body: "socialmgr connect · OAuth with your own developer app",
      done: ov.account != null,
    },
    {
      title: "Run for the first time",
      body: "Collects your profile and sets the follower baseline. About 1 minute.",
      done: ov.last_run != null,
      action: { label: "Start run", primary: true, title: RUN_FROM_CLI },
    },
    {
      title: "Set a goal",
      body: "There is no default goal without peers. Goal status reads “no goal set” until you add one.",
      done: false,
      action: { label: "Set goal", title: "Goals arrive in M3" },
    },
    {
      title: "Register your first planned posts",
      body: "Each gets a forecast frozen before it goes out. Agents can do this too.",
      done: false,
      action: { label: "Register post", title: "Planned posts arrive with register_planned_post (M1)" },
    },
  ];
  const next = steps.findIndex((s) => !s.done);
  return (
    <div className="flex w-full flex-1 font-sans">
      <section aria-label="Getting started" className="flex min-w-0 flex-1 flex-col border-r border-rule">
        <div className="flex h-10 items-center border-b border-rule pr-4">
          <PanelHeader
            title="Getting started"
            subtitle={`${steps.filter((s) => s.done).length} of ${steps.length} done`}
            showAction={false}
            className="flex-1"
          />
        </div>
        <ol className="flex flex-col">
          {steps.map((step, i) => (
            <li key={step.title} className="flex items-start gap-3 border-b border-rule px-4 py-3.5">
              <div
                className={cx(
                  "flex size-6 shrink-0 items-center justify-center rounded-full font-mono text-xs leading-[1.33333]",
                  step.done
                    ? "bg-positive-subtle text-positive"
                    : i === next
                      ? "border border-accent text-accent"
                      : "border border-rule-strong text-faint",
                )}
              >
                {step.done ? <CheckIcon aria-label="Done" className="size-3.5" /> : i + 1}
              </div>
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <span className={cx("text-[13px] leading-normal font-medium", step.done ? "text-muted" : "text-text")}>
                  {step.title}
                </span>
                <span className="text-xs leading-[1.45] text-faint">{step.body}</span>
              </div>
              {step.action && !step.done && (
                <Button
                  label={step.action.label}
                  showIcon={false}
                  variant={step.action.primary && i === next ? "primary" : "secondary"}
                  disabled
                  title={step.action.title}
                />
              )}
            </li>
          ))}
        </ol>
      </section>
      <section aria-label="What to expect" className="flex w-[400px] shrink-0 flex-col">
        <div className="flex h-10 items-center border-b border-rule pr-4">
          <PanelHeader title="What to expect" subtitle="no peers in v1" showAction={false} className="flex-1" />
          <Badge label="Prior only" tone="warn" />
        </div>
        <div className="flex flex-col gap-3.5 px-4 py-3">
          {[
            [
              "Days 0–7",
              "Forecasts come from hand-set priors and are labelled uncalibrated. They stay out of calibration and drift checks.",
            ],
            [
              "From ~20 matured posts",
              "About a week at 3 posts a day. The model learns your account; drift monitors switch on.",
            ],
            ["From 4 weeks", "Follower forecasts use your own history instead of the prior."],
            [
              "Every run",
              "Posts are read at whatever age they’ve reached; 48h values are estimated until a capture passes 48h.",
            ],
          ].map(([heading, text]) => (
            <div key={heading} className="flex flex-col gap-1">
              <BriefingHeading>{heading}</BriefingHeading>
              <span className="text-[13px] leading-[1.45] text-text">{text}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function Message({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 py-16 text-center">
      <p className="text-xs leading-normal text-faint">{children}</p>
      {action}
    </div>
  );
}
