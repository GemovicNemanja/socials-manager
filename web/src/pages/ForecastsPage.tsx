import { type ReactNode, useState } from "react";
import { useAcknowledgeAlarm, useGoals, useModelHealth, useShell } from "../api/hooks";
import type { ChangeEntry, DetectorAlarm, GoalStatus, HitRateTile, ModelHealth, MonitorChart, ModelReview } from "../api/models";
import { Badge, type BadgeTone } from "../components/Badge";
import { Button } from "../components/Button";
import { ChangeRow } from "../components/ChangeRow";
import { ColumnHeader } from "../components/ColumnHeader";
import { Notice } from "../components/Dialog";
import { ExpectationTile, type ExpectationTileProps } from "../components/ExpectationTile";
import { KeyValue } from "../components/KeyValue";
import { LegendItem } from "../components/LegendItem";
import { Meter } from "../components/Meter";
import { PanelHeader } from "../components/PanelHeader";
import { StatusPill, type StatusPillStatus } from "../components/StatusPill";
import { Tab } from "../components/Tab";
import { EMPTY, formatDateTime, formatDay, formatInt, formatPercent } from "../lib/format";
import { GOAL_STATUS, goalReasonText, primaryGoal } from "../lib/goals";
import { href, useRouteSub } from "../lib/router";

/** Changes shown before "All changes". */
const CHANGES_SHOWN = 6;
/** Open alarms listed before "and N older". */
const ALARMS_SHOWN = 4;

/** p-values: two decimals, "<0.01" below the gate. */
function formatP(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return EMPTY;
  return p < 0.01 ? "<0.01" : p.toFixed(2);
}

function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v));
}

/* ------------------------------------------------------------------ header tiles */

function hitTile(label: string, t: HitRateTile): ExpectationTileProps {
  const banded = t.band_lo != null && t.band_hi != null;
  return {
    label,
    value: formatPercent(t.value, 0),
    delta: `${formatInt(t.n)} matured posts`,
    percentile: t.in_band == null ? EMPTY : t.in_band ? "in band" : "outside",
    range: banded
      ? `target band ${Math.round(t.band_lo! * 100)}–${Math.round(t.band_hi! * 100)}%`
      : "needs matured posts",
    reading: t.value == null || t.in_band == null ? "none" : t.in_band ? "default" : "below",
    // The tile's band (20–80% of the strip) stands for the binomial target band.
    position:
      banded && t.value != null && t.band_hi! > t.band_lo!
        ? 0.2 + ((t.value - t.band_lo!) / (t.band_hi! - t.band_lo!)) * 0.6
        : undefined,
  };
}

function goalTile(goal: GoalStatus | null, tz: string): ExpectationTileProps {
  if (!goal) {
    return { label: "Goal", value: EMPTY, delta: "no goal set", percentile: "", range: "set one on Account", reading: "none" };
  }
  const g = goal.goal;
  const short = g.scope === "followers" && g.target_at ? `${formatInt(g.target)} by ${formatDay(g.target_at, tz)}` : `${formatInt(g.target)} per post`;
  const s = goal.status;
  return {
    label: `Goal: ${short}`,
    value: formatPercent(goal.p_goal, 0),
    delta: "P(goal met)",
    percentile: s === "no_goal" ? "no status" : GOAL_STATUS[s].label.toLowerCase(),
    range: goal.median != null ? `median path ${formatInt(goal.median)}` : (goalReasonText(goal) ?? ""),
    reading: s === "working" ? "above" : s === "not_working" ? "below" : s === "near" ? "default" : "none",
    position: goal.p_goal != null ? goal.p_goal : undefined,
  };
}

