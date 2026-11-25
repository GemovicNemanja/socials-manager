import type { SlotArm, SlotRow } from "../api/models";
import type { StatusPillStatus } from "../components/StatusPill";
import { EMPTY, formatCompact } from "./format";

/** A slot's outcome against its frozen forecast, coloured like the post status pills (MODEL §4.1). */
export const SLOT_OUTCOME: Record<SlotRow["outcome_status"], { label: string; pill: StatusPillStatus }> = {
  above: { label: "Above", pill: "working" },
  as_expected: { label: "As expected", pill: "insufficient" },
  below: { label: "Below", pill: "not-working" },
  awaiting: { label: "Under 48h", pill: "tracking" },
  planned: { label: "Planned", pill: "tracking" },
  missed: { label: "Missed", pill: "default" },
  cancelled: { label: "Not used", pill: "insufficient" },
};

/** 1.3812 -> "1.38×" */
export function formatRatio(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? EMPTY : `${value.toFixed(2)}×`;
}

/** 0.7412 -> "0.74" */
export function formatProb(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? EMPTY : value.toFixed(digits);
}

/** The frozen 80% range of a slot: "17–886". */
export function formatSlotRange(slot: Pick<SlotRow, "forecast_q10" | "forecast_q90">): string {
  if (slot.forecast_q10 == null || slot.forecast_q90 == null) return EMPTY;
  return `${formatCompact(slot.forecast_q10)}–${formatCompact(slot.forecast_q90)}`;
}

function localDate(iso: string | Date, tz: string): string {
  const date = typeof iso === "string" ? new Date(iso) : iso;
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
  } catch {
    return date.toISOString().slice(0, 10);
  }
}

/** YYYY-MM-DD in the account's zone, k days after now. */
export function localDay(now: Date, tz: string, k = 0): string {
  return localDate(new Date(now.getTime() + k * 86_400_000), tz);
}

/** "Today", "Tomorrow" or a short weekday with the date ("Sat 28"), in the account's zone. */
export function relativeDay(iso: string, tz: string, now: Date): string {
  const day = localDate(iso, tz);
  if (day === localDay(now, tz)) return "Today";
  if (day === localDay(now, tz, 1)) return "Tomorrow";
  return dayChip(day);
}

/** "2026-11-28" -> "Sat 28" */
export function dayChip(day: string): string {
  const d = new Date(`${day}T12:00:00Z`);
  const weekday = new Intl.DateTimeFormat("en-US", { weekday: "short", timeZone: "UTC" }).format(d);
  return `${weekday} ${d.getUTCDate()}`;
}

/** "07:00" from a slot's local time ("2026-11-26 07:00"). */
export function slotClock(slot: Pick<SlotRow, "planned_local">): string {
  return slot.planned_local.slice(11, 16);
}

/** "E03 treatment · E06 control"; a planned post outside experiments reads "no experiment". */
export function armsSummary(arms: SlotArm[]): string {
  if (arms.length === 0) return "no experiment";
  return arms.map((a) => `${a.experiment} ${a.arm}${a.reverted ? " (reverted)" : ""}`).join(" · ");
}

/** Capitalised first letter: "open with the outcome" -> "Open with the outcome". */
export function sentence(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}
