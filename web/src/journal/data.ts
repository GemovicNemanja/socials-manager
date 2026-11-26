import { useQuery } from "@tanstack/react-query";
import type { components } from "../api/types";

type Schemas = components["schemas"];
export type Journal = Schemas["JournalSnapshot"];
export type JournalEntry = Schemas["PublicEntry"];
export type JournalExperiment = Schemas["PublicExperiment"];
export type JournalPost = Schemas["PublicPostRow"];
export type JournalMethodSection = Schemas["MethodSection"];
export type ExperimentResult = JournalExperiment["result"];

/** Where the snapshot came from: the published site's data.json, or the local preview API. */
export type JournalSource = "static" | "preview";

export interface LoadedJournal {
  journal: Journal;
  source: JournalSource;
}

const STATIC_URL = "./data.json";
const PREVIEW_URL = "/api/journal?preview=true";

function isJson(response: Response): boolean {
  return (response.headers.get("content-type") ?? "").includes("json");
}

/**
 * The published site reads `./data.json` next to index.html. When there is none (the owner's local
 * preview at /journal/, served by `socialmgr serve`, or `pnpm dev:journal`), it falls back to the
 * preview API, which includes draft entries. A dev server answers unknown paths with index.html, so
 * a non-JSON answer counts as missing too.
 */
export async function loadJournal(fetcher: typeof fetch = fetch): Promise<LoadedJournal> {
  let staticError: string;
  try {
    const response = await fetcher(STATIC_URL, { cache: "no-cache" });
    if (response.ok && isJson(response)) return { journal: (await response.json()) as Journal, source: "static" };
    staticError = `data.json: HTTP ${response.status}`;
  } catch (error) {
    staticError = `data.json: ${String(error)}`;
  }
  const response = await fetcher(PREVIEW_URL);
  if (response.ok && isJson(response)) return { journal: (await response.json()) as Journal, source: "preview" };
  throw new Error(`${staticError}; preview API: HTTP ${response.status}`);
}

export function useJournal() {
  return useQuery({ queryKey: ["journal"], queryFn: () => loadJournal(), staleTime: Infinity });
}

export function findExperiment(journal: Journal, key: string): JournalExperiment | undefined {
  const k = key.toLowerCase();
  return journal.experiments.find((x) => x.slug === k || x.key.toLowerCase() === k);
}

export function findEntry(journal: Journal, slug: string): JournalEntry | undefined {
  return journal.entries.find((e) => e.slug === slug);
}
