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
