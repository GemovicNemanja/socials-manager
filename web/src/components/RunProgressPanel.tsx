import type { RunStatus, RunStep } from "../api/models";
import { cx } from "../lib/cx";
import { formatDateTime } from "../lib/format";
import { formatElapsed } from "../lib/time";
import { useNow } from "../lib/useNow";
import { Badge } from "./Badge";
import { icons } from "./icons";
import { PanelHeader } from "./PanelHeader";

/** Short names for the run steps (socialmgr/services/runs.py); unknown steps use the server's summary. */
const STEP_LABELS: Record<string, string> = {
  collect: "Refresh token, collect posts",
  regimes: "Check X ranker changes",
  match_planned: "Match posts to planned slots",
  label: "Label new posts",
  freeze_as_of: "Freeze as-of forecasts",
  score: "Score matured posts",
  fit_curve: "Refit the maturity curve",
  briefing: "Write the briefing",
  backup: "Back up the database",
};

const CheckIcon = icons.ActionCheck;
const RunningIcon = icons.ActorSystem;
const FailedIcon = icons.ActionClose;

export interface RunProgressPanelProps {
  /** The run being executed; null while the request has started but no run row is visible yet. */
  run: RunStatus | null;
  /** Step names in order, used while run is null. */
  stepNames: string[];
  summaries: Record<string, string>;
  details: Record<string, string>;
  tz: string;
  className?: string;
}

/** Overview briefing slot while a run executes: each step's state, live. */
export function RunProgressPanel({ run, stepNames, summaries, details, tz, className }: RunProgressPanelProps) {
  const now = useNow(1000);
  const steps: RunStep[] = run?.steps ?? stepNames.map((name) => ({ name, status: "pending", error: null }));
  const done = steps.filter((s) => s.status === "done").length;
  const current = steps.findIndex((s) => s.status !== "done");
  const started = run ? Date.parse(run.started_at) : null;
  const subtitle = run
    ? `started ${formatDateTime(run.started_at, tz)} · step ${Math.min(steps.length, done + 1)} of ${steps.length}`
    : "starting…";

  return (
    <section aria-label="Run in progress" className={cx("flex flex-col font-sans", className ?? "w-[400px] shrink-0")}>
      <div className="flex h-10 items-center border-b border-rule pr-4">
        <PanelHeader title="Run in progress" subtitle={subtitle} showAction={false} className="flex-1" />
        <Badge label="Running" tone="accent" />
      </div>
      <div
        role="progressbar"
        aria-label="Run progress"
        aria-valuemin={0}
        aria-valuemax={steps.length}
        aria-valuenow={done}
        className="flex h-1 w-full bg-rule"
      >
        <div className="h-full bg-accent transition-[width]" style={{ width: `${(done / Math.max(1, steps.length)) * 100}%` }} />
      </div>
      <ol className="flex flex-col px-4 py-2">
        {steps.map((step, i) => {
          const state = step.status === "done" ? "done" : step.status === "failed" ? "failed" : i === current ? "running" : "pending";
          const detail =
            state === "done"
              ? details[step.name]
              : state === "running" && started != null
                ? `${formatElapsed((now - started) / 1000)} elapsed`
                : state === "failed"
                  ? "failed"
                  : undefined;
          return (
            <li key={step.name} data-state={state} className="flex h-9 items-center gap-2.5" title={summaries[step.name]}>
              <span
                className={cx(
                  "flex size-4 shrink-0 items-center justify-center",
                  state === "done" && "text-positive",
                  state === "running" && "text-accent",
                  state === "failed" && "text-negative",
                )}
              >
                {state === "done" ? (
                  <CheckIcon aria-label="Done" className="size-3.5" />
                ) : state === "running" ? (
                  <RunningIcon aria-label="Running" className="size-3.5" />
                ) : state === "failed" ? (
                  <FailedIcon aria-label="Failed" className="size-3.5" />
                ) : (
                  <span aria-label="Pending" className="block size-1.5 rounded-full bg-rule-strong" />
                )}
              </span>
              <span
                className={cx(
                  "min-w-0 flex-1 truncate text-[13px] leading-normal",
                  state === "pending" ? "text-faint" : "text-text",
                )}
              >
                {STEP_LABELS[step.name] ?? summaries[step.name] ?? step.name}
              </span>
              {detail && (
                <span
                  className={cx(
                    "font-mono text-[11px] leading-normal whitespace-nowrap",
                    state === "failed" ? "text-negative" : "text-faint",
                  )}
                >
                  {detail}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      <div className="flex flex-col gap-1 border-t border-rule px-4 py-3">
        <span className="text-xs leading-[1.45] text-muted">
          You can close this page; the run keeps going. An interrupted run resumes from its last finished step.
        </span>
      </div>
    </section>
  );
}
