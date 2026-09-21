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
  /**
   * Why a metric takes no interval, set by the catalog exactly when interval is
   * null. The two are complements: a metric carries one or the other, never both
   * and never neither.
   */
  interval_note: string | null;
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

export interface BenchRow {
  panel: string;
  query: string;
  file: string;
  rows: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  file_bytes: number;
  row_groups: number;
  row_groups_skipped: number;
  browser_bytes: number | null;
  browser_requests: number | null;
}

export interface SourceWindow {
  start: string;
  end: string;
  detail_month: string;
}

/**
 * The build's own record of what it published. The documents are rendered from
 * this file, so the page reads provenance from it too rather than deriving the
 * same facts a second way and risking a disagreement.
 */
export interface Manifest {
  generated_at: string;
  commit: string;
  branch: string;
  backend: string[];
  window: SourceWindow;
  claims: Record<string, string | number>;
  quarantine_by_rule: { rule: string; rows: number; share_pct: number }[];
  shipped: { file: string; rows: number; bytes: number }[];
  bench: BenchRow[];
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
  /** Relative level change a candidate must clear before it is annotated. */
  min_effect: number;
  /** Candidates PELT returned, before the effect floor. */
  found: number;
  /** Candidates dropped for moving the level by less than min_effect. */
  below_effect_floor: number;
  /** What the search ran on, in the catalog's own words. */
  searched_on: string;
  stl_period: number;
  detected: Record<string, Changepoint[]>;
  generated_for_window: [string, string];
  generated_at: string;
}

export interface BootstrapDaily {
  d: string;
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
  trend: { d: string; trend: number }[];
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
