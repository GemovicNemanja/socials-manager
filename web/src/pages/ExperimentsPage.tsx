import { useState } from "react";
import { useDecideApproval, useExperiment, useExperimentAction, useExperiments, useNextPosts, useProposeExperiment } from "../api/hooks";
import type { ApprovalCard, ExperimentsResponse, ExperimentSummary, Suggestion } from "../api/models";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { ColumnHeader } from "../components/ColumnHeader";
import { Notice } from "../components/Dialog";
import { ExperimentRow } from "../components/ExperimentRow";
import { KeyValue } from "../components/KeyValue";
import { Meter } from "../components/Meter";
import { PanelHeader } from "../components/PanelHeader";
import { SectionLabel } from "../components/SectionLabel";
import { SuggestionCard } from "../components/SuggestionCard";
import { Tab } from "../components/Tab";
import { cx } from "../lib/cx";
import { formatDay } from "../lib/format";
import { navigate, useRouteSub } from "../lib/router";
import { useNow } from "../lib/useNow";
import { ExperimentReadout } from "./ExperimentReadout";
import { NewExperimentDialog } from "./NewExperimentDialog";
import { RegisterSlotsDialog } from "./RegisterSlotsDialog";

type TabName = "running" | "suggested" | "concluded" | "all";
const MAX_FACTORS = 3;
/** Suggestions shown in the side column, in MODEL §6.10's priority order. */
const SIDE_SUGGESTIONS = 2;

function progress(e: ExperimentSummary): string {
  if (e.status === "pending_approval" || e.status === "draft") return "—";
  if (e.design === "switchback") return e.week != null ? `week ${e.week} / ${e.weeks_total}` : `0 / ${e.weeks_total} wk`;
  return `${e.posts_matured} / ${e.slots_assigned}`;
}

/** Experiments (plan §1, M2): running, suggested and concluded experiments, the selected one's
 * readout, pending approvals and the power preview of E01-E09. */
