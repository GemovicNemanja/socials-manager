import type { ExpectationTileProps, ExpectationTileReading } from "../components/ExpectationTile";
import { formatPercentile } from "./format";

/** MODEL §4.1 thresholds, applied to a value's percentile under its predictive distribution. */
export function readingOf(percentile: number): ExpectationTileReading {
  if (percentile >= 0.8) return "above";
  if (percentile <= 0.2) return "below";
  return "default";
}

/** The verbal outcome of a value against its expected range, coloured like the post status pills. */
export const OUTCOME: Record<"above" | "default" | "below", NonNullable<ExpectationTileProps["outcome"]>> = {
  above: { label: "Above expected", status: "working" },
  default: { label: "As expected", status: "insufficient" },
  below: { label: "Below expected", status: "not-working" },
};

/** Percentile 0.1..0.9 maps onto the band (0.2..0.8 of the strip). */
export function stripPosition(percentile: number): number {
  return 0.2 + (percentile - 0.1) * 0.75;
}

/** The tile fields that read a past value against its frozen expected range. */
export function expectedFields(
  percentile: number,
  range: string,
): Pick<ExpectationTileProps, "percentile" | "range" | "reading" | "position" | "outcome"> {
  const reading = readingOf(percentile);
  return {
    percentile: formatPercentile(percentile),
    range,
    reading,
    position: stripPosition(percentile),
    outcome: OUTCOME[reading === "above" || reading === "below" ? reading : "default"],
  };
}
