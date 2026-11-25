import { useSyncExternalStore } from "react";

/**
 * Hash routes (#/posts) so the FastAPI process can serve index.html at /
 * without a catch-all route.
 */
export const routes = ["overview", "posts", "account", "forecasts", "experiments"] as const;
export type Route = (typeof routes)[number];

export const DEFAULT_ROUTE: Route = "posts";

export function href(route: Route): string {
  return `#/${route}`;
}

function read(): Route {
  const name = window.location.hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  return (routes as readonly string[]).includes(name ?? "") ? (name as Route) : DEFAULT_ROUTE;
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

export function useRoute(): Route {
  return useSyncExternalStore(subscribe, read, () => DEFAULT_ROUTE);
}
