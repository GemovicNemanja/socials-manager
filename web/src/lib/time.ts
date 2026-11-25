/**
 * Wall-clock times in the account's IANA zone, for <input type="datetime-local"> values
 * ("2026-11-26T13:30"), which carry no zone of their own.
 */

function validZone(tz: string): string {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return tz;
  } catch {
    return "UTC";
  }
}

/** Milliseconds the zone is ahead of UTC at the given instant. */
function offsetMs(instant: number, tz: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: validZone(tz),
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(new Date(instant));
  const get = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
  return asUtc - Math.floor(instant / 1000) * 1000;
}

/** "2026-11-26T13:30" in tz -> the UTC instant, or null if the value isn't a full date and time. */
export function zonedInputToUtc(value: string, tz: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!m) return null;
  const wall = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]));
  // Two passes settle the offset around daylight-saving changes.
  let utc = wall - offsetMs(wall, tz);
  utc = wall - offsetMs(utc, tz);
  return new Date(utc);
}

/** A UTC instant as a datetime-local value in tz. */
export function utcToZonedInput(date: Date, tz: string): string {
  return new Date(date.getTime() + offsetMs(date.getTime(), tz)).toISOString().slice(0, 16);
}

/** The zone's short name at an instant: "CET", "UTC", "GMT-5". */
export function zoneAbbreviation(date: Date, tz: string): string {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: validZone(tz), timeZoneName: "short" }).formatToParts(date);
  return parts.find((p) => p.type === "timeZoneName")?.value ?? tz;
}

/** A default planned time: at least two hours ahead, on the next half hour. */
export function defaultPlannedAt(now = new Date()): Date {
  const step = 30 * 60 * 1000;
  return new Date(Math.ceil((now.getTime() + 2 * 60 * 60 * 1000) / step) * step);
}

/** "2:05" for 125 seconds. */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