export function ExperimentsPage() {
  const list = useExperiments();
  const nextPosts = useNextPosts();
  const sub = useRouteSub();
  const now = useNow(60_000);
  const [tab, setTab] = useState<TabName>("running");
  const [dialog, setDialog] = useState<"slots" | "new" | null>(null);
  const [message, setMessage] = useState<{ text: string; tone: "ok" | "negative"; offerSlots?: boolean } | null>(null);
  const data = list.data;
  const running = data ? [...data.running, ...data.waiting] : [];
  const concluded = data?.concluded ?? [];
  const selectedKey = sub ?? running[0]?.key ?? concluded[0]?.key ?? null;
  const detail = useExperiment(selectedKey);
  const tz = data?.tz ?? "UTC";

  const select = (key: string) => navigate("experiments", key);
  const report = (text: string, done: boolean, offerSlots = false) =>
    setMessage({ text, tone: done ? "ok" : "negative", offerSlots: done && offerSlots });

  const counts = data
    ? `${data.running.length} running · ${data.suggestions.filter((s) => !s.already_running).length} suggested · ${concluded.length} concluded · ${data.approvals.length} awaiting approval`
    : list.isError
      ? "Couldn’t load experiments"
      : "Loading…";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-11 shrink-0 items-center gap-3 border-b border-rule px-4">
        <h1 className="text-sm leading-normal font-medium text-text">Experiments</h1>
        <span className="text-xs leading-normal whitespace-nowrap text-faint">{counts}</span>
        <div className="flex-1" />
        <Button label="Register slots" icon="ActionCalendar" onClick={() => setDialog("slots")} disabled={!nextPosts.data} />
        <Button label="New experiment" icon="ActionAdd" variant="primary" onClick={() => setDialog("new")} />
      </header>

      <div role="tablist" aria-label="Experiment lists" className="flex h-9 shrink-0 items-center gap-5 border-b border-rule px-4">
        <Tab label="Running" count={String(running.length)} current={tab === "running" ? "active" : "default"} onSelect={() => setTab("running")} />
        <Tab
          label="Suggested"
          count={String(data?.suggestions.length ?? 0)}
          current={tab === "suggested" ? "active" : "default"}
          onSelect={() => setTab("suggested")}
        />
        <Tab
          label="Concluded"
          count={String(concluded.length)}
          current={tab === "concluded" ? "active" : "default"}
          onSelect={() => setTab("concluded")}
        />
        <Tab label="All" showCount={false} current={tab === "all" ? "active" : "default"} onSelect={() => setTab("all")} />
        <div className="flex-1" />
        <span className="text-[11px] leading-normal whitespace-nowrap text-faint">
          Effect = outcome ÷ frozen blind forecast, 80% interval · multiplicity: e-LOND, α {data?.elond.alpha ?? 0.1}
        </span>
      </div>

      {message && (
        <div
          role="status"
          className={cx(
            "flex shrink-0 items-center gap-3 border-b border-rule px-4 py-2 text-xs leading-[1.45]",
            message.tone === "ok" ? "bg-accent-subtle text-text" : "bg-negative-subtle text-text",
          )}
        >
          <span className="flex-1">{message.text}</span>
          {message.offerSlots && <Button label="Register slots" showIcon={false} variant="ghost" onClick={() => setDialog("slots")} />}
          <Button label="Dismiss" showIcon={false} variant="ghost" onClick={() => setMessage(null)} />
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col overflow-y-auto border-r border-rule">
          {tab === "suggested" ? (
            <SuggestionGrid suggestions={data?.suggestions ?? []} onStarted={report} />
          ) : (
            <ExperimentTable
              tab={tab}
              running={running}
              concluded={concluded}
              selected={selectedKey}
              onSelect={select}
              loading={list.isPending}
            />
          )}
          {detail.data?.experiment && tab !== "suggested" && (
            <>
              <SelectedActions experiment={detail.data.experiment} tz={tz} onDone={report} onRegister={() => setDialog("slots")} />
              <ExperimentReadout detail={detail.data} now={now} />
            </>
          )}
        </div>

        <aside className="flex w-[400px] shrink-0 flex-col overflow-y-auto">
          {data && data.approvals.length > 0 && <Approvals approvals={data.approvals} onDone={report} />}
          <PanelHeader title="Suggested next" subtitle="E01–E09 · power at your posting rate" showAction={false} />
          <div className="flex flex-col gap-3 px-4">
            {(data?.suggestions ?? [])
              .filter((s) => !s.already_running)
              .slice(0, SIDE_SUGGESTIONS)
              .map((s) => (
                <SuggestionItem key={s.key} suggestion={s} onStarted={report} />
              ))}
          </div>
          {data && <Capacity data={data} tz={tz} />}
          {data && <LeversInUse running={data.running} tz={tz} />}
        </aside>
      </div>

      {dialog === "slots" && nextPosts.data && (
        <RegisterSlotsDialog
          tz={nextPosts.data.tz}
          drawing={nextPosts.data.drawing}
          registeredDays={nextPosts.data.registered_days}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "new" && (
        <NewExperimentDialog
          onClose={() => setDialog(null)}
          onDone={(r) => {
            report(r.summary, r.done, r.experiment?.status === "scheduled");
            if (r.experiment) select(r.experiment.key);
          }}
        />
      )}
    </div>
  );
}

function ExperimentTable({
  tab,
  running,
  concluded,
  selected,
  onSelect,
  loading,
}: {
  tab: TabName;
  running: ExperimentSummary[];
  concluded: ExperimentSummary[];
  selected: string | null;
  onSelect: (key: string) => void;
  loading: boolean;
}) {
  const top = tab === "concluded" ? concluded : running;
  const showConcluded = tab === "all" && concluded.length > 0;
  const subtitle =
    tab === "running"
      ? "Running and scheduled first, then those waiting for approval"
      : tab === "concluded"
        ? "Concluded and stopped, newest first"
        : "Running first, then concluded";
  return (
    <section aria-label="Experiments" className="flex flex-col">
      <PanelHeader title="Experiments" subtitle={subtitle} showAction={false} />
      <div role="row" className="flex h-7 items-center border-y border-rule bg-raised px-4">
        <ColumnHeader label="ID" className="w-[72px]" />
        <ColumnHeader label="Experiment" className="flex-1" />
        <ColumnHeader label="Design" className="w-[132px]" />
        <ColumnHeader label="Progress" align="right" className="w-[84px]" />
        <ColumnHeader label="Effect [80%]" align="right" className="w-[150px]" />
        <ColumnHeader label="Status" className="w-[132px] pl-5" />
      </div>
      {top.length === 0 && !showConcluded && (
        <p className="border-b border-grid px-4 py-3 text-xs leading-[1.45] text-faint">
          {loading
            ? "Loading…"
            : tab === "concluded"
              ? "Nothing has concluded yet."
              : "No experiment is running. Start one from the suggestions: E01–E03 run together as a factorial."}
        </p>
      )}
      {top.map((e) => (
        <Row key={e.key} e={e} selected={e.key === selected} onSelect={onSelect} />
      ))}
      {showConcluded && (
        <>
          <div className="flex h-7 items-end border-b border-grid px-4 pb-1">
            <SectionLabel label="Concluded" className="w-full px-0" />
          </div>
          {concluded.map((e) => (
            <Row key={e.key} e={e} selected={e.key === selected} onSelect={onSelect} />
          ))}
        </>
      )}
    </section>
  );
}

function Row({ e, selected, onSelect }: { e: ExperimentSummary; selected: boolean; onSelect: (key: string) => void }) {
  return (
    <div
      role="row"
      aria-selected={selected}
      tabIndex={0}
      onClick={() => onSelect(e.key)}
      onKeyDown={(ev) => (ev.key === "Enter" || ev.key === " " ? onSelect(e.key) : undefined)}
      className={cx(
        "flex h-[34px] cursor-pointer items-center border-b border-grid px-4",
        selected ? "bg-accent-subtle" : "hover:bg-raised",
      )}
    >
      <ExperimentRow id={e.key} name={e.name} design={e.design_label} progress={progress(e)} effect={e.effect_text} className="min-w-0 flex-1" />
      <div className="flex w-[132px] shrink-0 pl-5">
        <Badge label={e.status_label} tone={e.status_tone} />
      </div>
    </div>
  );
}

function SelectedActions({
  experiment: e,
  tz,
  onDone,
  onRegister,
}: {
  experiment: ExperimentSummary;
  tz: string;
  onDone: (text: string, done: boolean) => void;
  onRegister: () => void;
}) {
  const action = useExperimentAction();
  const live = e.status === "active" || e.status === "scheduled" || e.status === "paused";
  if (!live) return null;
  const canExtend = (e.status === "active" || e.status === "paused") && !e.extension_used;
  const run = (kind: "stop" | "extend") => {
    if (kind === "stop" && !window.confirm(`Stop ${e.key} for good? Its planned treated slots revert to control.`)) return;
    action.mutate({ key: e.key, action: kind, reason: "stopped from the Experiments screen" }, { onSuccess: (r) => onDone(r.summary, r.done) });
  };
  return (
    <div className="flex items-center gap-2 border-t border-rule px-4 py-2">
      <span className="flex-1 text-xs leading-[1.45] text-muted">
        {e.status === "scheduled"
          ? `${e.key} is scheduled: register slots to draw its first arms.`
          : e.extension_offered
            ? `${e.key} is near threshold at its horizon: one extension of up to 50% is offered.`
            : `${e.key} started ${e.started_at ? formatDay(e.started_at, tz) : "—"} · ends ${e.horizon_end ? formatDay(e.horizon_end, tz) : "—"}.`}
      </span>
      <Button label="Register slots" icon="ActionCalendar" onClick={onRegister} />
      {canExtend && (
        <Button label="Extend 50%" showIcon={false} title="MODEL §6.3: one extension of up to 50%" disabled={action.isPending} onClick={() => run("extend")} />
      )}
      <Button label="Stop" showIcon={false} variant="ghost" disabled={action.isPending} onClick={() => run("stop")} />
    </div>
  );
}

function Approvals({ approvals, onDone }: { approvals: ApprovalCard[]; onDone: (text: string, done: boolean) => void }) {
  const decide = useDecideApproval();
  return (
    <section aria-label="Awaiting your approval" className="flex flex-col gap-2 border-b border-rule px-4 pt-3 pb-4">
      <span className="font-mono text-[11px] leading-normal tracking-[1px] text-warn uppercase">Awaiting your approval</span>
      {approvals.map((a) => (
        <div key={a.id} aria-label={a.title} role="group" className="flex flex-col gap-2.5 rounded-md border border-rule-strong bg-warn-subtle p-3">
          <span className="text-[13px] leading-[1.4] font-semibold text-text">{a.title}</span>
          <span className="text-xs leading-[1.45] text-muted">{a.detail}</span>
          <div className="flex gap-2">
            <Button
              label="Approve"
              showIcon={false}
              variant="primary"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ id: a.approval_id, decision: "approve" }, { onSuccess: (r) => onDone(r.summary, r.done) })}
            />
            <Button
              label="Reject"
              showIcon={false}
              variant="secondary"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ id: a.approval_id, decision: "reject" }, { onSuccess: (r) => onDone(r.summary, r.done) })}
            />
          </div>
        </div>
      ))}
      {decide.isError && <Notice tone="negative">Couldn’t decide: {decide.error.message}</Notice>}
    </section>
  );
}

