import { useState } from "react";
import { useFollowers, useGoals, useNextPosts, useShell } from "../api/hooks";
import type { CountTile, FollowerScore, FollowersResponse, FunnelStep, GoalStatus, SlotRow } from "../api/models";
import { Button } from "../components/Button";
import { ColumnHeader } from "../components/ColumnHeader";
import { CountRow } from "../components/CountRow";
import { ExpectationTile, type ExpectationTileProps } from "../components/ExpectationTile";
import { FollowerChart } from "../components/FollowerChart";
import { KeyValue } from "../components/KeyValue";
import { Meter } from "../components/Meter";
import { MetricRow } from "../components/MetricRow";
import { PanelHeader } from "../components/PanelHeader";
import { SectionLabel } from "../components/SectionLabel";
import { StatusPill, type StatusPillStatus } from "../components/StatusPill";
import type { Swatch } from "../components/swatch";
import { expectedFields } from "../lib/expectation";
import { armsSummary, formatSlotRange, relativeDay, sentence, slotClock } from "../lib/experiments";
import { EMPTY, formatCompact, formatDay, formatInt, formatPercent, formatPercentile, formatSigned } from "../lib/format";
import { describeGoal, GOAL_STATUS, goalReasonText, primaryGoal } from "../lib/goals";
import { href } from "../lib/router";
import { useNow } from "../lib/useNow";
import { RegisterPlannedPostDialog } from "./RegisterPlannedPostDialog";
import { RegisterSlotsDialog } from "./RegisterSlotsDialog";
import { SetGoalDialog } from "./SetGoalDialog";

/** Account (plan §1): follower tiles against their frozen expectations, followers vs forecast, the
 * 7-day funnel, the goal, scored past follower forecasts and "Next posts" with experiment arms. */
