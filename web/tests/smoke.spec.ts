import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { expect, test, type Page } from '@playwright/test';

// scripts/bench_queries.py folds this file into the published benchmark when it
// is present, joining on the label. Latency it can measure locally; bytes over
// the wire it cannot, because a local read is a file read. This is the only
// place that number exists.
const QUERY_LOG = 'tests/query-log.json';

/** The bench rows this page actually issues a query for, by label. A bench row
 *  whose query reads a different file is deliberately absent: attaching these
 *  byte counts to it would put one query's traffic beside another's file. */
const BENCH_LABELS = new Set([
  'Daily volume with trend',
  'Hour of week grid',
  'Choropleth, trips by zone',
  'Top origin destination flows',
  'Duration ridgeline',
  'Fare against distance hexbin',
]);

function writeQueryLog(queries: QueryLog['queries']): number {
  // One entry per label, taking the live run rather than a cache hit, which by
  // construction pulled nothing.
  const byLabel = new Map<string, { label: string; bytes: number; requests: number }>();
  for (const q of queries) {
    if (q.cached || q.error || !BENCH_LABELS.has(q.label)) continue;
    if (byLabel.has(q.label)) continue;
    byLabel.set(q.label, { label: q.label, bytes: q.bytes, requests: q.requests });
  }
  const entries = [...byLabel.values()].sort((a, b) => a.label.localeCompare(b.label));
  mkdirSync(dirname(QUERY_LOG), { recursive: true });
  writeFileSync(QUERY_LOG, `${JSON.stringify(entries, null, 2)}\n`, 'utf8');
  return entries.length;
}

const PANELS = [
  { id: 'pulse', chart: 'chart-hour-of-week' },
  { id: 'geography', chart: 'chart-zone-pareto' },
  { id: 'behavior', chart: 'chart-duration-ridgeline' },
  { id: 'mix', chart: 'chart-indexed-services' },
  { id: 'trust', chart: 'chart-quarantine' },
  { id: 'lineage', chart: 'chart-lineage-graph' },
] as const;

interface QueryLog {
  queries: {
    label: string;
    durationMs: number;
    bytes: number;
    requests: number;
    rows: number;
    cached: boolean;
    error?: string;
  }[];
  engineReadyMs: number | null;
  firstPaintMs: number | null;
  totalBytes: number;
  rangedRequests: number;
  wholeFileRequests: number;
}

declare global {
  interface Window {
    __cityflowQueryLog?: QueryLog;
    __cityflowQuery?: (sql: string) => Promise<unknown[]>;
  }
}

interface HourCell {
  dow: number;
  hour: number;
  trips: number;
}

async function waitForEngine(page: Page): Promise<void> {
  await page.waitForFunction(() => window.__cityflowQueryLog?.engineReadyMs != null, undefined, {
    timeout: 90_000,
  });
}

