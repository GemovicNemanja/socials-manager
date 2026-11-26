import { EMPTY, formatCompact, formatInt, formatSigned } from "../lib/format";
import type { BadgeTone } from "../components/Badge";
import type { ExperimentResult, JournalExperiment } from "./data";

/** The public site shows every date in UTC: readers are anywhere, and the numbers are cut in UTC. */
const TZ = "UTC";

function parts(value: string | Date, options: Intl.DateTimeFormatOptions): string {
  const date = typeof value === "string" ? new Date(value.length === 10 ? `${value}T00:00:00Z` : value) : value;
  if (Number.isNaN(date.getTime())) return EMPTY;
  return new Intl.DateTimeFormat("en-US", { timeZone: TZ, ...options }).format(date);
}

/** "2026-11-16" -> "Nov 16" */
export function day(value: string): string {
  return parts(value, { month: "short", day: "numeric" });
}

/** "2026-11-16" -> "Nov 16, 2026" */
export function dayYear(value: string): string {
  return parts(value, { month: "short", day: "numeric", year: "numeric" });
}

/** A period: "Nov 17–23, 2026", "Oct 28 – Nov 3, 2026", "Dec 29, 2025 – Jan 4, 2026". */
export function period(start: string, end: string | null): string {
  if (!end) return `${dayYear(start)} – now`;
  const [sy, ey] = [parts(start, { year: "numeric" }), parts(end, { year: "numeric" })];
  const [sm, em] = [parts(start, { month: "short" }), parts(end, { month: "short" })];
  if (sy !== ey) return `${dayYear(start)} – ${dayYear(end)}`;
  if (sm !== em) return `${day(start)} – ${day(end)}, ${ey}`;
  return `${day(start)}–${parts(end, { day: "numeric" })}, ${ey}`;
}

/** "updated Nov 25, 06:00 UTC" */
export function updated(at: string | null): string {
  if (!at) return "not updated yet";
  const date = parts(at, { month: "short", day: "numeric" });
  const time = parts(at, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  return `updated ${date}, ${time} UTC`;
}

/** 1.3812 -> "1.38×" */
export function ratio(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? EMPTY : `${value.toFixed(digits)}×`;
}

/** An effect and its 80% interval: "1.38× [1.02, 1.86]". */
export function effect(x: Pick<JournalExperiment, "effect_ratio" | "effect_ci80">): string {
  if (x.effect_ratio == null) return EMPTY;
  const ci = x.effect_ci80;
  return ci ? `${ratio(x.effect_ratio)} [${ci[0].toFixed(2)}, ${ci[1].toFixed(2)}]` : ratio(x.effect_ratio);
}

/** The effect cell of an experiments table: the estimate, or why there is none. */
export function effectCell(x: JournalExperiment): string {
  if (x.result === "stopped") return "stopped";
  if (x.result === "scheduled") return "not started";
  if (x.effect_ratio == null) return "too early";
  return effect(x);
}

/** 0.784 -> "78%" */
export function pct(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? EMPTY : `${Math.round(value * 100)}%`;
}

export { formatCompact as compact, formatInt as int, formatSigned as signed };

export const RESULT: Record<ExperimentResult, { label: string; tone: BadgeTone }> = {
  running: { label: "Running", tone: "accent" },
  scheduled: { label: "Scheduled", tone: "default" },
  effect_found: { label: "Effect found", tone: "positive" },
  no_effect: { label: "No effect", tone: "default" },
  stopped: { label: "Stopped", tone: "warn" },
};

export function isOngoing(x: Pick<JournalExperiment, "result">): boolean {
  return x.result === "running" || x.result === "scheduled";
}

/** "outcome_first" -> "outcome first"; display labels pass through. */
export function armLabel(label: string): string {
  return /^[a-z0-9_]+$/.test(label) ? label.replace(/_/g, " ") : label;
}

/** Singular or plural: plural(1, "post") -> "1 post". */
export function plural(n: number, word: string, many = `${word}s`): string {
  return `${formatInt(n)} ${n === 1 ? word : many}`;
}
