import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, unwrap } from "./client";
import type {
  GoalRequest,
  PlanRequest,
  PostsStatusFilter,
  ProposeRequest,
  RegisterRequest,
  RegisterSlotsRequest,
} from "./models";

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
  experiments: ["experiments"] as const,
  experiment: (key: string) => ["experiments", key] as const,
  nextPosts: ["next-posts"] as const,
  model: ["model"] as const,
  followers: ["followers"] as const,
  goals: ["goals"] as const,
  goalPreview: (req: GoalRequest | null) => ["goals", "preview", req] as const,
};

/** The Forecasts screen: hit rates, skill, calibration, drift, PIT, model card, gates, changes, monitors, alarms. */
export function useModelHealth() {
  return useQuery({
    queryKey: queryKeys.model,
    queryFn: ({ signal }) => unwrap(api.GET("/api/model", { signal })),
    refetchInterval: 60_000,
  });
}

/** Acknowledge a detector alarm; nothing else changes. */
export function useAcknowledgeAlarm() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, note }: { id: number; note?: string }) =>
      unwrap(api.POST("/api/alarms/{alarm_id}/ack", { params: { path: { alarm_id: id } }, body: note ? { note } : undefined })),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.model }),
  });
}

/** Follower tiles, actuals per run, the latest frozen follower forecast, funnel, goal and scored past forecasts. */
export function useFollowers() {
  return useQuery({
    queryKey: queryKeys.followers,
    queryFn: ({ signal }) => unwrap(api.GET("/api/followers", { signal })),
    refetchInterval: 60_000,
  });
}

/** Current goals with their status, and every goal ever set. */
export function useGoals() {
  return useQuery({
    queryKey: queryKeys.goals,
    queryFn: ({ signal }) => unwrap(api.GET("/api/goals", { signal })),
    refetchInterval: 60_000,
  });
}

/** Where you'd stand today against an unsaved goal; idle while req is null. */
export function useGoalPreview(req: GoalRequest | null) {
  return useQuery({
    queryKey: queryKeys.goalPreview(req),
    queryFn: ({ signal }) => unwrap(api.POST("/api/goals/preview", { body: req!, signal })),
    enabled: req != null,
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: 60_000,
  });
}

/** Save a goal: supersedes the scope's current goal. A refusal comes back as saved=false with a summary. */
export function useSetGoal() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: GoalRequest) => unwrap(api.POST("/api/goals", { body })),
    onSuccess: (data) => {
      if (!data.saved) return;
      for (const key of [queryKeys.goals, queryKeys.followers, queryKeys.model, queryKeys.overview]) {
        void client.invalidateQueries({ queryKey: key });
      }
    },
  });
}

/** Running, waiting and concluded experiments, E01-E09 with power, pending approvals. */
export function useExperiments() {
  return useQuery({
    queryKey: queryKeys.experiments,
    queryFn: ({ signal }) => unwrap(api.GET("/api/experiments", { signal })),
    refetchInterval: 60_000,
  });
}

/** One experiment: protocol, slots with arms, readouts, guardrails. Idle while key is null. */
export function useExperiment(key: string | null) {
  return useQuery({
    queryKey: queryKeys.experiment(key ?? ""),
    queryFn: ({ signal }) => unwrap(api.GET("/api/experiments/{key}", { params: { path: { key: key! } }, signal })),
    enabled: key != null,
    retry: (count, error) => count < 1 && !(error instanceof ApiError && error.status === 404),
  });
}

/** Registered slots and planned posts for the next days (Account, "Next posts"). */
export function useNextPosts(days = 7) {
  return useQuery({
    queryKey: queryKeys.nextPosts,
    queryFn: ({ signal }) => unwrap(api.GET("/api/next-posts", { params: { query: { days } }, signal })),
    refetchInterval: 60_000,
  });
}

/** After any experiment write: experiments, next posts, the planned schedule and the shell count. */
function useInvalidateExperiments() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: queryKeys.experiments });
    void client.invalidateQueries({ queryKey: queryKeys.nextPosts });
    void client.invalidateQueries({ queryKey: queryKeys.schedule });
    void client.invalidateQueries({ queryKey: queryKeys.shell });
  };
}

/** Start a suggestion or a custom experiment as the owner. */
export function useProposeExperiment() {
  const invalidate = useInvalidateExperiments();
  return useMutation({
    mutationFn: (body: ProposeRequest) => unwrap(api.POST("/api/experiments", { body })),
    onSuccess: invalidate,
  });
}

/** Register whole days of slots: blind forecasts freeze, then arms are drawn. */
export function useRegisterSlots() {
  const invalidate = useInvalidateExperiments();
  return useMutation({
    mutationFn: (body: RegisterSlotsRequest) => unwrap(api.POST("/api/experiments/slots", { body })),
    onSuccess: invalidate,
  });
}

/** Approve or reject a pending item as the owner. */
export function useDecideApproval() {
  const invalidate = useInvalidateExperiments();
  return useMutation({
    mutationFn: ({ id, decision, note }: { id: number; decision: "approve" | "reject"; note?: string }) => {
      const params = { path: { approval_id: id } };
      const body = note ? { note } : undefined;
      return unwrap(
        decision === "approve"
          ? api.POST("/api/approvals/{approval_id}/approve", { params, body })
          : api.POST("/api/approvals/{approval_id}/reject", { params, body }),
      );
    },
    onSuccess: invalidate,
  });
}

/** Stop an experiment for good, or take its one extension. */
export function useExperimentAction() {
  const invalidate = useInvalidateExperiments();
  return useMutation({
    mutationFn: ({ key, action, reason }: { key: string; action: "stop" | "extend"; reason?: string }) => {
      const params = { path: { key } };
      return unwrap(
        action === "stop"
          ? api.POST("/api/experiments/{key}/stop", { params, body: { reason: reason ?? "stopped by the owner" } })
          : api.POST("/api/experiments/{key}/extend", { params }),
      );
    },
    onSuccess: invalidate,
  });
}

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
        void client.invalidateQueries({ queryKey: queryKeys.nextPosts });
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