/** Scrolling every panel into view is what makes the charts below the fold mount. */
async function visitEveryPanel(page: Page): Promise<void> {
  for (const panel of PANELS) {
    await page.locator(`#${panel.id}`).scrollIntoViewIfNeeded();
    await page.waitForTimeout(1200);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
}

test.describe('cityflow dashboard', () => {
  test('renders every panel, answers queries, filters and toggles', async ({ page }, testInfo) => {
    const consoleErrors: string[] = [];
    const offOrigin: string[] = [];
    page.on('pageerror', (error) => consoleErrors.push(`pageerror: ${error.message}`));
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    // The page must be self contained: no CDN, no font service, no tile server.
    page.on('request', (request) => {
      const url = request.url();
      if (url.startsWith('data:') || url.startsWith('blob:')) return;
      if (!url.startsWith('http://127.0.0.1:4173/')) offOrigin.push(url);
    });

    const openedAt = Date.now();
    await page.goto('./', { waitUntil: 'domcontentloaded' });

    // First paint must not wait on the engine: the precomputed bootstrap puts a
    // real number on screen before DuckDB exists.
    const tripsTile = page.getByTestId('kpi-trips-value');
    await expect(tripsTile).toBeVisible({ timeout: 20_000 });
    const firstPaintMs = Date.now() - openedAt;
    const bootstrapValue = (await tripsTile.textContent())?.trim() ?? '';
    expect(bootstrapValue).not.toBe('');

    // The provenance banner is visible on first load, not buried in a panel.
    const banner = page.getByTestId('provenance-banner');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText(/source/i);
    await expect(page.getByTestId('provenance-pill')).toBeVisible();

    await waitForEngine(page);
    await visitEveryPanel(page);

    // 1. Every panel renders a chart.
    for (const panel of PANELS) {
      await expect(page.locator(`#${panel.id}`)).toBeVisible();
      const chart = page.getByTestId(panel.chart);
      await expect(chart).toBeVisible();
      await expect(chart.locator('svg').first()).toBeVisible();
    }

    // 2. The engine answered, over range requests, without downloading whole files.
    const log = await page.evaluate(() => window.__cityflowQueryLog);
    expect(log).toBeTruthy();
    const failed = (log?.queries ?? []).filter((q) => q.error);
    expect(failed, `queries failed: ${failed.map((q) => `${q.label}: ${q.error}`).join('; ')}`).toHaveLength(0);
    expect((log?.queries ?? []).length).toBeGreaterThan(8);
    expect(log?.rangedRequests ?? 0).toBeGreaterThan(0);
    expect(log?.wholeFileRequests ?? 0).toBe(0);

    testInfo.annotations.push(
      { type: 'firstPaintMs', description: String(firstPaintMs) },
      { type: 'engineReadyMs', description: String(Math.round(log?.engineReadyMs ?? 0)) },
      { type: 'queries', description: String(log?.queries.length ?? 0) },
      { type: 'bytesPulled', description: String(log?.totalBytes ?? 0) },
    );

    // 3. A filter change alters a displayed number.
    const before = (await tripsTile.textContent())?.trim();
    await page.getByTestId('filter-service-fhvhv').click();
    await expect
      .poll(async () => (await tripsTile.textContent())?.trim(), { timeout: 30_000 })
      .not.toBe(before);
    const after = (await tripsTile.textContent())?.trim();
    expect(after).toBeTruthy();
    // Put it back so the screenshots below show the default window.
    await page.getByTestId('filter-service-fhvhv').click();
    await expect.poll(async () => (await tripsTile.textContent())?.trim(), { timeout: 30_000 }).toBe(before);

    // 4. The hour of week grid is 168 measured cells.
    //
    // This is the check that would have caught the earlier version, which
    // reconstructed the grid by splitting a daily level with a day type hour
    // profile. That construction gave the five weekdays one shape and the two
    // weekend days another, so it is caught twice over: once by comparing rows
    // for equality, and once, with more teeth, by comparing them after
    // normalising away the day's volume. Two days can legitimately be busy in
    // the same hours; two days cannot legitimately have identical profiles to
    // fifteen decimal places.
    const heatmap = page.getByTestId('chart-hour-of-week');
    await heatmap.scrollIntoViewIfNeeded();
    await expect(heatmap.locator('svg g[aria-label="cell"] rect')).toHaveCount(168);

    const grid = (await page.evaluate(async () => {
      const run = window.__cityflowQuery;
      if (!run) return [];
      return (await run(
        `with agg as (select day_of_week, hour, trips from 'agg_hour_of_week.parquet')
         select day_of_week::int as dow, hour::int as hour, sum(trips) as trips
         from agg group by 1, 2 order by 1, 2`,
      )) as HourCell[];
    })) as HourCell[];

    expect(grid).toHaveLength(168);
    expect(new Set(grid.map((c) => c.dow)).size).toBe(7);
    expect(new Set(grid.map((c) => c.hour)).size).toBe(24);

    const rows = [...new Set(grid.map((c) => c.dow))].sort((a, b) => a - b).map((dow) =>
      grid
        .filter((c) => c.dow === dow)
        .sort((a, b) => a.hour - b.hour)
        .map((c) => Number(c.trips)),
    );
    for (let i = 0; i < rows.length; i += 1) {
      for (let j = i + 1; j < rows.length; j += 1) {
        const a = rows[i]!;
        const b = rows[j]!;
        expect(a.join(','), `days ${i} and ${j} have identical hourly counts`).not.toBe(b.join(','));

        const scaleA = a.reduce((acc, v) => acc + v, 0);
        const scaleB = b.reduce((acc, v) => acc + v, 0);
        expect(scaleA).toBeGreaterThan(0);
        expect(scaleB).toBeGreaterThan(0);
        const shapeA = a.map((v) => (v / scaleA).toFixed(12)).join(',');
        const shapeB = b.map((v) => (v / scaleB).toFixed(12)).join(',');
        expect(shapeA, `days ${i} and ${j} share one hourly profile, which is what a reconstruction looks like`).not.toBe(
          shapeB,
        );
      }
    }

    // 5. The table toggle swaps the chart for the rows behind it.
    const card = page.getByTestId('chart-zone-pareto');
    await card.scrollIntoViewIfNeeded();
    await expect(card.locator('svg').first()).toBeVisible();
    await page.getByTestId('chart-zone-pareto-view-table').click();
    await expect(card).toHaveAttribute('data-view', 'table');
    await expect(card.locator('table')).toBeVisible();
    await expect(card.locator('table thead th').first()).toBeVisible();
    await expect(card.locator('svg')).toHaveCount(0);
    await page.getByTestId('chart-zone-pareto-view-chart').click();
    await expect(card).toHaveAttribute('data-view', 'chart');
    await expect(card.locator('svg').first()).toBeVisible();

    expect(offOrigin, `requests left the origin: ${offOrigin.join(' | ')}`).toHaveLength(0);
    expect(consoleErrors, `console errors: ${consoleErrors.join(' | ')}`).toHaveLength(0);

    // 6. Hand the byte counts to the benchmark script.
    const finalLog = await page.evaluate(() => window.__cityflowQueryLog);
    const written = writeQueryLog(finalLog?.queries ?? []);
    expect(written, 'no benchmarked query was captured for the byte log').toBeGreaterThanOrEqual(
      BENCH_LABELS.size,
    );
    testInfo.annotations.push({ type: 'queryLogEntries', description: String(written) });
  });

  test('captures each panel', async ({ page }) => {
    await page.goto('./', { waitUntil: 'domcontentloaded' });
    await waitForEngine(page);
    await visitEveryPanel(page);

    for (const [index, panel] of PANELS.entries()) {
      const section = page.locator(`#${panel.id}`);
      await section.scrollIntoViewIfNeeded();
      // Plot and MapLibre both render after layout settles; a short wait keeps
      // the screenshots from catching a half drawn canvas.
      await page.waitForTimeout(1500);
      await section.screenshot({ path: `tests/screenshots/panel-${index + 1}-${panel.id}.png` });
    }
  });

  test('works at phone width without a horizontal scroll', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('./', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('kpi-trips-value')).toBeVisible({ timeout: 20_000 });
    await waitForEngine(page);
    await visitEveryPanel(page);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, 'the page scrolls sideways at phone width').toBeLessThanOrEqual(1);

    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(600);
    await page.screenshot({ path: 'tests/screenshots/phone-width.png', fullPage: false });
  });
});
