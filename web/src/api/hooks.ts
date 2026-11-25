import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, unwrap } from "./client";
import type { PlanRequest, PostsStatusFilter, RegisterRequest } from "./models";

export const PAGE_SIZE = 50;
/** How often run status is polled while a run executes. */
export const RUN_POLL_MS = 750;

export const queryKeys = {
  shell: ["shell"] as const,
  posts: (status: PostsStatusFilter) => ["posts", { status }] as const,
  post: (id: string) => ["post", id] as const,
  overview: ["overview"] as const,
  briefing: ["briefing"] as const,
  runStatus: ["runs", "status"] as const,
  schedule: ["planned"] as const,
  draft: (req: PlanRequest | null) => ["planned", "draft", req] as const,
};

/** The latest run's briefing: summary, notable posts, caveats and spend. */
export function useBriefing() {
  return useQuery({
    queryKey: queryKeys.briefing,
    queryFn: ({ signal }) => unwrap(api.GET("/api/briefing", { signal })),
    refetchInterval: 60_000,
  });
}

/** Latest and unfinished run, step by step. Polls quickly while a run executes (here or elsewhere). */
export function useRunStatus(starting: boolean) {
  return useQuery({
    queryKey: queryKeys.runStatus,
    queryFn: ({ signal }) => unwrap(api.GET("/api/runs/status", { signal })),
    refetchInterval: (query) => (starting || query.state.data?.active ? RUN_POLL_MS : 60_000),
    staleTime: 0,
  });
}

/**
 * Start (or resume) a run. The request blocks until the run ends; the server finishes it even if
 * the page closes, and useRunStatus shows its steps meanwhile.
 */
export function useStartRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/runs")),
    onSuccess: (data) => client.setQueryData(queryKeys.runStatus, data.status),
    onSettled: () => client.invalidateQueries(),
  });
}

/** Planned posts: upcoming with frozen ranges, and the last week's posted or missed ones. */
export function useSchedule() {
  return useQuery({
    queryKey: queryKeys.schedule,
    queryFn: ({ signal }) => unwrap(api.GET("/api/planned", { signal })),
    refetchInterval: 60_000,
  });
}

/** Draft forecast and guardrails for a planned post; freezes nothing. Idle while req is null. */
export function useDraftForecast(req: PlanRequest | null) {
  return useQuery({
    queryKey: queryKeys.draft(req),
    queryFn: ({ signal }) => unwrap(api.POST("/api/planned/draft", { body: req!, signal })),
    enabled: req != null,
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: 60_000,
  });
}

/** Register a planned post: freezes its forecast now. */
export function useRegisterPlanned() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: RegisterRequest) => unwrap(api.POST("/api/planned", { body })),
    onSuccess: (data) => {
      if (data.registered) {
        void client.invalidateQueries({ queryKey: queryKeys.schedule });
        void client.invalidateQueries({ queryKey: queryKeys.overview });
      }
    },
  });
}

/** KPI tiles, follower series, status counts, recent posts and last run for the Overview screen. */
export function useOverview() {
  return useQuery({
    queryKey: queryKeys.overview,
    queryFn: ({ signal }) => unwrap(api.GET("/api/overview", { signal })),
    refetchInterval: 60_000,
  });
}

/** One post for the detail drawer; idle while no post is selected. */
export function usePostDetail(id: string | null) {
  return useQuery({
    queryKey: queryKeys.post(id ?? ""),
    queryFn: ({ signal }) => unwrap(api.GET("/api/posts/{post_id}", { params: { path: { post_id: id! } }, signal })),
    enabled: id != null,
    retry: (count, error) => count < 1 && !(error instanceof ApiError && error.status === 404),
  });
}

/** Account, last run, month spend and counts for the app shell. */
export function useShell() {
  return useQuery({
    queryKey: queryKeys.shell,
    queryFn: ({ signal }) => unwrap(api.GET("/api/shell", { signal })),
    refetchInterval: 60_000,
  });
}

/** Posts, newest first, paged with limit/offset. */
export function usePosts(status: PostsStatusFilter) {
  return useInfiniteQuery({
    queryKey: queryKeys.posts(status),
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      unwrap(
        api.GET("/api/posts", {
          params: { query: { limit: PAGE_SIZE, offset: pageParam, status } },
          signal,
        }),
      ),
    getNextPageParam: (lastPage, pages) => {
      const loaded = pages.reduce((n, page) => n + page.items.length, 0);
      return lastPage.items.length > 0 && loaded < lastPage.total ? loaded : undefined;
    },
    placeholderData: keepPreviousData,
  });
}
