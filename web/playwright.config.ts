import { defineConfig, devices } from '@playwright/test';

// The smoke test runs against the real export, not the dev server: the whole
// point is to check the thing that gets deployed, including the base path and
// the range requests DuckDB depends on.
export default defineConfig({
  testDir: './tests',
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4173/cityflow/',
    viewport: { width: 1440, height: 1000 },
    // Dark is the default theme, and the headless browser reports a light
    // preference unless told otherwise, so the screenshots would come out in the
    // secondary theme.
    colorScheme: 'dark',
    trace: 'off',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'node tests/static-server.mjs',
    url: 'http://127.0.0.1:4173/cityflow/',
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
