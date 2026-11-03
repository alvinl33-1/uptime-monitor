export type OverallState = | "operational" | "degraded" | "partial_outage" | "major_outage" | "no_data";

export type MonitorState = | "up" | "degraded" | "down" | "unknown";

export interface MonitorStatus {
  id: number;
  name: string;
  status: MonitorState;
  uptime_24h: number | null;
  response_time_ms: number | null;
  last_checked_at: string | null;
}

export interface OpenIncident {
  id: number;
  monitor_id: number;
  monitor_name: string;
  started_at: string;
}

export interface StatusResponse {
  overall: OverallState;
  generated_at: string;
  monitors: MonitorStatus[];
  open_incidents: OpenIncident[];
}
