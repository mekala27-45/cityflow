import { expect, test, type Page, type Route } from '@playwright/test';

/**
 * The host mis-serves a byte range, and the page has to survive it.
 *
 * This is not hypothetical. On the deployed site, GitHub Pages answered
 * 'Range: bytes=0-3' on two of the eighteen shipped parquet files with four
 * zero bytes, while a whole file GET against the same URL in the same second
 * returned the correct bytes and the correct SHA-256. It gzip compresses
 * parquet responses and its edge served the range out of the compressed
 * representation while labelling the response with the identity length, so the
 * first few kilobytes arrived as zeroes, the tail arrived correct, and the
 * total size was right. Nothing upstream noticed. DuckDB read the footer
 * successfully, seeked to a column chunk near the start of the file, got
 * zeroes, and failed inside the Thrift parser with "Invalid data", which names
 * neither the file nor the cause. Three tiles read "no data" and two charts
 * showed an error that no reader could act on.
 *
 * So the fault is reproduced here rather than described. The static server used
 * by the rest of the suite serves ranges correctly, so the route below breaks
 * one file on purpose, in exactly the way the real host broke it: ranged reads
 * return zeroes, whole file reads are left alone.
 */

declare global {
  interface Window {
    __cityflowQueryLog?: { queries: { label: string; error?: string }[]; engineReadyMs: number | null };
    __cityflowDelivery?: () => { file: string; mode: 'range' | 'buffer'; bytes?: number; reason?: string }[];
  }
}

const BROKEN = 'agg_daily.parquet';

/** Serve ranged reads of one file as zeroes, exactly as the real host did. */
async function breakRangesFor(page: Page, file: string): Promise<{ ranged: () => number }> {
  let ranged = 0;
  await page.route(`**/data/${file}`, async (route: Route) => {
    const range = route.request().headers()['range'];
    if (!range) return route.fallback();

    const match = /bytes=(\d+)-(\d+)?/.exec(range);
    if (!match) return route.fallback();

    // Ask the server for the real thing, so the reported total and the response
    // length stay honest. Only the bytes are wrong, which is the whole point:
    // a fault that announced itself would already have been caught.
    const real = await route.fetch();
    const body = await real.body();
    ranged += 1;
    await route.fulfill({
      status: real.status(),
      headers: real.headers(),
      body: Buffer.alloc(body.length, 0),
    });
  });
  return { ranged: () => ranged };
}

test.describe('a host that mis-serves byte ranges', () => {
  test('falls back per file, keeps every number, and says so', async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));

    const probe = await breakRangesFor(page, BROKEN);
    await page.goto('./', { waitUntil: 'domcontentloaded' });

    await page.waitForFunction(() => window.__cityflowQueryLog?.engineReadyMs != null, undefined, {
      timeout: 90_000,
    });

    // The fault was actually exercised. Without this the test could pass by
    // never having broken anything, which is the failure mode of most tests
    // written for a bug that has already been fixed.
    expect(probe.ranged(), 'no ranged read of the broken file was intercepted').toBeGreaterThan(0);

    // 1. The broken file fell back, and only it.
    const report = await page.evaluate(() => window.__cityflowDelivery?.() ?? []);
    const buffered = report.filter((d) => d.mode === 'buffer');
    expect(buffered.map((d) => d.file)).toEqual([BROKEN]);
    expect(buffered[0]?.reason ?? '').toContain('50 41 52 31');
    expect(report.filter((d) => d.mode === 'range').length).toBeGreaterThan(10);

    // 2. The numbers that read "no data" when this bug was live now render.
    for (const tile of ['trips', 'mean_duration_min', 'tip_rate', 'airport_share']) {
      const value = page.getByTestId(`kpi-${tile}-value`);
      await expect(value).toBeVisible({ timeout: 30_000 });
      await expect(value).not.toHaveText(/no data/i);
    }

    // 3. No query failed, including the two that read the broken file.
    const log = await page.evaluate(() => window.__cityflowQueryLog);
    const failed = (log?.queries ?? []).filter((q) => q.error);
    expect(
      failed,
      `queries failed: ${failed.map((q) => `${q.label}: ${q.error}`).join('; ')}`,
    ).toHaveLength(0);

    // 4. The page discloses the fallback rather than hiding it. Every other
    //    panel claims the browser reads by range; on this host that is not
    //    true for this file, and saying nothing would make the page wrong.
    await page.locator('#trust').scrollIntoViewIfNeeded();
    const notice = page.getByText(/not read the way the rest of this page describes/i);
    await expect(notice).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(BROKEN, { exact: false }).first()).toBeVisible();

    expect(pageErrors).toHaveLength(0);
  });

  test('says nothing when every file is served correctly', async ({ page }) => {
    await page.goto('./', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.__cityflowQueryLog?.engineReadyMs != null, undefined, {
      timeout: 90_000,
    });

    const report = await page.evaluate(() => window.__cityflowDelivery?.() ?? []);
    expect(report.length).toBeGreaterThan(10);
    expect(report.every((d) => d.mode === 'range')).toBe(true);

    await page.locator('#trust').scrollIntoViewIfNeeded();
    await expect(page.getByText(/not read the way the rest of this page describes/i)).toHaveCount(0);
  });
});
