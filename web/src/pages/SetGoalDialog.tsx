import { useEffect, useMemo, useRef, useState } from "react";
import { useGoalPreview, useSetGoal } from "../api/hooks";
import type { GoalPreview, GoalRequest, GoalScope, GoalSetResponse, GoalStatus } from "../api/models";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { DialogFrame, Field, Notice } from "../components/Dialog";
import { icons } from "../components/icons";
import { KeyValue } from "../components/KeyValue";
import { cx } from "../lib/cx";
import { EMPTY, formatDay, formatInt, formatPercent } from "../lib/format";
import { GOAL_STATUS } from "../lib/goals";
import { utcToZonedInput, zonedInputToUtc } from "../lib/time";
import { useDebounced } from "../lib/useDebounced";
import { useNow } from "../lib/useNow";

const CalendarIcon = icons.ActionCalendar;
const PREVIEW_DEBOUNCE_MS = 400;
const DAY = 24 * 3600 * 1000;
/** goals.MAX_FOLLOWER_HORIZON_DAYS on the backend. */
const MAX_HORIZON_DAYS = 365;

const SCOPES: { value: GoalScope; label: string; unit: string }[] = [
  { value: "followers", label: "Followers", unit: "followers" },
  { value: "posts", label: "48h impressions per post", unit: "impressions" },
];

export interface SetGoalDialogProps {
  tz: string;
  /** Current goals, to start from the one already set for a scope. */
  current: GoalStatus[];
  initialScope?: GoalScope;
  onClose: () => void;
  onSaved?: (result: GoalSetResponse) => void;
}

function parseTarget(text: string): number | null {
  const n = Number(text.replace(/[,\s]/g, ""));
  return text.trim() && Number.isFinite(n) ? n : null;
}

