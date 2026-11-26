import { useSyncExternalStore } from "react";

/**
 * Hash routes, so GitHub Pages serves every page from one index.html with no rewrites:
 * #/ · #/notes/week-0 · #/experiments · #/experiments/e03 · #/method
 */
export type JournalRoute =
  | { page: "home" }
  | { page: "entry"; slug: string }
  | { page: "experiments" }
  | { page: "experiment"; slug: string }
  | { page: "method" }
  | { page: "not-found"; path: string };

export const paths = {
  home: "#/",
  entry: (slug: string) => `#/notes/${encodeURIComponent(slug)}`,
  experiments: "#/experiments",
  experiment: (slug: string) => `#/experiments/${encodeURIComponent(slug.toLowerCase())}`,
  method: "#/method",
} as const;

export function parseHash(hash: string): JournalRoute {
  const path = hash.replace(/^#\/?/, "").split("?")[0]!.replace(/\/+$/, "");
  const [head, sub, ...rest] = path.split("/");
  if (rest.length) return { page: "not-found", path };
  if (!head) return { page: "home" };
  if (head === "notes" && sub) return { page: "entry", slug: decodeURIComponent(sub) };
  if (head === "notes" && !sub) return { page: "home" };
  if (head === "experiments") return sub ? { page: "experiment", slug: decodeURIComponent(sub) } : { page: "experiments" };
  if (head === "method" && !sub) return { page: "method" };
  return { page: "not-found", path };
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

const readHash = () => window.location.hash;

export function useJournalRoute(): JournalRoute {
  const hash = useSyncExternalStore(subscribe, readHash, () => "");
  return parseHash(hash);
}
