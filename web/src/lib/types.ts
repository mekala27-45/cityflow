export type MetricKind = 'sum' | 'mean' | 'ratio' | 'proportion';
export type IntervalMethod = 'wilson' | 'student-t' | null;

export interface Metric {
  name: string;
  kind: MetricKind;
  description: string;
  grain: string[];
  unit: string;
  format: string;
  interval: IntervalMethod;
  filters: string[];
  owner: string;
  models: string[];
  components: string[];
  warehouse_sql: string;
  browser_sql: string;
  numerator: string | null;
  denominator: string | null;
}

export interface LineageNode {
  name: string;
  type: string;
  layer: string;
  description: string;
  depends_on: string[];
}

export interface LineageExposure {
  name: string;
  label: string;
  description: string;
  depends_on: string[];
  url: string;
  owner: string;
}

export interface Lineage {
  available: boolean;
  nodes: Record<string, LineageNode>;
  exposures: LineageExposure[];
}

export interface Changepoint {
  date: string;
  index: number;
  before_mean: number;
  after_mean: number;
  relative_change: number;
  direction: 'up' | 'down';
}

export interface Changepoints {
  q: number;
  min_size_days: number;
  stl_period: number;
  detected: Record<string, Changepoint[]>;
  generated_for_window: [string, string];
  generated_at: string;
}

export interface BootstrapDaily {
  d: string;
  day_type: string;
  holiday: boolean;
  trips: number;
  revenue: number;
  mean_duration_min: number;
  tip_rate: number;
  airport_share: number;
}

export interface Bootstrap {
  generated_at: string;
  metrics: string[];
  daily: BootstrapDaily[];
  trend: { d: string; trend: number; observed: number }[];
  hour_of_week: { dow: number; day: string; hour: number; trips: number }[];
  top_zones: { zone_id: number; zone: string; borough: string; trips: number; rank: number }[];
  unknown_zone_share: number;
  provenance: {
    backend: string;
    min_period: string | null;
    max_period: string | null;
    periods: number;
    gaps: number;
    source_rows: number;
    clean_rows: number;
    quarantined_rows: number;
  };
  services: { service: string; service_label: string; service_description: string }[];
}

export interface ZoneFeatureProperties {
  zone_id: number;
  zone: string;
  borough: string;
  service_zone: string;
}
