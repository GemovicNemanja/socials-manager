/** Canvas swatch tokens (dashboard/positive ...) used by legends and count rows. */
export type Swatch = "positive" | "negative" | "faint" | "neutral" | "accent" | "band" | "warn";

export const swatchBg: Record<Swatch, string> = {
  positive: "bg-positive",
  negative: "bg-negative",
  faint: "bg-faint",
  neutral: "bg-neutral",
  accent: "bg-accent",
  band: "bg-band",
  warn: "bg-warn",
};
