import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import type { PostsStatusFilter } from "./models";

export const PAGE_SIZE = 50;

export const queryKeys = {
  shell: ["shell"] as const,
  posts: (status: PostsStatusFilter) => ["posts", { status }] as const,
};

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