function buildTiles(m: ModelHealth, goal: GoalStatus | null, tz: string): ExpectationTileProps[] {
  const { skill, calibration: cal, drift } = m;
  return [
    hitTile("80% interval hit rate", m.hit_80),
    hitTile("50% interval hit rate", m.hit_50),
    {
      label: "CRPS skill vs baseline",
      value: skill.value == null ? EMPTY : `${skill.value >= 0 ? "+" : "−"}${Math.abs(skill.value).toFixed(2)}`,
      delta: "log scale, week blocks",
      percentile: skill.passed == null ? EMPTY : skill.passed ? "pass" : "fail",
      range:
        skill.ci90_lo != null && skill.ci90_hi != null
          ? `90% CI ${skill.ci90_lo.toFixed(2)} to ${skill.ci90_hi.toFixed(2)}`
          : "computed at the first review",
      reading: skill.passed == null ? "none" : skill.passed ? "above" : "below",
    },
    {
      label: "Calibration test",
      value: formatP(cal.berkowitz_p),
      delta: "Berkowitz p-value",
      percentile: cal.passed == null ? EMPTY : cal.passed ? "pass" : "fail",
      range: "gate p > 0.01",
      reading: cal.passed == null ? "none" : cal.passed ? "default" : "below",
    },
    {
      label: "Location drift",
      value: drift.stat == null ? EMPTY : drift.stat.toFixed(1),
      delta: drift.armed ? drift.reason.split(/, h /)[0]! : "not armed yet",
      percentile: drift.state,
      range: drift.threshold != null ? `alarm at ${drift.threshold.toFixed(2)}` : drift.reason,
      reading: drift.state === "alarm" ? "below" : drift.state === "quiet" ? "default" : "none",
      // The alarm threshold sits at the band's upper edge.
      position: drift.stat != null && drift.threshold ? clamp01((drift.stat / drift.threshold) * 0.8) : undefined,
    },
    goalTile(goal, tz),
  ];
}

/* ------------------------------------------------------------------ page */

/** Forecasts (plan §1, M3): hit rates, skill, calibration, drift, model card, changes, gates, monitors, alarms. */
export function ForecastsPage() {
  const model = useModelHealth();
  const goals = useGoals();
  const shell = useShell();
  const m = model.data;
  const tz = shell.data?.account?.tz ?? "UTC";
  const context = m
    ? `Model ${m.card.version ?? m.card.champion} · refit ${m.card.refit} · ${formatInt(m.owner_n)} matured posts`
    : "";

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex h-11 w-full shrink-0 items-center gap-3 border-b border-rule px-4">
        <h1 className="text-sm leading-normal font-medium text-text">Forecasts</h1>
        <span className="truncate text-xs leading-normal whitespace-nowrap text-faint" title={m?.caveats.join(" ")}>
          {context}
        </span>
        <div className="flex-1" />
        {m?.stage === "prior_only" && <Badge label="Prior only" tone="warn" />}
        {m?.as_of && (
          <span className="font-mono text-[11px] leading-normal whitespace-nowrap text-faint">as of {formatDateTime(m.as_of, tz)}</span>
        )}
      </header>
      {model.isPending ? (
        <Message>Loading model health…</Message>
      ) : model.isError ? (
        <Message action={<Button label="Retry" showIcon={false} onClick={() => void model.refetch()} />}>
          Can’t reach the API ({model.error.message}). Is <code className="font-mono text-muted">socialmgr serve</code> running?
        </Message>
      ) : (
        <ForecastsBody m={model.data} goal={primaryGoal(goals.data)} tz={tz} />
      )}
    </div>
  );
}

