/** Shared chart helpers (the dashboard's FollowerChart and the public journal's charts). */

/** The axis top: the smallest step whose quarters are round numbers (ticks sit at quarters). */
export function niceMax(value: number): number {
  if (value <= 0) return 10;
  const step = 10 ** Math.floor(Math.log10(value));
  for (const m of [1, 2, 2.5, 4, 5, 6, 8, 10]) if (m * step >= value) return m * step;
  return 10 * step;
}

/** Powers of two that cover [lo, hi] on a log axis, at least [0.5, 2]: 0.25×, 0.5×, 1×, 2×, 4× ... */
export function log2Ticks(lo: number, hi: number): number[] {
  const a = Math.min(-1, Math.floor(Math.log2(Math.max(lo, 1e-6))));
  const b = Math.max(1, Math.ceil(Math.log2(Math.max(hi, 1e-6))));
  return Array.from({ length: b - a + 1 }, (_, i) => 2 ** (b - i));
}
