import type { paths } from "./types";

// Types are read through `paths`, so they survive the swap from the
// hand-written types.ts to the openapi-typescript output whatever the
// backend names its schemas.
type JsonOk<P extends keyof paths> = paths[P] extends {
  get: { responses: { 200: { content: { "application/json": infer T } } } };
}
  ? T
  : never;

export type ShellResponse = JsonOk<"/api/shell">;
export type Account = ShellResponse["account"];
export type LastRun = NonNullable<ShellResponse["last_run"]>;
export type Spend = ShellResponse["spend"];

export type PostsResponse = JsonOk<"/api/posts">;
export type PostItem = PostsResponse["items"][number];
export type PostStatus = PostItem["status"];
export type PostsQuery = NonNullable<paths["/api/posts"]["get"]["parameters"]["query"]>;
export type PostsStatusFilter = NonNullable<PostsQuery["status"]>;
export type StatusCounts = PostsResponse["counts_by_status"];

export type OverviewResponse = JsonOk<"/api/overview">;
export type OverviewTiles = OverviewResponse["tiles"];
export type Kpi = OverviewTiles["impressions_7d"];
export type FollowerPoint = OverviewResponse["follower_series"][number];

export type PostDetailResponse = JsonOk<"/api/posts/{post_id}">;
export type ForecastDetail = NonNullable<PostDetailResponse["forecast"]>;
export type ScoreDetail = NonNullable<PostDetailResponse["score"]>;
export type SnapshotPoint = PostDetailResponse["snapshots"][number];
export type PostLabel = PostDetailResponse["labels"][number];

type PostOk<P extends keyof paths> = paths[P] extends {
  post: { responses: { 200: { content: { "application/json": infer T } } } };
}
  ? T
  : never;
type PostBody<P extends keyof paths> = paths[P] extends {
  post: { requestBody: { content: { "application/json": infer T } } };
}
  ? T
  : never;

export type BriefingResponse = JsonOk<"/api/briefing">;
export type BriefingData = NonNullable<BriefingResponse["briefing"]>;
export type NotablePost = BriefingData["notable"][number];
export type Confidence = BriefingResponse["confidence"];

export type RunStatusResponse = JsonOk<"/api/runs/status">;
export type RunStatus = NonNullable<RunStatusResponse["latest"]>;
export type RunStep = RunStatus["steps"][number];
export type RunFailure = NonNullable<RunStatusResponse["failure"]>;
export type RunStartResponse = PostOk<"/api/runs">;

export type ScheduleResponse = JsonOk<"/api/planned">;
export type PlannedSlot = ScheduleResponse["slots"][number];
export type PlanRequest = PostBody<"/api/planned/draft">;
export type RegisterRequest = PostBody<"/api/planned">;
export type DraftResponse = PostOk<"/api/planned/draft">;
export type RegisterResponse = PostOk<"/api/planned">;
export type GuardrailHit = NonNullable<DraftResponse["guardrails"]>[number];
export type PlannedPostType = PlanRequest["post_type"];
export type PlannedFormat = NonNullable<PlanRequest["post_format"]>;
export type PlannedTopic = NonNullable<PlanRequest["topic"]>;

export type ExperimentsResponse = JsonOk<"/api/experiments">;
export type ExperimentSummary = ExperimentsResponse["running"][number];
export type Suggestion = ExperimentsResponse["suggestions"][number];
export type ApprovalCard = ExperimentsResponse["approvals"][number];
export type ExperimentDetail = JsonOk<"/api/experiments/{key}">;
export type ReadoutView = NonNullable<ExperimentDetail["readout"]>;
export type FactorReadout = ReadoutView["factors"][number];
export type FactorPast = ExperimentDetail["past"][number];
export type SlotRow = ExperimentDetail["slots"][number];
export type SlotArm = SlotRow["arms"][number];
export type NextPostsResponse = JsonOk<"/api/next-posts">;
export type ProposeRequest = PostBody<"/api/experiments">;
export type CustomSpec = NonNullable<ProposeRequest["custom"]>;
export type ExperimentAction = PostOk<"/api/experiments">;
export type RegisterSlotsRequest = PostBody<"/api/experiments/slots">;
export type DayPlanIn = RegisterSlotsRequest["days"][number];
export type RegisterSlotsResponse = PostOk<"/api/experiments/slots">;
export type DecideResponse = PostOk<"/api/approvals/{approval_id}/approve">;