/** The three-number preview over the horizon: "4 wk: 1.25× .09 · 1.5× .20 · 2× .48". */
function powerLine(s: Suggestion): string {
  const p = s.power;
  const n = (v: number | null) => (v == null ? "n/a" : v.toFixed(2).replace(/^0/, ""));
  return `${s.horizon_weeks} wk: 1.25× ${n(p.power_125)} · 1.5× ${n(p.power_15)} · 2× ${n(p.power_2)}`;
}

function SuggestionItem({ suggestion: s, onStarted }: { suggestion: Suggestion; onStarted: (text: string, done: boolean, offer?: boolean) => void }) {
  const propose = useProposeExperiment();
  const needsApproval = s.needs_owner_because.length > 0;
  return (
    <section aria-label={`Suggestion ${s.key}`} className="flex flex-col gap-2">
      <SuggestionCard
        author={`${s.key} · MODEL §6.10 · ${s.design_label}`}
        lever={s.factor.lever_label.toLowerCase()}
        title={s.name}
        hypothesis={s.hypothesis}
        metric={s.primary_metric_label}
        power={powerLine(s)}
        className="w-full"
      >
        <div className="flex justify-between gap-3">
          <span className="text-[11px] leading-normal whitespace-nowrap text-faint">80% power at</span>
          <span className="text-right font-mono text-[11px] leading-normal text-text">
            {s.power.mde_ratio_80 ? `${s.power.mde_ratio_80}× lift` : "out of reach"} · {s.power.posts_per_day.toFixed(1)} posts/day
          </span>
        </div>
        <div className="flex justify-between gap-3">
          <span className="text-[11px] leading-normal whitespace-nowrap text-faint">MODEL §6.10</span>
          <span className="text-right text-[11px] leading-normal text-muted">{s.detectability}</span>
        </div>
      </SuggestionCard>
      <div className="flex items-center gap-2">
        <Button
          label={s.already_running ? "Running" : needsApproval ? `Start ${s.key} (needs approval)` : `Start ${s.key}`}
          aria-label={`Start ${s.key}`}
          showIcon={false}
          variant="primary"
          disabled={s.already_running || propose.isPending}
          onClick={() =>
            propose.mutate(
              { suggestion: s.key, custom: null },
              { onSuccess: (r) => onStarted(r.summary, r.done, r.experiment?.status === "scheduled") },
            )
          }
        />
        {needsApproval && !s.already_running && (
          <span className="text-[11px] leading-[1.4] text-warn">{s.needs_owner_because.join("; ")}</span>
        )}
      </div>
    </section>
  );
}

