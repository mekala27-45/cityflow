// The metric layer owns every aggregate definition. The dashboard reads
// metric_catalog.json at runtime and splices the expression the catalog already
// compiled, so a change to a definition in dbt reaches the page without anyone
// editing TypeScript. Nothing here writes an aggregate expression by hand, and
// that is deliberate: a second copy of "tip sum over observable fare sum" living
// in a React component is how two numbers with the same label start to disagree.

import type { Metric } from './types';

const BROWSER_SHAPE = /^\s*select\s+([\s\S]+?)\s+from\s+agg\s*$/i;
const WAREHOUSE_SHAPE = /^\s*select\s+([\s\S]+?)\s+from\s+fct_trip\s*$/i;

export class MetricCatalog {
  private readonly byName: Map<string, Metric>;

  constructor(readonly metrics: Metric[]) {
    this.byName = new Map(metrics.map((m) => [m.name, m]));
  }

  get(name: string): Metric {
    const metric = this.byName.get(name);
    if (!metric) throw new Error(`metric "${name}" is not in the catalog`);
    return metric;
  }

  has(name: string): boolean {
    return this.byName.has(name);
  }

  names(): string[] {
    return this.metrics.map((m) => m.name);
  }

  /**
   * The projection the catalog compiled, lifted out of its select wrapper so
   * several metrics can share one scan. Returns "expr as name" verbatim.
   */
  projection(name: string): string {
    const metric = this.get(name);
    const match = BROWSER_SHAPE.exec(metric.browser_sql);
    if (!match || !match[1]) {
      throw new Error(`metric "${name}" has a browser_sql shape this splicer cannot lift`);
    }
    return match[1];
  }

  /** Comma separated projections for a multi metric select over a CTE named agg. */
  projections(names: readonly string[]): string {
    return names.map((n) => this.projection(n)).join(',\n       ');
  }

  /**
   * The warehouse form of the same definition, for the one relation that is row
   * level rather than pre aggregated: detail_2024_06. Falls back to the browser
   * form for metrics whose warehouse_sql does not read fct_trip directly.
   */
  rowLevelProjection(name: string): string | null {
    const metric = this.get(name);
    const match = WAREHOUSE_SHAPE.exec(metric.warehouse_sql);
    return match && match[1] ? match[1] : null;
  }
}

/** A metric whose catalog entry names an interval method can show one. */
export function hasInterval(metric: Metric): boolean {
  return metric.interval !== null;
}

export function intervalNote(metric: Metric): string {
  switch (metric.interval) {
    case 'wilson':
      return 'Wilson score interval, 95 percent';
    case 'student-t':
      return 'Student t interval, 95 percent';
    default:
      return 'the catalog defines no interval method for this metric';
  }
}