/** Set a goal (MODEL §4.2): followers by a date, or 48h impressions per post, with a live preview. */
export function SetGoalDialog({ tz, current, initialScope = "followers", onClose, onSaved }: SetGoalDialogProps) {
  const existing = (scope: GoalScope) => current.find((g) => g.goal.scope === scope)?.goal ?? null;
  const [scope, setScope] = useState<GoalScope>(initialScope);
  const [targets, setTargets] = useState<Record<GoalScope, string>>(() => ({
    followers: existing("followers") ? String(existing("followers")!.target) : "",
    posts: existing("posts") ? String(existing("posts")!.target) : "",
  }));
  const [date, setDate] = useState(() => {
    const at = existing("followers")?.target_at;
    return utcToZonedInput(at ? new Date(at) : new Date(Date.now() + 60 * DAY), tz).slice(0, 10);
  });
  const targetField = useRef<HTMLInputElement>(null);
  const dateField = useRef<HTMLInputElement>(null);
  const save = useSetGoal();
  const now = useNow(60_000);

  useEffect(() => {
    targetField.current?.focus();
  }, [scope]);

  const target = parseTarget(targets[scope]);
  const targetAt = scope === "followers" ? zonedInputToUtc(`${date}T23:59`, tz) : null;
  const dateProblem =
    scope !== "followers"
      ? null
      : targetAt == null
        ? "Pick the date to reach it by."
        : targetAt.getTime() <= now
          ? "Pick a date in the future."
          : targetAt.getTime() - now > MAX_HORIZON_DAYS * DAY
            ? "Follower goals reach at most a year ahead."
            : null;
  const targetProblem = targets[scope].trim() && (target == null || target <= 0) ? "The target must be a number above zero." : null;
  const targetIso = targetAt?.toISOString() ?? null;
  const request = useMemo<GoalRequest | null>(
    () => (target == null || target <= 0 || dateProblem ? null : { scope, target, target_at: targetIso }),
    [scope, target, dateProblem, targetIso],
  );
  const preview = useGoalPreview(useDebounced(request, PREVIEW_DEBOUNCE_MS));
  const refused = save.data && !save.data.saved ? save.data : null;
  const unit = SCOPES.find((s) => s.value === scope)!.unit;

  const submit = () => {
    if (!request) return;
    save.mutate(request, {
      onSuccess: (result) => {
        if (result.saved) {
          onSaved?.(result);
          onClose();
        }
      },
    });
  };

  return (
    <DialogFrame
      title="Set a goal"
      subtitle="Goal status (working, near threshold, not working) is read against this target."
      onClose={onClose}
      width="w-[520px]"
      footer={
        <>
          <Button label="Cancel" showIcon={false} variant="ghost" onClick={onClose} />
          <Button
            label={save.isPending ? "Saving…" : "Save goal"}
            showIcon={false}
            variant="primary"
            disabled={!request || save.isPending}
            onClick={submit}
          />
        </>
      }
    >
      <div className="flex flex-col gap-1.5">
        <span id="goal-scope-label" className="text-xs leading-[1.33333] text-muted">
          What to aim for
        </span>
        <div role="radiogroup" aria-labelledby="goal-scope-label" className="flex rounded-sm border border-rule bg-ground p-0.5">
          {SCOPES.map((s) => {
            const on = s.value === scope;
            return (
              <button
                key={s.value}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => setScope(s.value)}
                className={cx(
                  "flex h-7 flex-1 items-center justify-center rounded-xs text-xs leading-[1.33333]",
                  on ? "bg-surface font-medium text-text" : "font-normal text-muted hover:text-text",
                )}
              >
                {s.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex gap-3">
        <div className="min-w-0 flex-1">
          <Field label="Target">
            {(id) => (
              <div className="flex h-8 items-center gap-2 rounded-sm border border-rule bg-ground px-2.5 focus-within:border-accent">
                <input
                  id={id}
                  ref={targetField}
                  inputMode="numeric"
                  value={targets[scope]}
                  placeholder={scope === "followers" ? "1,000" : "3,000"}
                  onChange={(e) => setTargets((t) => ({ ...t, [scope]: e.target.value }))}
                  onKeyDown={(e) => e.key === "Enter" && submit()}
                  className="min-w-0 flex-1 bg-transparent font-mono text-[13px] leading-normal text-text outline-none placeholder:text-faint"
                />
                <span className="text-xs leading-[1.33333] text-faint">{unit}</span>
              </div>
            )}
          </Field>
        </div>
        {scope === "followers" && (
          <div className="min-w-0 flex-1">
            <Field label="By">
              {(id) => (
                <div className="flex h-8 items-center gap-2 rounded-sm border border-rule bg-ground px-2.5 focus-within:border-accent">
                  <button
                    type="button"
                    aria-label="Open the date picker"
                    onClick={() => dateField.current?.showPicker?.()}
                    className="shrink-0 text-faint hover:text-muted"
                  >
                    <CalendarIcon aria-hidden className="size-3.5" />
                  </button>
                  <input
                    id={id}
                    ref={dateField}
                    type="date"
                    value={date}
                    onChange={(e) => setDate(e.target.value)}
                    className="min-w-0 flex-1 bg-transparent font-mono text-[13px] leading-normal text-text outline-none [color-scheme:dark] [&::-webkit-calendar-picker-indicator]:hidden"
                  />
                </div>
              )}
            </Field>
          </div>
        )}
      </div>
      {targetProblem && <Notice tone="negative">{targetProblem}</Notice>}
      {dateProblem && date && <Notice tone="negative">{dateProblem}</Notice>}

      <StandToday
        preview={preview.data}
        pending={preview.isFetching}
        error={preview.error}
        idle={request == null}
        scope={scope}
        targetAt={targetAt}
        tz={tz}
      />

      <span className="text-xs leading-[1.45] text-faint">
        Goals are frozen per period. Saving starts a new period, and the change is logged.
      </span>
      {refused && <Notice tone="negative">{refused.summary}</Notice>}
      {save.isError && <Notice tone="negative">Couldn’t save the goal: {save.error.message}</Notice>}
    </DialogFrame>
  );
}

function StandToday({
  preview,
  pending,
  error,
  idle,
  scope,
  targetAt,
  tz,
}: {
  preview: GoalPreview | undefined;
  pending: boolean;
  error: Error | null;
  idle: boolean;
  scope: GoalScope;
  targetAt: Date | null;
  tz: string;
}) {
  const p = !idle && !error ? preview : undefined;
  const status = p ? GOAL_STATUS[p.status] : null;
  const range = p?.q10 != null && p.q90 != null ? ` · 80%: ${formatInt(p.q10)}–${formatInt(p.q90)}` : "";
  const median =
    p?.median == null
      ? EMPTY
      : scope === "followers" && targetAt
        ? `${formatInt(p.median)} on ${formatDay(targetAt.toISOString(), tz)}${range}`
        : `${formatInt(p.median)} per post${range}`;
  return (
    <section
      aria-label="Where you'd stand today"
      aria-busy={pending}
      className="flex flex-col gap-2 rounded-md border border-rule bg-ground p-3"
    >
      <div className="flex items-center gap-2">
        <span className="flex-1 text-[13px] leading-normal font-medium text-text">Where you’d stand today</span>
        {pending ? (
          <span className="font-mono text-[11px] leading-normal text-faint">updating…</span>
        ) : (
          status && <Badge label={status.label} tone={status.badge} />
        )}
      </div>
      <KeyValue
        label="P(goal met)"
        value={p?.p_goal != null ? `${formatPercent(p.p_goal, 0)} · ${formatInt(p.paths)} simulated paths` : EMPTY}
        className="w-full"
      />
      <KeyValue label="Median path" value={median} className="w-full" />
      {/* The summary repeats the rows when they have numbers; it explains why when they don't. */}
      {p?.median == null && (
        <span className={cx("text-xs leading-[1.45]", error ? "text-negative" : "text-faint")}>
          {idle
            ? "Enter a target to see where you’d stand."
            : error
              ? `Couldn’t get a preview (${error.message}).`
              : (p?.summary ?? "Loading the preview…")}
        </span>
      )}
    </section>
  );
}
