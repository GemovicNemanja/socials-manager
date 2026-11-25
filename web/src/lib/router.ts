import { useSyncExternalStore } from "react";

/**
 * Hash routes (#/posts) so the FastAPI process can serve index.html at /
 * without a catch-all route. A route may carry one sub-path: #/posts/129
 * keeps the post whose drawer is open.
 */
export const routes = ["overview", "posts", "account", "forecasts", "experiments"] as const;
export type Route = (typeof routes)[number];

export const DEFAULT_ROUTE: Route = "posts";

export function href(route: Route, sub?: string): string {
  return sub ? `#/${route}/${encodeURIComponent(sub)}` : `#/${route}`;
}

function segments(): string[] {
  return window.location.hash.replace(/^#\/?/, "").split("?")[0]!.split("/");
}

function read(): Route {
  const name = segments()[0];
  return (routes as readonly string[]).includes(name ?? "") ? (name as Route) : DEFAULT_ROUTE;
}

function readSub(): string | null {
  const sub = segments()[1];
  return sub ? decodeURIComponent(sub) : null;
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

export function useRoute(): Route {
  return useSyncExternalStore(subscribe, read, () => DEFAULT_ROUTE);
}

/** The sub-path after the route (#/posts/129 -> "129"), or null. */
export function useRouteSub(): string | null {
  return useSyncExternalStore(subscribe, readSub, () => null);
}

export function navigate(route: Route, sub?: string): void {
  window.location.hash = href(route, sub);
}
