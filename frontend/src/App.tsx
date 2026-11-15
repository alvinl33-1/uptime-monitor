import { overallLabel } from "./api";
import {
  formatDateTime,
  formatResponseTime,
  formatUptime,
  pluralize,
  type Tone,
} from "./format";
import type {
  MonitorState,
  MonitorStatus,
  OpenIncident,
  OverallState,
} from "./types";
import { useStatus } from "./useStatus";

const monitorStateLabels: Record<MonitorState, string> = {
  up: "Operational",
  degraded: "Degraded",
  down: "Down",
  unknown: "Awaiting first check",
};

const monitorStateDescriptions: Record<MonitorState, string> = {
  up: "Responding normally",
  degraded: "Recent check failed",
  down: "Open incident detected",
  unknown: "No check has completed",
};

function toneForOverall(state: OverallState | string): Tone {
  switch (state) {
    case "operational":
      return "good";
    case "degraded":
    case "partial_outage":
      return "warn";
    case "major_outage":
      return "bad";
    default:
      return "neutral";
  }
}

function toneForMonitor(state: MonitorState): Tone {
  switch (state) {
    case "up":
      return "good";
    case "degraded":
      return "warn";
    case "down":
      return "bad";
    default:
      return "neutral";
  }
}

function countStates(monitors: MonitorStatus[]) {
  return monitors.reduce(
    (counts, monitor) => {
      counts[monitor.status] += 1;
      return counts;
    },
    { up: 0, degraded: 0, down: 0, unknown: 0 } as Record<MonitorState, number>,
  );
}

function LoadingState() {
  return (
    <main className="page" aria-busy="true">
      <PageHeader />
      <section className="loading-panel" aria-label="Loading status">
        <div className="loading-line loading-line--wide" />
        <div className="loading-line" />
        <div className="loading-grid" aria-hidden="true">
          <div className="loading-card" />
          <div className="loading-card" />
        </div>
        <p className="sr-only">Loading current status.</p>
      </section>
    </main>
  );
}

function ErrorState() {
  return (
    <main className="page">
      <PageHeader />
      <section className="empty-state" role="alert">
        <p className="eyebrow">Status unavailable</p>
        <h2>We couldn&apos;t load the public status.</h2>
        <p>Try again in a moment. If the problem continues, the status service may be unavailable.</p>
      </section>
    </main>
  );
}

function PageHeader() {
  return (
    <header className="page-header">
      <div className="brand-lockup">
        <span className="brand-mark" aria-hidden="true">UM</span>
        <div>
          <p className="eyebrow">Public status</p>
          <h1>Uptime Monitor</h1>
        </div>
      </div>
      <p className="header-note">Service health at a glance</p>
    </header>
  );
}

function StatusBanner({
  overall,
  monitorCount,
  openIncidentCount,
}: {
  overall: OverallState;
  monitorCount: number;
  openIncidentCount: number;
}) {
  const tone = toneForOverall(overall);
  const incidentSummary = openIncidentCount === 0
    ? "No open incidents"
    : pluralize(openIncidentCount, "open incident");

  return (
    <section className="status-banner" data-tone={tone} aria-labelledby="overall-status-title">
      <div className="status-indicator" aria-hidden="true" />
      <div className="status-banner-copy">
        <p className="eyebrow">Current status</p>
        <h2 id="overall-status-title">{overallLabel(overall)}</h2>
        <p>{pluralize(monitorCount, "monitor")} tracked · {incidentSummary}</p>
      </div>
    </section>
  );
}

function Overview({ monitors, openIncidentCount }: { monitors: MonitorStatus[]; openIncidentCount: number }) {
  const counts = countStates(monitors);
  return (
    <section className="overview" aria-labelledby="overview-title">
      <div className="section-heading section-heading--compact">
        <div>
          <p className="eyebrow">Overview</p>
          <h2 id="overview-title">What is being watched</h2>
        </div>
      </div>
      <div className="metric-grid">
        <Metric label="Monitors" value={monitors.length.toString()} detail="Configured endpoints" />
        <Metric label="Operational" value={counts.up.toString()} detail="Responding normally" tone="good" />
        <Metric
          label="Needs attention"
          value={(counts.degraded + counts.down).toString()}
          detail={pluralize(openIncidentCount, "open incident")}
          tone={counts.down > 0 ? "bad" : counts.degraded > 0 ? "warn" : "neutral"}
        />
      </div>
    </section>
  );
}

