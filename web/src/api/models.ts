import type { paths } from "./types";

// Types are read through `paths`, so they survive the swap from the
// hand-written types.ts to the openapi-typescript output whatever the
// backend names its schemas.
type JsonOk<P extends keyof paths> = paths[P]["get"]["responses"][200]["content"]["application/json"];

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
