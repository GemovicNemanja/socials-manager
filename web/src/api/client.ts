import createClient from "openapi-fetch";
import type { paths } from "./types";

// Same origin: in production FastAPI serves the SPA and /api together;
// in dev the Vite server proxies /api to 127.0.0.1:8765.
export const api = createClient<paths>({ baseUrl: "" });

export class ApiError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Unwraps an openapi-fetch result, throwing ApiError on any failure. */
export async function unwrap<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result: Awaited<typeof request>;
  try {
    result = await request;
  } catch (cause) {
    throw new ApiError(cause instanceof Error ? cause.message : "Network error", null);
  }
  const { data, response } = result;
  if (!response.ok || data === undefined) {
    throw new ApiError(`${response.status} ${response.statusText}`.trim(), response.status);
  }
  return data;
}
