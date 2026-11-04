import { overallLabel } from "./api";
import { useStatus } from "./useStatus";

/**
 * Spec:
 * - Call useStatus() to get { data, error, loading }.
 * - Always render <main> with an <h1>Uptime Monitor</h1>.
 * - If loading and there's no data yet: show "Loading…".
 * - If there's an error and no data: show "Could not load status."
 * - If there's data: show
 *     * the headline from overallLabel(data.overall) in an <h2>
 *     * a line like "3 monitors, 1 open incident" (use correct singular
 *       and plural: "1 monitor", "2 monitors", "0 open incidents")
 * - If there's BOTH data and an error (a refresh failed): still show the
 *   data, plus a small note: "Showing the last known status. Refresh failed."
 * - No styling yet. Plain HTML elements only.
 */
function App() {
  const { data, error, loading } = useStatus();

  return (
    <main>
      <h1>Uptime Monitor</h1>
      {loading && !data && <p>Loading…</p>}
      {!loading && error && !data && <p>Could not load status.</p>}
      {data && (
        <>
          <h2>{overallLabel(data.overall)}</h2>
          <p>
            {data.monitors.length}{" "}
            {data.monitors.length === 1 ? "monitor" : "monitors"},{" "}
            {data.open_incidents.length} open{" "}
            {data.open_incidents.length === 1 ? "incident" : "incidents"}
          </p>
          {error && (
            <small>Showing the last known status. Refresh failed.</small>
          )}
        </>
      )}
    </main>
  );
}

export default App;