function ForecastsBody({ m, goal, tz }: { m: ModelHealth; goal: GoalStatus | null; tz: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <section aria-label="Model health" className="flex w-full shrink-0 border-b border-rule">
        {buildTiles(m, goal, tz).map((tile) => (
          <ExpectationTile key={tile.label} {...tile} className="min-w-0 flex-1" />
        ))}
      </section>
      <div className="flex min-h-[330px] w-full shrink-0 border-b border-rule">
        <PitPanel m={m} />
        <ModelCardPanel m={m} tz={tz} />
      </div>
      <div className="flex w-full shrink-0 border-b border-rule">
        <ChangesPanel changes={m.changes} tz={tz} />
        <DriftPanel m={m} tz={tz} />
      </div>
      <div className="flex w-full flex-1">
        <GatesPanel m={m} tz={tz} />
        <MonitorsPanel m={m} tz={tz} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ PIT and coverage */

function PitPanel({ m }: { m: ModelHealth }) {
  const pit = m.pit;
  const expected = pit.n / 10;
  const top = Math.max(...pit.counts, pit.band_hi ?? 0, expected, 1) * 1.15;
  const y = (v: number) => 150 - (v / top) * 150;
  const hit: Record<string, HitRateTile> = { "0.5": m.hit_50, "0.8": m.hit_80 };
  return (
    <section aria-label="Calibration" className="flex min-w-0 flex-1 flex-col border-r border-rule">
      <PanelHeader
        title="Where outcomes landed in their forecast"
        subtitle={
          pit.n > 0 ? `PIT of 48h impressions, last ${formatInt(pit.n)} originals · flat means calibrated` : "no matured posts yet"
        }
        showAction={false}
      />
      <div className="flex flex-1 gap-6 px-4 pb-3">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex h-5 items-center gap-4">
            <LegendItem label="Posts per decile" swatch="neutral" />
            <LegendItem label="90% band if calibrated" swatch="band" />
          </div>
          <div className="relative flex-1 border border-grid bg-plot">
            {pit.n > 0 ? (
              <svg
                viewBox="0 0 400 150"
                preserveAspectRatio="none"
                className="absolute inset-0 size-full"
                role="img"
                aria-label={`PIT histogram, posts per decile: ${pit.counts.join(", ")}`}
              >
                {pit.band_lo != null && pit.band_hi != null && (
                  <rect x={0} y={y(pit.band_hi)} width={400} height={y(pit.band_lo) - y(pit.band_hi)} className="fill-band" fillOpacity={0.45} />
                )}
                <line
                  x1={0}
                  y1={y(expected)}
                  x2={400}
                  y2={y(expected)}
                  className="stroke-faint"
                  strokeWidth={1}
                  strokeDasharray="3 3"
                  vectorEffect="non-scaling-stroke"
                />
                {pit.counts.map((c, i) => (
                  <rect key={i} x={i * 40 + 4} y={y(c)} width={32} height={150 - y(c)} className="fill-neutral">
                    <title>{`${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}: ${c} posts`}</title>
                  </rect>
                ))}
              </svg>
            ) : (
              <p className="absolute inset-0 flex items-center justify-center px-6 text-center text-xs leading-normal text-faint">
                The histogram fills as posts reach 48h and are scored against their frozen forecasts.
              </p>
            )}
          </div>
          <div className="flex justify-between font-mono text-[10px] leading-normal text-faint">
            <span>0 · far below forecast</span>
            <span>0.5</span>
            <span>far above · 1</span>
          </div>
        </div>
        <div className="flex w-[260px] shrink-0 flex-col gap-[7px] pt-6">
          {m.coverage.map((c) => {
            const tile = hit[String(c.level)];
            return (
              <Meter
                key={c.level}
                label={`${Math.round(c.level * 100)}% intervals containing outcome`}
                value={formatPercent(c.value, 0)}
                fraction={c.value ?? 0}
                fill={tile?.in_band === false ? "negative" : "neutral"}
                className="w-full"
              />
            );
          })}
          <KeyValue
            label="Interval width"
            value={m.width_ratio != null ? `${m.width_ratio.toFixed(2)}× baseline` : "at the first review"}
            className="w-full"
          />
          <KeyValue label="PIT χ² test" value={`p ${formatP(m.calibration.pit_chi2_p)}`} className="w-full" />
          <KeyValue
            label="Next check"
            value={m.next_check_in_posts != null ? `after ${formatInt(m.next_check_in_posts)} more posts` : EMPTY}
            className="w-full"
          />
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ model card */

function Truncated({ text }: { text: string }) {
  return <span title={text}>{text}</span>;
}

function ModelCardPanel({ m, tz }: { m: ModelHealth; tz: string }) {
  const c = m.card;
  const e = c.follower_elasticity;
  const badge: { label: string; tone: BadgeTone } | null = c.last_promotion
    ? { label: `Promoted ${formatDay(c.last_promotion, tz)}`, tone: "positive" }
    : c.stage === "prior_only"
      ? { label: "Prior only", tone: "warn" }
      : null;
  const rows: [string, string][] = [
    ["Outcome model", c.outcome_model],
    ["Prior", c.prior],
    ["Follower elasticity β", e ? `${e.q50.toFixed(2)} [${e.q10.toFixed(2)}, ${e.q90.toFixed(2)}]` : "not fitted yet"],
    ["Post features", c.post_features.length ? c.post_features.join(", ") : EMPTY],
    ["Maturity curve", c.maturity_curve],
    ["Challenger", c.challenger],
    ["Promotion rule", c.promotion_rule],
    ["Last promotion", c.last_promotion ? formatDateTime(c.last_promotion, tz) : "none yet"],
    ["Refit", c.refit],
  ];
  return (
    <section aria-label="Model card" className="flex w-[400px] shrink-0 flex-col">
      <div className="flex h-10 items-center gap-2 px-4">
        <span className="text-[13px] leading-normal font-medium whitespace-nowrap text-text">Model card</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] leading-normal text-faint">{c.version ?? c.champion}</span>
        {badge && <Badge label={badge.label} tone={badge.tone} />}
      </div>
      <div className="flex flex-col px-4">
        {rows.map(([label, value]) => (
          <KeyValue key={label} label={label} value={<Truncated text={value} />} className="w-full" />
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ changes */

function ChangesPanel({ changes, tz }: { changes: ChangeEntry[]; tz: string }) {
  const all = useRouteSub() === "changes";
  const shown = all ? changes : changes.slice(0, CHANGES_SHOWN);
  const more = changes.length > CHANGES_SHOWN;
  return (
    <section aria-label="Changes that can shift performance" className="flex min-w-0 flex-1 flex-col border-r border-rule">
      <PanelHeader
        title="Changes that can shift performance"
        subtitle="X ranker repo, account and model · each sets a change-point"
        action={more ? (all ? "Fewer changes" : `All changes (${changes.length})`) : undefined}
        actionHref={all ? href("forecasts") : href("forecasts", "changes")}
      />
      <div role="table" aria-label="Changes" className="flex flex-col">
        <div role="row" className="flex h-7 items-center border-y border-rule bg-raised px-4">
          <ColumnHeader label="Date" className="w-[72px]" />
          <ColumnHeader label="Source" className="w-[120px]" />
          <ColumnHeader label="Change" className="min-w-0 flex-1" />
          <ColumnHeader label="Model action" align="right" className="w-[176px]" />
        </div>
        <div role="rowgroup">
          {shown.map((c, i) => (
            <div key={`${c.at}-${i}`} className="border-b border-grid px-4">
              <ChangeRow date={formatDay(c.at, tz)} source={c.source} text={c.text} impact={c.action} className="w-full" />
            </div>
          ))}
        </div>
        {changes.length === 0 && (
          <p className="px-4 py-3 text-xs leading-[1.45] text-faint">
            No changes logged yet. X ranker parameter changes, model releases, goals and monitor alarms land here, each
            dated.
          </p>
        )}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ drift monitor */

const MONITOR_TAB: Record<string, string> = {
  d1_location: "Location",
  d1_scale: "Scale",
  d1_followers: "Follower gains",
  d3: "Interval",
};

function DriftPanel({ m, tz }: { m: ModelHealth; tz: string }) {
  const charts = m.monitors.filter((mo) => mo.series.length > 0);
  const [chart, setChart] = useState("d1_location");
  const mo = charts.find((c) => c.chart === chart) ?? charts[0];
  return (
    <section aria-label="Drift monitor" className="flex w-[400px] shrink-0 flex-col pb-3">
      <PanelHeader title="Drift monitor" subtitle={mo ? mo.label : "no monitor has run yet"} showAction={false} />
      {charts.length > 1 && (
        <div role="tablist" aria-label="Monitor charts" className="-mt-2 flex h-8 items-center gap-4 px-4">
          {charts.map((c) => (
            <Tab
              key={c.chart}
              label={MONITOR_TAB[c.chart] ?? c.label}
              showCount={false}
              current={c.chart === mo?.chart ? "active" : "default"}
              onSelect={() => setChart(c.chart)}
              className="h-8 w-fit"
            />
          ))}
        </div>
      )}
      {mo ? <MonitorDetail mo={mo} m={m} tz={tz} /> : (
        <p className="px-4 text-xs leading-[1.45] text-faint">Monitors switch on at {m.owner_terms_at} matured posts.</p>
      )}
    </section>
  );
}

function MonitorDetail({ mo, m, tz }: { mo: MonitorChart; m: ModelHealth; tz: string }) {
  const s = mo.series;
  const t0 = Date.parse(s[0]!.at);
  const t1 = Date.parse(s[s.length - 1]!.at);
  const top = Math.max(...s.map((p) => p.value), (mo.threshold ?? 0) * 1.25, 1e-9);
  const x = (t: number) => ((t - t0) / (t1 - t0 || 1)) * 400;
  const y = (v: number) => 120 - (v / top) * 120;
  const points = s.map((p) => `${x(Date.parse(p.at)).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const changePoints = m.changes.filter((c) => c.source !== "Monitor" && Date.parse(c.at) >= t0 && Date.parse(c.at) <= t1);
  const latest = changePoints[0];
  const onAlarm = m.changes.find((c) => c.source === "Monitor" && c.text.startsWith(mo.label))?.action;
  const isD1 = mo.chart.startsWith("d1_");
  return (
    <div className="flex flex-col gap-1.5 px-4">
      <div className="relative h-[120px] border border-grid bg-plot">
        <svg
          viewBox="0 0 400 120"
          preserveAspectRatio="none"
          className="absolute inset-0 size-full"
          role="img"
          aria-label={`${mo.label}: ${mo.stat != null ? mo.stat.toFixed(2) : "no value"} against threshold ${mo.threshold?.toFixed(2) ?? "none"}`}
        >
          {mo.threshold != null && (
            <line
              x1={0}
              y1={y(mo.threshold)}
              x2={400}
              y2={y(mo.threshold)}
              className="stroke-negative"
              strokeWidth={1}
              strokeDasharray="4 3"
              vectorEffect="non-scaling-stroke"
            />
          )}
          {changePoints.map((c, i) => (
            <line
              key={`${c.at}-${i}`}
              x1={x(Date.parse(c.at))}
              y1={0}
              x2={x(Date.parse(c.at))}
              y2={120}
              className="stroke-rule-strong"
              strokeWidth={1}
              strokeDasharray="2 3"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          <polyline points={points} fill="none" className="stroke-neutral" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
        </svg>
      </div>
      <div className="flex justify-between font-mono text-[10px] leading-normal text-faint">
        <span>{formatDay(s[0]!.at, tz)}</span>
        {latest && <span>{formatDay(latest.at, tz)} change-point</span>}
        <span>today</span>
      </div>
      <KeyValue
        label="State"
        value={
          <span className="inline-flex items-center gap-2">
            <StatusPill label={MONITOR_STATE[mo.state].label} status={MONITOR_STATE[mo.state].pill} className="h-[18px] w-fit" />
            <span className="font-mono text-faint">
              {mo.stat != null ? mo.stat.toFixed(2) : EMPTY}
              {mo.threshold != null ? ` / ${mo.threshold.toFixed(2)}` : ""}
            </span>
          </span>
        }
        className="w-full"
      />
      <KeyValue
        label="Alarm threshold"
        value={mo.threshold != null ? `h ${mo.threshold.toFixed(2)}${isD1 ? " · ARL₀ 300 posts" : ""}` : EMPTY}
        className="w-full"
      />
      <KeyValue label="Detail" value={<Truncated text={mo.detail} />} className="w-full" />
      {onAlarm && <KeyValue label="On alarm" value={onAlarm} className="w-full" />}
    </div>
  );
}

const MONITOR_STATE: Record<MonitorChart["state"], { label: string; pill: StatusPillStatus }> = {
  alarm: { label: "Alarm", pill: "not-working" },
  quiet: { label: "Quiet", pill: "working" },
  off: { label: "Off", pill: "insufficient" },
  proposal: { label: "Proposal", pill: "default" },
};

/* ------------------------------------------------------------------ release gates */

const DECISION: Record<ModelReview["decision"], { label: string; tone: BadgeTone }> = {
  promoted: { label: "Promoted", tone: "positive" },
  held: { label: "Held", tone: "warn" },
  not_eligible: { label: "Not eligible", tone: "default" },
  kept: { label: "Kept", tone: "default" },
};

function GatesPanel({ m, tz }: { m: ModelHealth; tz: string }) {
  const r = m.last_review;
  return (
    <section aria-label="Release gates" className="flex min-w-0 flex-1 flex-col border-r border-rule pb-3">
      <div className="flex h-10 items-center gap-2 pr-4">
        <PanelHeader
          title="Release gates"
          subtitle={
            r
              ? `last review ${formatDateTime(r.at, tz)} · ${r.candidate} vs ${r.champion_before}`
              : `first review at ${m.first_review_at} matured posts`
          }
          showAction={false}
          className="min-w-0 flex-1"
        />
        {r && <Badge label={DECISION[r.decision].label} tone={DECISION[r.decision].tone} />}
      </div>
      {r ? (
        <>
          <div role="table" aria-label="Gates" className="flex flex-col">
            <div role="row" className="flex h-7 items-center border-y border-rule bg-raised px-4">
              <ColumnHeader label="Gate" className="min-w-0 flex-1" />
              <ColumnHeader label="Value" align="right" className="w-20" />
              <ColumnHeader label="Threshold" className="w-[160px] pl-4" />
              <ColumnHeader label="Result" className="w-[104px]" />
            </div>
            <div role="rowgroup">
              {r.gates.map((g) => (
                <div key={g.key} role="row" title={g.detail} className="flex h-[30px] items-center border-b border-grid px-4">
                  <span role="cell" className="min-w-0 flex-1 truncate text-xs leading-normal text-text">
                    {g.label}
                  </span>
                  <span role="cell" className="w-20 text-right font-mono text-xs leading-normal text-text">
                    {g.value == null ? EMPTY : Math.abs(g.value) >= 100 ? formatInt(g.value) : g.value.toFixed(2)}
                  </span>
                  <span role="cell" className="w-[160px] truncate pl-4 font-mono text-xs leading-normal text-faint">
                    {g.threshold}
                  </span>
                  <span role="cell" className="w-[104px]">
                    <StatusPill
                      label={g.passed == null ? "Not evaluated" : g.passed ? "Pass" : "Fail"}
                      status={g.passed == null ? "insufficient" : g.passed ? "working" : "not-working"}
                    />
                  </span>
                </div>
              ))}
            </div>
          </div>
          <p className="px-4 pt-2 text-xs leading-[1.45] text-muted">{r.reason}</p>
          {m.reviews.length > 1 && (
            <div className="flex flex-col px-4 pt-2">
              <span className="font-mono text-[11px] leading-normal tracking-[1px] text-faint uppercase">Earlier reviews</span>
              {m.reviews.slice(1, 6).map((rv) => (
                <KeyValue
                  key={`${rv.run_id}-${rv.at}`}
                  label={formatDateTime(rv.at, tz)}
                  value={`${DECISION[rv.decision].label}: ${rv.reason}`}
                  className="w-full"
                />
              ))}
            </div>
          )}
        </>
      ) : (
        <p className="px-4 text-xs leading-[1.45] text-faint">
          No promotion review yet. The first runs once {m.first_review_at} owner posts have matured and the owner-level model
          can be fitted; a candidate replaces the champion only if every MODEL §5 gate passes. Until then the{" "}
          {m.stage === "prior_only" ? "prior-only" : "current"} model stays, and a failed gate is logged here.
        </p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ monitors and alarms */

function MonitorsPanel({ m, tz }: { m: ModelHealth; tz: string }) {
  const open = m.alarms.filter((a) => a.acknowledged_at == null);
  return (
    <section aria-label="Monitors" className="flex w-[400px] shrink-0 flex-col pb-3">
      <PanelHeader title="Monitors" subtitle="D1–D3 at the last run" showAction={false} />
      <div className="flex flex-col px-4">
        {m.monitors.map((mo) => (
          <div key={mo.chart} title={mo.detail} className="flex h-[26px] items-center gap-2 border-b border-grid">
            <StatusPill label={MONITOR_STATE[mo.state].label} status={MONITOR_STATE[mo.state].pill} className="h-5 w-[72px] shrink-0" />
            <span className="min-w-0 flex-1 truncate text-xs leading-normal text-text">{mo.label}</span>
            <span className="font-mono text-xs leading-normal text-muted">
              {mo.stat != null ? mo.stat.toFixed(2) : EMPTY}
              {mo.threshold != null ? ` / ${mo.threshold.toFixed(2)}` : ""}
            </span>
          </div>
        ))}
      </div>
      <section aria-label="Open alarms" className="flex flex-col gap-1 px-4 pt-3">
        <span className="font-mono text-[11px] leading-normal tracking-[1px] text-faint uppercase">
          Open alarms{open.length ? ` · ${open.length}` : ""}
        </span>
        {open.length === 0 ? (
          <span className="text-xs leading-[1.45] text-faint">None open. An alarm stays listed until you acknowledge it.</span>
        ) : (
          <>
            {open.slice(0, ALARMS_SHOWN).map((a) => (
              <AlarmRow key={a.id} alarm={a} tz={tz} />
            ))}
            {open.length > ALARMS_SHOWN && (
              <span className="text-xs leading-[1.45] text-faint">and {open.length - ALARMS_SHOWN} older</span>
            )}
          </>
        )}
      </section>
    </section>
  );
}

function AlarmRow({ alarm: a, tz }: { alarm: DetectorAlarm; tz: string }) {
  const ack = useAcknowledgeAlarm();
  const refused = ack.data && !ack.data.acknowledged ? ack.data.summary : null;
  return (
    <div data-alarm={a.id} className="flex flex-col gap-1 border-b border-grid py-1.5">
      <div className="flex items-center gap-2">
        <span className="w-[52px] shrink-0 font-mono text-[11px] leading-normal text-faint">{formatDay(a.raised_at, tz)}</span>
        <span className="min-w-0 flex-1 truncate text-xs leading-normal text-text" title={a.label}>
          {a.label}
        </span>
        <Button
          label={ack.isPending ? "Acknowledging…" : "Acknowledge"}
          showIcon={false}
          variant="ghost"
          disabled={ack.isPending}
          onClick={() => ack.mutate({ id: a.id })}
          className="h-6 w-fit"
        />
      </div>
      <span className="pl-[60px] font-mono text-[11px] leading-normal text-muted">
        {a.stat != null ? a.stat.toFixed(2) : EMPTY}
        {a.threshold != null ? ` > ${a.threshold.toFixed(2)}` : ""}
        {a.direction ? ` · ${a.direction}` : ""}
        {a.attribution && a.attribution !== "unknown" ? ` · ${a.attribution}` : ""}
      </span>
      {refused && <Notice tone="negative">{refused}</Notice>}
      {ack.isError && <Notice tone="negative">Couldn’t acknowledge: {ack.error.message}</Notice>}
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