export function AccountPage() {
  const shell = useShell();
  const followers = useFollowers();
  const goals = useGoals();
  const next = useNextPosts();
  const [dialog, setDialog] = useState<"slots" | "post" | "goal" | null>(null);
  const tz = next.data?.tz ?? shell.data?.account?.tz ?? "UTC";
  const handle = shell.data?.account?.handle;
  const f = followers.data;
  const now = f?.tiles.followers.value;
  const since = f?.actual[0]?.at;
  const goal = primaryGoal(goals.data);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-11 shrink-0 items-center gap-3 border-b border-rule px-4">
        <h1 className="text-sm leading-normal font-medium text-text">Account</h1>
        <span className="text-xs leading-normal whitespace-nowrap text-faint">
          {[handle, now != null ? `${formatInt(now)} followers` : null, since ? `tracked since ${formatDay(since, tz)}` : null]
            .filter(Boolean)
            .join(" · ") || "No account yet"}
        </span>
        <div className="flex-1" />
        <Button label="Register post" icon="ActionCalendar" onClick={() => setDialog("post")} />
        <Button label="Register slots" icon="ActionAdd" disabled={!next.data} onClick={() => setDialog("slots")} />
        <Button label="Set goal" icon="ActionAdd" variant="primary" onClick={() => setDialog("goal")} />
      </header>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <section aria-label="Follower expectations" className="flex w-full shrink-0 border-b border-rule">
          {buildTiles(f).map((tile) => (
            <ExpectationTile key={tile.label} {...tile} className="min-w-0 flex-1" />
          ))}
        </section>
        <div className="flex min-h-[330px] w-full shrink-0 border-b border-rule">
          <FollowerChart
            actual={f?.actual ?? []}
            forecast={f?.forecast ?? null}
            annotations={f?.annotations}
            tz={tz}
            className="flex-1 border-r border-rule"
          />
          <div className="flex w-[400px] shrink-0 flex-col">
            <Funnel steps={f?.funnel ?? []} loading={followers.isPending} />
            <GoalPanel goal={goal} tz={tz} onEdit={() => setDialog("goal")} />
          </div>
        </div>
        <div className="flex w-full flex-1">
          <div className="flex min-w-0 flex-1 flex-col border-r border-rule">
            <PostsThatBroughtFollowers />
            <PastFollowerForecasts past={f?.past ?? []} hasForecast={f?.forecast != null} tz={tz} />
          </div>
          <NextPosts slots={next.data?.slots ?? []} tz={tz} loading={next.isPending} drawing={next.data?.drawing ?? []} />
        </div>
      </div>

      {dialog === "slots" && next.data && (
        <RegisterSlotsDialog
          tz={next.data.tz}
          drawing={next.data.drawing}
          registeredDays={next.data.registered_days}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "post" && <RegisterPlannedPostDialog tz={tz} onClose={() => setDialog(null)} />}
      {dialog === "goal" && (
        <SetGoalDialog
          tz={tz}
          current={goals.data?.current ?? []}
          initialScope={goal?.goal.scope ?? "followers"}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ tiles */

function countTile(
  label: string,
  tile: CountTile | undefined,
  value: (v: number) => string,
  delta: string | null,
  range: (lo: number, hi: number) => string,
): ExpectationTileProps {
  if (tile?.value == null) {
    return { label, value: EMPTY, delta: tile?.note ?? "no run yet", percentile: EMPTY, range: "", reading: "none" };
  }
  const e = tile.expected;
  const base = { label, value: value(tile.value), delta: delta ?? tile.note ?? "" };
  return e
    ? { ...base, ...expectedFields(e.percentile, `exp. ${range(e.expected.q10, e.expected.q90)}`) }
    : { ...base, percentile: EMPTY, range: "no forecast yet", reading: "none" };
}

const perDay = (t: CountTile | undefined) => (t?.per_day != null ? `${t.per_day.toFixed(1)} a day` : null);
const intRange = (lo: number, hi: number) => `${formatCompact(lo)}–${formatCompact(hi)}`;

function buildTiles(f: FollowersResponse | undefined): ExpectationTileProps[] {
  const t = f?.tiles;
  return [
    countTile("Followers", t?.followers, formatInt, t?.net_7d != null ? `${formatSigned(t.net_7d)} net in 7d` : null, intRange),
    countTile("New follows, 7d", t?.new_follows_7d, formatInt, perDay(t?.new_follows_7d), intRange),
    countTile("Unfollows, 7d", t?.unfollows_7d, formatInt, "new follows − net change", intRange),
    countTile("Profile visits, 7d", t?.profile_visits_7d, formatInt, null, intRange),
    countTile(
      "Visit → follow",
      t?.visit_to_follow_7d,
      (v) => formatPercent(v, 1),
      null,
      (lo, hi) => `${formatPercent(lo, 1)}–${formatPercent(hi, 1)}`,
    ),
    countTile("Your replies, 7d", t?.replies_7d, formatInt, perDay(t?.replies_7d), intRange),
  ];
}

/* ------------------------------------------------------------------ funnel and goal */

const FUNNEL_SWATCH: Record<FunnelStep["key"], Swatch> = {
  impressions: "neutral",
  profile_visits: "neutral",
  new_follows: "neutral",
  unfollows: "negative",
  net: "positive",
};

function Funnel({ steps, loading }: { steps: FunnelStep[]; loading: boolean }) {
  return (
    <section aria-label="Follower funnel, 7 days" className="flex flex-col gap-1 border-b border-rule px-4 py-3">
      <PanelHeader
        title="Follower funnel, 7 days"
        subtitle="owner analytics, last 7 days"
        showAction={false}
        inset={false}
        className="mb-1 h-6 w-full"
      />
      {steps.length === 0 && (
        <span className="text-xs leading-[1.45] text-faint">{loading ? "Loading…" : "The first run fills the funnel."}</span>
      )}
      {steps.map((s) => (
        <CountRow
          key={s.key}
          swatch={s.key === "net" && (s.value ?? 0) < 0 ? "negative" : FUNNEL_SWATCH[s.key]}
          name={s.label}
          definition={s.note}
          value={s.key === "net" ? formatSigned(s.value) : formatCompact(s.value)}
          className="h-[26px] w-full"
        />
      ))}
    </section>
  );
}

function GoalPanel({ goal, tz, onEdit }: { goal: GoalStatus | null; tz: string; onEdit: () => void }) {
  const status = goal ? GOAL_STATUS[goal.status] : null;
  const g = goal?.goal;
  const range = goal?.q10 != null && goal.q90 != null ? ` · 80%: ${formatInt(goal.q10)}–${formatInt(goal.q90)}` : "";
  const reason = goal ? goalReasonText(goal) : null;
  return (
    <section aria-label="Goal" className="flex flex-1 flex-col gap-[7px] px-4 pt-2.5 pb-2">
      <PanelHeader
        title="Goal"
        subtitle={goal ? describeGoal(goal.goal, tz) : "No goal set"}
        action={goal ? "Edit" : "Set goal"}
        onAction={onEdit}
        inset={false}
        className="h-6 w-full"
      />
      {goal && g && status ? (
        <>
          <Meter
            label={g.scope === "followers" ? "P(goal met), 4,000 simulated paths" : "P(goal met)"}
            value={goal.p_goal != null ? formatPercent(goal.p_goal, 0) : EMPTY}
            fraction={goal.p_goal ?? 0}
            fill={status.fill}
            className="w-full"
          />
          <KeyValue
            label="Median path"
            value={
              goal.median == null
                ? EMPTY
                : g.scope === "followers" && g.target_at
                  ? `${formatInt(goal.median)} on ${formatDay(g.target_at, tz)}${range}`
                  : `${formatInt(goal.median)} per post${range}`
            }
            className="w-full"
          />
          <KeyValue
            label="Status"
            value={
              <span className="inline-flex items-center gap-2" title={reason ?? undefined}>
                <StatusPill label={status.label} status={status.pill} className="h-[18px] w-fit" />
                {reason ? (
                  <span className="text-faint">{reason}</span>
                ) : (
                  goal.since && <span className="text-faint">since {formatDay(goal.since, tz)}</span>
                )}
              </span>
            }
            className="w-full"
          />
        </>
      ) : (
        <span className="text-xs leading-[1.45] text-faint">
          There is no default goal without peers: goal status reads “no goal set” until you add one. Aim for followers by a
          date, or 48h impressions per post.
        </span>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ posts and past forecasts */

function PostsThatBroughtFollowers() {
  return (
    <section aria-label="Posts that brought followers" className="flex flex-col">
      <PanelHeader
        title="Posts that brought followers"
        subtitle="Follows per post from owner analytics, when the endpoint is available"
        action="Open posts"
        actionHref={href("posts")}
      />
      <div role="row" className="flex h-7 items-center border-y border-rule bg-raised px-4">
        <ColumnHeader label="Post" className="min-w-0 flex-1" />
        <ColumnHeader label="Format" className="w-[88px]" />
        <ColumnHeader label="Topic" className="w-20" />
        <ColumnHeader label="Visits" align="right" className="w-12" />
        <ColumnHeader label="Follows" align="right" className="w-14" />
        <ColumnHeader label="Per 1k" align="right" className="w-[60px]" />
      </div>
      <p className="border-b border-grid px-4 py-3 text-xs leading-[1.45] text-faint">
        Not available yet: X’s API doesn’t give this app follows per post, so no post can be credited with followers.
        Account-level follows are in the funnel above; this table fills when the endpoint is available.
      </p>
    </section>
  );
}

const OUTCOME_OF: Record<FollowerScore["outcome"], { label: string; pill: StatusPillStatus; tone: "above" | "default" | "below" }> = {
  above: { label: "Above expected", pill: "working", tone: "above" },
  as_expected: { label: "As expected", pill: "insufficient", tone: "default" },
  below: { label: "Below expected", pill: "not-working", tone: "below" },
};

function PastFollowerForecasts({ past, hasForecast, tz }: { past: FollowerScore[]; hasForecast: boolean; tz: string }) {
  return (
    <section aria-label="Past follower forecasts" className="flex flex-col pb-3">
      <PanelHeader title="Past follower forecasts" subtitle="frozen at earlier runs, scored when their horizon passed" showAction={false} />
      <div role="row" className="flex h-7 items-center border-y border-rule bg-raised px-4">
        <ColumnHeader label="Forecast" className="min-w-0 flex-1" />
        <ColumnHeader label="Actual" align="right" className="w-16" />
        <ColumnHeader label="Expected (80%)" align="right" className="w-[110px]" />
        <ColumnHeader label="Pctl" align="right" className="w-12" />
        <ColumnHeader label="Outcome" className="w-[136px] pl-4" />
      </div>
      {past.length === 0 ? (
        <p className="px-4 py-3 text-xs leading-[1.45] text-faint">
          {hasForecast
            ? "None scored yet: a frozen follower forecast is scored when its 7-day horizon passes."
            : "No follower forecast has been frozen yet, so none has been scored. Each run freezes one once the follower model has run."}
        </p>
      ) : (
        <div className="flex flex-col px-4">
          {past.map((p) => {
            const o = OUTCOME_OF[p.outcome];
            return (
              <div key={`${p.forecast_id}-${p.horizon_days}`} className="flex items-center">
                <MetricRow
                  metric={`${p.horizon_days}-day, made ${formatDay(p.made_at, tz)} → ${formatDay(p.target_at, tz)}`}
                  actual={formatInt(p.actual)}
                  expected={`${formatInt(p.expected.q10)}–${formatInt(p.expected.q90)}`}
                  percentile={formatPercentile(p.percentile)}
                  tone={o.tone}
                  className="min-w-0 flex-1"
                />
                <StatusPill label={o.label} status={o.pill} className="h-[26px] w-[136px] shrink-0 border-b border-grid pl-4" />
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ next posts (M2) */

function NextPosts({ slots, tz, loading, drawing }: { slots: SlotRow[]; tz: string; loading: boolean; drawing: string[] }) {
  const now = new Date(useNow(60_000));
  const days: { label: string; rows: SlotRow[] }[] = [];
  for (const s of slots) {
    const label = relativeDay(s.planned_at, tz, now);
    const last = days.at(-1);
    if (last && last.label === label) last.rows.push(s);
    else days.push({ label, rows: [s] });
  }
  return (
    <section aria-label="Next posts" className="flex w-[400px] shrink-0 flex-col">
      <PanelHeader title="Next posts" subtitle="next 7 days · arms drawn at registration" showAction={false} />
      <div className="flex flex-col px-4">
        {slots.length === 0 && (
          <p className="py-2 text-xs leading-[1.45] text-faint">
            {loading
              ? "Loading…"
              : drawing.length > 0
                ? `Nothing registered for the next 7 days. Register slots to draw arms for ${drawing.join(", ")}.`
                : "Nothing registered for the next 7 days. Start an experiment, then register slots; or register a planned post."}
          </p>
        )}
        {days.map((d) => (
          <div key={d.label} className="flex flex-col">
            <SectionLabel label={d.label} className="w-full px-0 pt-2" />
            {d.rows.map((s) => (
              <NextPostRow key={s.slot} slot={s} />
            ))}
          </div>
        ))}
      </div>
      <div className="mt-auto flex flex-col gap-1 border-t border-rule px-4 py-3">
        <span className="font-mono text-[11px] leading-normal tracking-[1px] text-faint uppercase">Agents</span>
        <span className="text-xs leading-[1.45] text-muted">
          Agents can register slots and start per-post experiments up to 4 weeks. Longer horizons, more than 3 experiments,
          amendments and exclusions wait for your approval on the Experiments screen.
        </span>
      </div>
    </section>
  );
}

function NextPostRow({ slot: s }: { slot: SlotRow }) {
  const what = s.arms.length > 0 ? sentence(s.arms.map((a) => a.instruction).join("; ")) : s.text || "Planned post";
  return (
    <div data-slot={s.slot} className="flex gap-3 border-b border-grid py-1.5">
      <span className="w-[44px] shrink-0 font-mono text-xs leading-[1.45] text-faint">{slotClock(s)}</span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-xs leading-[1.45] text-muted">
          {s.experiment_slot ? armsSummary(s.arms) : "no experiment"}
          {s.labels.content_kind ? ` · ${s.labels.content_kind}` : ""}
          {s.labels.link_post ? " · link post" : ""}
        </span>
        <span className="text-xs leading-[1.45] text-text">{what}</span>
        <span className="font-mono text-[11px] leading-normal text-faint">
          {s.forecast_kind === "treatment_blind" ? "blind" : "frozen"} 80% range {formatSlotRange(s)} · median {formatInt(s.forecast_q50)}
        </span>
      </div>
    </div>
  );
}