function Metric({ label, value, detail, tone = "neutral" }: { label: string; value: string; detail: string; tone?: Tone }) {
  return (
    <article className="metric-card" data-tone={tone}>
      <p className="metric-label">{label}</p>
      <p className="metric-value">{value}</p>
      <p className="metric-detail">{detail}</p>
    </article>
  );
}

function MonitorCard({ monitor }: { monitor: MonitorStatus }) {
  const tone = toneForMonitor(monitor.status);
  return (
    <li className="monitor-card" data-tone={tone}>
      <div className="monitor-card-header">
        <div>
          <h3>{monitor.name}</h3>
          <p>{monitorStateDescriptions[monitor.status]}</p>
        </div>
        <span className="status-badge" data-tone={tone}>
          <span className="status-dot" aria-hidden="true" />
          {monitorStateLabels[monitor.status]}
        </span>
      </div>
      <dl className="monitor-metrics">
        <div>
          <dt>24h uptime</dt>
          <dd>{formatUptime(monitor.uptime_24h)}</dd>
        </div>
        <div>
          <dt>Response time</dt>
          <dd>{formatResponseTime(monitor.response_time_ms)}</dd>
        </div>
        <div>
          <dt>Last checked</dt>
          <dd>{formatDateTime(monitor.last_checked_at)}</dd>
        </div>
      </dl>
    </li>
  );
}

function MonitorSection({ monitors }: { monitors: MonitorStatus[] }) {
  return (
    <section className="content-section" aria-labelledby="monitors-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Coverage</p>
          <h2 id="monitors-title">Monitors</h2>
        </div>
        <p className="section-count">{pluralize(monitors.length, "endpoint")}</p>
      </div>
      {monitors.length > 0 ? (
        <ul className="monitor-list">
          {monitors.map((monitor) => <MonitorCard key={monitor.id} monitor={monitor} />)}
        </ul>
      ) : (
        <p className="empty-state empty-state--small">No monitors have been configured yet.</p>
      )}
    </section>
  );
}

function IncidentSection({ incidents }: { incidents: OpenIncident[] }) {
  return (
    <section className="content-section" aria-labelledby="incidents-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Attention</p>
          <h2 id="incidents-title">Open incidents</h2>
        </div>
        <p className="section-count">{pluralize(incidents.length, "incident")}</p>
      </div>
      {incidents.length > 0 ? (
        <ul className="incident-list">
          {incidents.map((incident) => <IncidentCard key={incident.id} incident={incident} />)}
        </ul>
      ) : (
        <p className="empty-state empty-state--small">No active incidents. Everything is quiet.</p>
      )}
    </section>
  );
}

function IncidentCard({ incident }: { incident: OpenIncident }) {
  return (
    <li className="incident-card" data-tone="bad">
      <span className="status-dot" aria-hidden="true" />
      <div>
        <h3>{incident.monitor_name}</h3>
        <p>Incident opened {formatDateTime(incident.started_at)}</p>
      </div>
    </li>
  );
}

function App() {
  const { data, error, loading } = useStatus();

  if (loading && !data) return <LoadingState />;
  if (!data) return <ErrorState />;

  return (
    <main className="page">
      <PageHeader />
      {error && (
        <div className="notice" role="status">
          <strong>Showing the last known status.</strong> The latest refresh failed; we&apos;ll keep trying automatically.
        </div>
      )}
      <StatusBanner
        overall={data.overall}
        monitorCount={data.monitors.length}
        openIncidentCount={data.open_incidents.length}
      />
      <Overview monitors={data.monitors} openIncidentCount={data.open_incidents.length} />
      <MonitorSection monitors={data.monitors} />
      <IncidentSection incidents={data.open_incidents} />
      <footer className="page-footer">
        <p>Status refreshes automatically every 30 seconds.</p>
        <p>Last generated {formatDateTime(data.generated_at)}.</p>
      </footer>
    </main>
  );
}

export default App;
