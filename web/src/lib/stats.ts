// Inference over metric values, not metric definitions. Nothing here recomputes
// a metric: the inputs are either counts the catalog names as a numerator and a
// denominator, or a series of already computed daily metric values.

export interface Interval {
  point: number;
  lower: number;
  upper: number;
  method: string;
  n: number;
}

const Z95 = 1.959963984540054;

/**
 * Wilson score interval. Used where the catalog says a proportion's interval
 * method is wilson, which is everywhere a share of trips is shown.
 */
export function wilson(successes: number, trials: number, z = Z95): Interval | null {
  if (!Number.isFinite(successes) || !Number.isFinite(trials) || trials <= 0) return null;
  const p = successes / trials;
  const z2 = z * z;
  const denom = 1 + z2 / trials;
  const centre = (p + z2 / (2 * trials)) / denom;
  const spread = (z / denom) * Math.sqrt((p * (1 - p)) / trials + z2 / (4 * trials * trials));
  return {
    point: p,
    lower: Math.max(0, centre - spread),
    upper: Math.min(1, centre + spread),
    method: 'Wilson score, 95 percent',
    n: trials,
  };
}

function mean(values: readonly number[]): number {
  if (values.length === 0) return Number.NaN;
  let total = 0;
  for (const v of values) total += v;
  return total / values.length;
}

function variance(values: readonly number[]): number {
  if (values.length < 2) return Number.NaN;
  const m = mean(values);
  let total = 0;
  for (const v of values) total += (v - m) * (v - m);
  return total / (values.length - 1);
}

// Two tailed t quantile at 0.975. A lookup beats pulling an incomplete beta
// implementation in for a table that stops mattering above about thirty degrees
// of freedom, where it is already within a percent of the normal quantile.
const T975: Record<number, number> = {
  1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
  9: 2.262, 10: 2.228, 12: 2.179, 14: 2.145, 16: 2.12, 18: 2.101, 20: 2.086,
  25: 2.06, 30: 2.042, 40: 2.021, 60: 2.0, 120: 1.98,
};

export function tCritical(df: number): number {
  if (!Number.isFinite(df) || df < 1) return Z95;
  if (df >= 120) return Z95;
  const keys = Object.keys(T975).map(Number).sort((a, b) => a - b);
  for (const k of keys) {
    if (df <= k) return T975[k] ?? Z95;
  }
  return Z95;
}

/** Student t interval on the mean of a series of daily metric values. */
export function tInterval(values: readonly number[]): Interval | null {
  const clean = values.filter((v) => Number.isFinite(v));
  if (clean.length < 2) return null;
  const m = mean(clean);
  const se = Math.sqrt(variance(clean) / clean.length);
  const t = tCritical(clean.length - 1);
  return {
    point: m,
    lower: m - t * se,
    upper: m + t * se,
    method: `Student t on ${clean.length} daily values, 95 percent`,
    n: clean.length,
  };
}

export interface Delta {
  /** Relative change, current against prior. */
  relative: number;
  relativeLower: number;
  relativeUpper: number;
  absolute: number;
  method: string;
  currentN: number;
  priorN: number;
}

/**
 * Welch interval on the difference between two periods' daily means, expressed
 * as a relative change so it can sit next to a percentage in a tile. A count
 * over a closed period has no sampling error, but the question a delta answers
 * is whether the daily level moved, and that does.
 */
export function welchDelta(current: readonly number[], prior: readonly number[]): Delta | null {
  const a = current.filter((v) => Number.isFinite(v));
  const b = prior.filter((v) => Number.isFinite(v));
  if (a.length < 2 || b.length < 2) return null;
  const ma = mean(a);
  const mb = mean(b);
  if (!Number.isFinite(mb) || mb === 0) return null;
  const va = variance(a) / a.length;
  const vb = variance(b) / b.length;
  const se = Math.sqrt(va + vb);
  const df = (va + vb) ** 2 / (va ** 2 / (a.length - 1) + vb ** 2 / (b.length - 1));
  const t = tCritical(df);
  const diff = ma - mb;
  return {
    relative: diff / mb,
    relativeLower: (diff - t * se) / mb,
    relativeUpper: (diff + t * se) / mb,
    absolute: diff,
    method: `Welch t on daily means, ${a.length} against ${b.length} days, 95 percent`,
    currentN: a.length,
    priorN: b.length,
  };
}

