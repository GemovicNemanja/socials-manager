import { useMemo, useState } from "react";
import { useRegisterSlots } from "../api/hooks";
import type { DayPlanIn, RegisterSlotsResponse } from "../api/models";
import { Button } from "../components/Button";
import { DialogFrame, Field, Notice } from "../components/Dialog";
import { cx } from "../lib/cx";
import { dayChip, formatSlotRange, localDay, slotClock } from "../lib/experiments";
import { useNow } from "../lib/useNow";

/** Tags that factors filter on (E01 demo posts, E02 link posts). */
const TAGS = [
  ["", "No tag"],
  ["content_kind=demo", "Demo post"],
  ["link_post=true", "Link post"],
] as const;
type Tag = (typeof TAGS)[number][0];
const DEFAULT_TIMES = ["09:00", "13:00", "17:00"];
const DAYS_AHEAD = 7;

export interface RegisterSlotsDialogProps {
  tz: string;
  /** Experiments the new slots draw arms for. */
  drawing: string[];
  /** Local dates that already hold experiment slots (whole days are registered once). */
  registeredDays: string[];
  onClose: () => void;
}

interface SlotDraft {
  time: string;
  tag: Tag;
}

/**
 * Register whole local days of slots for every scheduled or active experiment (a factorial).
 * The server freezes each slot's treatment-blind forecast before it draws the arms; the result
 * says what to post in each slot.
 */
