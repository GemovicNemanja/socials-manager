const intFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

export const EMPTY = "—";

/** 3904 -> "3,904" */
export function formatInt(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? EMPTY : intFormat.format(Math.round(value));
}

/** 29.43 -> "29.4" */
export function formatDecimal(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? EMPTY : oneDecimal.format(value);
}

/** Estimated 48h impressions; "≈" marks a provisional (under 48h) estimate. */
export function formatI48(i48: { value: number | null; provisional: boolean }): string {
  if (i48.value == null) return EMPTY;
  return `${i48.provisional ? "≈" : ""}${formatInt(i48.value)}`;
}

/** Hours under 48 as "21h", older as days with one decimal, "2.9d". */
export function formatAge(hours: number | null | undefined): string {
  if (hours == null || !Number.isFinite(hours)) return EMPTY;
  if (hours < 48) return `${Math.max(0, Math.round(hours))}h`;
  return `${oneDecimal.format(hours / 24)}d`;
}

/** 4.12 -> "$4.12", 15 -> "$15" */
export function formatUsd(value: number): string {
  const whole = Number.isInteger(value);
  return `$${value.toLocaleString("en-US", {
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: 2,
  })}`;
}

function part(parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes): string {
  return parts.find((p) => p.type === type)?.value ?? "";
}

/**
 * An ISO UTC timestamp shown in the account's zone, labelled:
 * "Nov 25, 06:00 CET". Falls back to the browser zone if tz is invalid.
 */
export function formatDateTime(iso: string, tz: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return EMPTY;
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(date);
    // en-GB gives abbreviations such as CET where en-US gives GMT+1.
    const zone = part(
      new Intl.DateTimeFormat("en-GB", { timeZone: tz, timeZoneName: "short" }).formatToParts(date),
      "timeZoneName",
    );
    const day = `${part(parts, "month")} ${part(parts, "day")}`;
    const time = `${part(parts, "hour")}:${part(parts, "minute")}`;
    return `${day}, ${time} ${zone}`.trim();
  } catch {
    return date.toLocaleString("en-US");
  }
}

/** "narrated_video" -> "Narrated video"; display strings pass through. */
export function formatLabel(value: string | null | undefined): string {
  if (!value) return EMPTY;
  if (!/^[a-z0-9_]+$/.test(value)) return value;
  const spaced = value.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