export interface BoxSummary {
  min: number;
  q1: number;
  median: number;
  q3: number;
  max: number;
  notchLower: number;
  notchUpper: number;
  n: number;
}

/**
 * Quantiles from a histogram of one minute buckets. The result is accurate to a
 * minute by construction, which the panel footnote states, and the notch is the
 * usual 1.58 * IQR / sqrt(n) approximation to a median confidence interval.
 */
export function boxFromHistogram(buckets: { value: number; count: number }[]): BoxSummary | null {
  const sorted = [...buckets].filter((b) => b.count > 0).sort((a, b) => a.value - b.value);
  if (sorted.length === 0) return null;
  const n = sorted.reduce((acc, b) => acc + b.count, 0);
  if (n <= 0) return null;

  const at = (fraction: number): number => {
    const target = fraction * n;
    let cumulative = 0;
    for (const bucket of sorted) {
      cumulative += bucket.count;
      if (cumulative >= target) return bucket.value;
    }
    return sorted[sorted.length - 1]!.value;
  };

  const q1 = at(0.25);
  const median = at(0.5);
  const q3 = at(0.75);
  const iqr = q3 - q1;
  const notch = (1.58 * iqr) / Math.sqrt(n);
  return {
    min: sorted[0]!.value,
    q1,
    median,
    q3,
    max: sorted[sorted.length - 1]!.value,
    notchLower: median - notch,
    notchUpper: median + notch,
    n,
  };
}

/** Least squares fit of y against a cubic in x, weighted by trip count. */
export function weightedPolyFit(
  points: { x: number; y: number; w: number }[],
  degree = 3,
): ((x: number) => number) | null {
  const usable = points.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y) && p.w > 0);
  if (usable.length <= degree) return null;
  const size = degree + 1;
  // Normal equations. At degree three with a few hundred bins this is stable
  // enough, and it avoids pulling a linear algebra package in for one curve.
  const ata: number[][] = Array.from({ length: size }, () => new Array<number>(size).fill(0));
  const atb: number[] = new Array<number>(size).fill(0);
  for (const p of usable) {
    const powers: number[] = [];
    for (let i = 0; i < size; i += 1) powers.push(i === 0 ? 1 : powers[i - 1]! * p.x);
    for (let i = 0; i < size; i += 1) {
      for (let j = 0; j < size; j += 1) ata[i]![j]! += p.w * powers[i]! * powers[j]!;
      atb[i]! += p.w * powers[i]! * p.y;
    }
  }
  // Gaussian elimination with partial pivoting.
  for (let col = 0; col < size; col += 1) {
    let pivot = col;
    for (let row = col + 1; row < size; row += 1) {
      if (Math.abs(ata[row]![col]!) > Math.abs(ata[pivot]![col]!)) pivot = row;
    }
    if (Math.abs(ata[pivot]![col]!) < 1e-12) return null;
    [ata[col], ata[pivot]] = [ata[pivot]!, ata[col]!];
    [atb[col], atb[pivot]] = [atb[pivot]!, atb[col]!];
    for (let row = col + 1; row < size; row += 1) {
      const factor = ata[row]![col]! / ata[col]![col]!;
      for (let k = col; k < size; k += 1) ata[row]![k]! -= factor * ata[col]![k]!;
      atb[row]! -= factor * atb[col]!;
    }
  }
  const coefficients = new Array<number>(size).fill(0);
  for (let row = size - 1; row >= 0; row -= 1) {
    let total = atb[row]!;
    for (let k = row + 1; k < size; k += 1) total -= ata[row]![k]! * coefficients[k]!;
    coefficients[row] = total / ata[row]![row]!;
  }
  return (x: number) => coefficients.reduce((acc, c, i) => acc + c * x ** i, 0);
}
