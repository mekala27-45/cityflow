import { expect, test, type Page } from '@playwright/test';

const PANELS = [
  { id: 'pulse', chart: 'chart-hour-of-week' },
  { id: 'geography', chart: 'chart-zone-pareto' },
  { id: 'behavior', chart: 'chart-duration-ridgeline' },
  { id: 'mix', chart: 'chart-indexed-services' },
  { id: 'trust', chart: 'chart-quarantine' },
  { id: 'lineage', chart: 'chart-lineage-graph' },
] as const;

interface QueryLog {
  queries: { label: string; durationMs: number; bytes: number; rows: number; cached: boolean; error?: string }[];
  engineReadyMs: number | null;
  firstPaintMs: number | null;
  totalBytes: number;
  rangedRequests: number;
  wholeFileRequests: number;
}

declare global {
  interface Window {
    __cityflowQueryLog?: QueryLog;
  }
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

    // 4. The table toggle swaps the chart for the rows behind it.
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
