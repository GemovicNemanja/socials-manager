import type { PostItem, PostStatus, PostsStatusFilter } from "../api/models";
import type { RangeStripReading } from "../components/RangeStrip";
import type { StatusPillStatus } from "../components/StatusPill";

export const POST_STATUS: Record<PostStatus, { label: string; pill: StatusPillStatus }> = {
  above: { label: "Above", pill: "working" },
  as_expected: { label: "As expected", pill: "insufficient" },
  below: { label: "Below", pill: "not-working" },
  provisional: { label: "Provisional", pill: "tracking" },
  no_forecast: { label: "No forecast", pill: "insufficient" },
};

export const STATUS_FILTERS: { value: PostsStatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "above", label: "Above" },
  { value: "as_expected", label: "As expected" },
  { value: "below", label: "Below" },
  { value: "provisional", label: "Provisional" },
];

/** Where the range strip puts its tick; null when there is nothing to draw. */
export function rangeReading(
  post: Pick<PostItem, "status" | "pit_mid" | "forecast">,
): RangeStripReading | null {
  if (post.status === "provisional") return "live";
  if (post.forecast == null || post.pit_mid == null) return null;
  const p = post.pit_mid;
  if (p < 0.1) return "below";
  if (p < 0.2) return "low";
  if (p < 0.8) return "default";
  if (p < 0.9) return "high";
  return "above";
}
