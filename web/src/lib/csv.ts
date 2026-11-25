import type { PostItem } from "../api/models";

const columns: [string, (p: PostItem) => string | number | boolean | null][] = [
  ["id", (p) => p.id],
  ["external_id", (p) => p.external_id],
  ["created_at_utc", (p) => p.created_at],
  ["type", (p) => p.type],
  ["format", (p) => p.format],
  ["topic", (p) => p.topic],
  ["age_h", (p) => p.age_h],
  ["i48", (p) => p.i48.value],
  ["i48_exact", (p) => p.i48.exact],
  ["i48_provisional", (p) => p.i48.provisional],
  ["likes_per_1k", (p) => p.likes_per_1k],
  ["forecast_q10", (p) => p.forecast?.q10 ?? null],
  ["forecast_q50", (p) => p.forecast?.q50 ?? null],
  ["forecast_q90", (p) => p.forecast?.q90 ?? null],
  ["forecast_kind", (p) => p.forecast?.kind ?? null],
  ["pit_mid", (p) => p.pit_mid],
  ["status", (p) => p.status],
  ["text", (p) => p.text],
];

function cell(value: string | number | boolean | null): string {
  if (value == null) return "";
  const s = String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function postsToCsv(posts: PostItem[]): string {
  const head = columns.map(([name]) => name).join(",");
  const rows = posts.map((p) => columns.map(([, get]) => cell(get(p))).join(","));
  return [head, ...rows].join("\r\n");
}

export function downloadText(filename: string, text: string, type = "text/csv;charset=utf-8"): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