function SuggestionGrid({ suggestions, onStarted }: { suggestions: Suggestion[]; onStarted: (text: string, done: boolean, offer?: boolean) => void }) {
  return (
    <section aria-label="Suggestions" className="flex flex-col">
      <PanelHeader
        title="Suggested experiments"
        subtitle="E01–E09 from MODEL §6.10, with power at your posting rate: 1.25× · 1.5× · 2×"
        showAction={false}
      />
      <div className="grid grid-cols-2 gap-3 px-4 pb-4">
        {suggestions.map((s) => (
          <SuggestionItem key={s.key} suggestion={s} onStarted={onStarted} />
        ))}
      </div>
    </section>
  );
}

function Capacity({ data, tz }: { data: ExperimentsResponse; tz: string }) {
  const perPost = data.running.filter((e) => e.design === "per_post").reduce((n, e) => n + e.factors.length, 0);
  const nextLook = data.running
    .filter((e) => e.next_look)
    .sort((a, b) => Date.parse(a.next_look!.after) - Date.parse(b.next_look!.after))[0];
  return (
    <section aria-label="Capacity" className="mt-3 flex flex-col gap-[7px] border-t border-rule px-4 py-3">
      <PanelHeader
        title="Capacity"
        subtitle={`${data.posts_per_week} posts a week, ${MAX_FACTORS} per-post factors at once`}
        showAction={false}
        inset={false}
        className="h-6 w-full"
      />
      <Meter label="Per-post factors in use" value={`${perPost} / ${MAX_FACTORS}`} fraction={perPost / MAX_FACTORS} fill={perPost >= MAX_FACTORS ? "warn" : "neutral"} className="w-full" />
      <KeyValue
        label="e-LOND (FDR 0.10)"
        value={`${data.elond.tests} concluded test${data.elond.tests === 1 ? "" : "s"} · ${data.elond.rejections} confirmed`}
        className="w-full"
      />
      <KeyValue
        label="Next decision"
        value={nextLook ? `${nextLook.key} ${nextLook.next_look!.look} look · first run after ${formatDay(nextLook.next_look!.after, tz)}` : "No look scheduled"}
        className="w-full"
      />
    </section>
  );
}

function LeversInUse({ running, tz }: { running: ExperimentSummary[]; tz: string }) {
  const rows = running.flatMap((e) =>
    e.factors.map((f) => ({
      lever: f.lever_label,
      value: `${e.key} · ${e.design === "switchback" ? "switchback to" : "until"} ${e.horizon_end ? formatDay(e.horizon_end, tz) : "its first slots"}`,
    })),
  );
  return (
    <section aria-label="Levers in use" className="flex flex-col gap-1 border-t border-rule px-4 py-3">
      <PanelHeader title="Levers in use" subtitle="One experiment per lever at a time" showAction={false} inset={false} className="mb-1 h-6 w-full" />
      {rows.length === 0 ? (
        <span className="text-xs leading-normal text-faint">None: every lever is free.</span>
      ) : (
        rows.map((r) => <KeyValue key={r.lever + r.value} label={r.lever} value={r.value} className="w-full" />)
      )}
    </section>
  );
}
