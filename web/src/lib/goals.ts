import type { GoalStatus, GoalsResponse } from "../api/models";
import type { BadgeTone } from "../components/Badge";
import type { StatusPillStatus } from "../components/StatusPill";
import type { Swatch } from "../components/swatch";
import { formatDay, formatInt } from "./format";

type StatusName = GoalStatus["status"];

/** MODEL §4.2 goal status in words, with the colours the canvas gives each. */
export const GOAL_STATUS: Record<StatusName, { label: string; badge: BadgeTone; pill: StatusPillStatus; fill: Swatch }> = {
  working: { label: "Working", badge: "positive", pill: "working", fill: "positive" },
  near: { label: "Near threshold", badge: "warn", pill: "default", fill: "warn" },
  not_working: { label: "Not working", badge: "negative", pill: "not-working", fill: "negative" },
  no_goal: { label: "No status yet", badge: "default", pill: "insufficient", fill: "neutral" },
};

/** "1,000 followers by Dec 31" or "3,000 impressions at 48h per post". */
export function describeGoal(goal: GoalStatus["goal"], tz: string): string {
  return goal.scope === "followers" && goal.target_at
    ? `${formatInt(goal.target)} followers by ${formatDay(goal.target_at, tz)}`
    : `${formatInt(goal.target)} impressions at 48h per post`;
}

/** Why a goal has no status yet, in plain words. */
export function goalReasonText(status: GoalStatus): string | null {
  if (status.status !== "no_goal") return null;
  switch (status.reason) {
    case "awaiting_run":
      return "The next run computes its status.";
    case "awaiting_model":
      return "The follower model hasn’t run yet.";
    case "insufficient_owner_data":
      return "Needs 20 matured posts before it reads working or not.";
    default:
      return status.reason ? status.reason.replace(/_/g, " ") : null;
  }
}

/** The goal a screen shows: the follower goal if one is set, else the per-post goal. */
export function primaryGoal(goals: GoalsResponse | undefined): GoalStatus | null {
  const current = goals?.current ?? [];
  return current.find((g) => g.goal.scope === "followers") ?? current[0] ?? null;
}
