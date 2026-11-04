import type { OverallState, StatusResponse } from "./types";

export const API_BASE = "/api"


/**
 * Fetch the public status document.
 *
 * Spec:
 * - GET `${API_BASE}/status` using fetch, passing { signal } through so the
 *   caller can cancel, and an Accept: application/json header.
 * - If response.ok is false, throw new Error(`Status request failed (${response.status})`).
 * - Parse the body with response.json().
 * - Light validation: if the result isn't an object, or `overall` isn't a
 *   string, or `monitors` isn't an array, throw new Error("Unexpected response").
 *   (This guards against an HTML error page or a proxy message sneaking in.)
 * - Return the parsed data typed as StatusResponse.
 */
export async function fetchStatus(signal?: AbortSignal): Promise<StatusResponse> {
  const response = await fetch(`${API_BASE}/status`, { signal });
  if (!response.ok) {
    throw new Error(`Failed to fetch status: ${response.status}`);
  } else {
    const data = await response.json();
    if (typeof data !== "object" || typeof data.overall !== "string" || !Array.isArray(data.monitors)) {
      return Promise.reject(new Error("Unexpected response"));  
    }
    return data as StatusResponse;
  }
}


/**
 * Turn the overall state into the headline people read.
 *
 * Spec:
 * - "operational"    -> "All systems operational"
 * - "degraded"       -> "Degraded performance"
 * - "partial_outage" -> "Partial outage"
 * - "major_outage"   -> "Major outage"
 * - "no_data"        -> "No data yet"
 * - anything else    -> "Unknown"  (the server may add states someday;
 *                       the page must not crash on one)
 * - Pure function: no fetch, no React.
 */
export function overallLabel(state: OverallState | string): string {
  switch (state) {
    case "operational":
      return "All systems operational";
    case "degraded":
      return "Degraded performance";
    case "partial_outage":
      return "Partial outage";
    case "major_outage":
      return "Major outage";
    case "no_data":
      return "No data yet";
    default:
      return "Unknown";
  }
}