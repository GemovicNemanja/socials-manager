import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api, ApiError, unwrap } from "./client";
import type { PostsStatusFilter } from "./models";

export const PAGE_SIZE = 50;

export const queryKeys = {
  shell: ["shell"] as const,
  posts: (status: PostsStatusFilter) => ["posts", { status }] as const,
  post: (id: string) => ["post", id] as const,
  overview: ["overview"] as const,
};

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