export function RegisterSlotsDialog({ tz, drawing, registeredDays, onClose }: RegisterSlotsDialogProps) {
  const now = useNow(60_000);
  const days = useMemo(() => Array.from({ length: DAYS_AHEAD }, (_, k) => localDay(new Date(now), tz, k + 1)), [now, tz]);
  const taken = new Set(registeredDays);
  const [picked, setPicked] = useState<string[]>(() => {
    const first = days.find((d) => !taken.has(d));
    return first ? [first] : [];
  });
  const [slots, setSlots] = useState<SlotDraft[]>(DEFAULT_TIMES.map((time) => ({ time, tag: "" })));
  const register = useRegisterSlots();
  const result = register.data?.registered ? register.data : null;
  const refused = register.data && !register.data.registered ? register.data : null;

  const valid = picked.length > 0 && slots.length > 0 && slots.every((s) => /^\d{2}:\d{2}$/.test(s.time));
  const submit = () => {
    if (!valid) return;
    const plan: DayPlanIn[] = [...picked].sort().map((day) => ({
      day,
      slots: slots.map((s) => {
        const [k, v] = s.tag ? s.tag.split("=") : [];
        return { time: s.time, post_type: "original", labels: k && v ? { [k]: v } : {} };
      }),
    }));
    register.mutate({ days: plan });
  };
  const toggleDay = (day: string) =>
    setPicked((current) => (current.includes(day) ? current.filter((d) => d !== day) : [...current, day]));
  const setSlot = (i: number, patch: Partial<SlotDraft>) =>
    setSlots((current) => current.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  if (result) return <RegisteredSlots result={result} tz={tz} onClose={onClose} />;

  return (
    <DialogFrame
      title="Register slots"
      subtitle={
        drawing.length > 0
          ? `Whole days, up to 7 ahead. Each slot's treatment-blind forecast is frozen first, then arms are drawn for ${drawing.join(", ")}.`
          : "No experiment is scheduled or running, so there are no arms to draw. Start one on the Experiments screen first."
      }
      onClose={onClose}
      footer={
        <>
          <Button label="Cancel" showIcon={false} variant="ghost" onClick={onClose} />
          <Button
            label={register.isPending ? "Registering…" : "Register and draw arms"}
            showIcon={false}
            variant="primary"
            disabled={!valid || drawing.length === 0 || register.isPending}
            onClick={submit}
          />
        </>
      }
    >
      <div className="flex flex-col gap-1.5">
        <span className="text-xs leading-[1.33333] text-muted">Days ({tz})</span>
        <div role="group" aria-label="Days" className="flex flex-wrap gap-1.5">
          {days.map((day) => {
            const isTaken = taken.has(day);
            const on = picked.includes(day);
            return (
              <button
                key={day}
                type="button"
                aria-pressed={on}
                aria-label={day}
                disabled={isTaken}
                title={isTaken ? "Already registered: whole days are registered once" : undefined}
                onClick={() => toggleDay(day)}
                className={cx(
                  "h-7 rounded-sm border px-2.5 font-mono text-xs leading-normal",
                  on ? "border-accent bg-accent-subtle text-text" : "border-rule text-muted hover:bg-surface",
                  isTaken && "cursor-default line-through opacity-50 hover:bg-transparent",
                )}
              >
                {dayChip(day)}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-xs leading-[1.33333] text-muted">Slot times, the same each day (originals at least 3h apart)</span>
        {slots.map((slot, i) => (
          <div key={i} className="flex items-end gap-2">
            <div className="w-[120px]">
              <Field label={`Slot ${i + 1} time`}>
                {(id) => (
                  <input
                    id={id}
                    type="time"
                    value={slot.time}
                    onChange={(e) => setSlot(i, { time: e.target.value })}
                    className="h-8 w-full rounded-sm border border-rule bg-ground px-2.5 font-mono text-xs text-text outline-none [color-scheme:dark] focus:border-accent"
                  />
                )}
              </Field>
            </div>
            <div className="flex-1">
              <Field label={`Slot ${i + 1} tag`}>
                {(id) => (
                  <select
                    id={id}
                    value={slot.tag}
                    onChange={(e) => setSlot(i, { tag: e.target.value as Tag })}
                    className="h-8 w-full rounded-sm border border-rule bg-ground px-2 text-[13px] text-text outline-none [color-scheme:dark] focus:border-accent"
                  >
                    {TAGS.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
            </div>
            <Button
              label="Remove"
              showIcon={false}
              variant="ghost"
              aria-label={`Remove slot ${i + 1}`}
              disabled={slots.length === 1}
              onClick={() => setSlots((current) => current.filter((_, j) => j !== i))}
            />
          </div>
        ))}
        <Button
          label="Add a time"
          variant="ghost"
          className="h-7 w-fit"
          disabled={slots.length >= 6}
          onClick={() => setSlots((current) => [...current, { time: "20:00", tag: "" }])}
        />
      </div>

      <p className="text-[11px] leading-[1.45] text-faint">
        Tags matter only to experiments that filter on them: E01 draws on demo posts, E02 on link posts. Post within 30 minutes of
        each slot; a post the run can't match counts as missed.
      </p>
      {refused && <Notice tone="negative">{refused.summary}</Notice>}
      {register.isError && <Notice tone="negative">Couldn’t register: {register.error.message}</Notice>}
    </DialogFrame>
  );
}

function RegisteredSlots({ result, tz, onClose }: { result: RegisterSlotsResponse; tz: string; onClose: () => void }) {
  return (
    <DialogFrame
      title="Slots registered"
      subtitle={result.summary}
      onClose={onClose}
      width="w-[640px]"
      footer={<Button label="Done" showIcon={false} variant="primary" onClick={onClose} />}
    >
      {result.warnings.map((w) => (
        <Notice key={w} tone="warn">
          {w}
        </Notice>
      ))}
      <section aria-label="What to post" className="flex flex-col">
        {result.slots.map((slot) => (
          <div key={slot.slot} className="flex gap-3 border-b border-grid py-2">
            <span className="w-[92px] shrink-0 font-mono text-xs leading-[1.45] text-faint">
              {dayChip(slot.planned_local.slice(0, 10))} {slotClock(slot)}
            </span>
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="text-xs leading-[1.45] text-text">{slot.instructions}</span>
              <span className="font-mono text-[11px] leading-normal text-faint">
                blind range {formatSlotRange(slot)} · {slot.forecast} · {tz}
              </span>
            </div>
          </div>
        ))}
      </section>
    </DialogFrame>
  );
}
