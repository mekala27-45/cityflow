// The metric layer owns every aggregate definition. The dashboard reads
// metric_catalog.json at runtime and splices the expression the catalog already
// compiled, so a change to a definition in dbt reaches the page without anyone
// editing TypeScript. Nothing here writes an aggregate expression by hand, and
// that is deliberate: a second copy of "tip sum over observable fare sum" living
// in a React component is how two numbers with the same label start to disagree.

import type { Metric } from './types';

const BROWSER_SHAPE = /^\s*select\s+([\s\S]+?)\s+from\s+agg\s*$/i;

export class MetricCatalog {
  private readonly byName: Map<string, Metric>;

  constructor(readonly metrics: Metric[]) {
    // A metric with no interval method and no reason for it cannot be published
    // by the current catalog, so seeing one means something upstream is broken
    // and the page should say so rather than quietly print a level with nothing
    // next to it.
    const unexplained = metrics.filter((m) => m.interval === null && !m.interval_note);
    if (unexplained.length > 0) {
      const names = unexplained.map((m) => m.name).join(', ');
      throw new Error(
        `metric_catalog.json is malformed: ${names} ${
          unexplained.length === 1 ? 'declares' : 'declare'
        } neither an interval method nor an interval_note.`,
      );
    }
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
}

/**
 * What to print next to a level: the method where the catalog names one, and the
 * catalog's own sentence explaining the absence where it does not. The page never
 * writes that explanation itself, because the reason a ratio of two totals takes
 * no interval is a property of the metric, not of the chart showing it.
 */
export function intervalNote(metric: Metric): string {
  switch (metric.interval) {
    case 'wilson':
      return 'Wilson score interval, 95 percent';
    case 'student-t':
      return 'Student t interval, 95 percent';
    default:
      return metric.interval_note ?? 'no interval method and no reason given, which the loader should have rejected';
  }
}
