import { useEffect, useState } from "react";
import { fetchStatus } from "./api";
import type { StatusResponse } from "./types";

export const POLL_MS = 30_000;

/**
 * A React hook that loads the status and keeps it fresh.
 *
 * Returns { data, error, loading }:
 *   data:    StatusResponse | null  (null until the first success)
 *   error:   string | null          (message from the latest failed attempt)
 *   loading: boolean                (true until the first attempt finishes)
 *
 * Spec:
 * - Keep three pieces of state: data (starts null), error (starts null),
 *   loading (starts true).
 * - useEffect that runs once (empty dependency list, plus pollMs):
 *     * create an AbortController
 *     * define an async load() that calls fetchStatus(controller.signal)
 *         - on success: setData(result), setError(null)
 *         - on failure: IGNORE the error if it is an AbortError (that just
 *           means we cancelled). Otherwise setError(the error's message).
 *           Do NOT clear data. Stale information is better than a blank page.
 *         - finally: setLoading(false)
 *     * call load() immediately, then again every pollMs with setInterval
 *     * the effect's cleanup function must clearInterval AND abort the
 *       controller, so nothing updates after the component is gone.
 *       (In development React runs effects twice on purpose, to check
 *       exactly this.)
 * - Why 30 seconds: the server caches status for 15 seconds, so polling
 *   faster just returns the same answer.
 */
export function useStatus(pollMs: number = POLL_MS): {
  data: StatusResponse | null;
  error: string | null;
  loading: boolean;
} {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const result = await fetchStatus(controller.signal);
        if (controller.signal.aborted) {
          return;
        }
        setData(result);
        setError(null);
      } catch (err) {
        if (controller.signal.aborted || (err instanceof Error && err.name === "AbortError")) {
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    };

    load();

    const interval = setInterval(load, pollMs);

    return () => {
      clearInterval(interval);
      controller.abort();
    };
  }, [pollMs]);

  return { data, error, loading };
}
