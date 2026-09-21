// Record the frames for the demo animation.
//
// Two shots, chosen because they are the two things a reader will not believe
// from a static screenshot: that the hour of week grid is a hundred and sixty
// eight measured cells rather than a picture, and that changing a filter runs a
// real query against parquet fetched by range request rather than swapping a
// precomputed image.
//
// Frames go out as PNG and scripts/build_demo_gif.py assembles them, because
// assembling an animation is an image problem and not a browser problem.
//
//   node tests/record-demo.mjs <outputDirectory>

import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdir, rm } from "node:fs/promises";
import path from "node:path";

const OUT = process.argv[2] ?? "tests/demo-frames";
// The same static server the smoke test uses, for the same reason: it serves
// the base path and it answers range requests, and a server that does neither
// would record an animation of a page that never loads.
const BASE = "http://127.0.0.1:4173/cityflow/";

// A frame every 120ms. Fast enough that a filter change reads as a change
// rather than a cut, slow enough that the file stays small.
const FRAME_MS = 120;

async function serve() {
  const server = spawn("node", ["tests/static-server.mjs"], {
    stdio: "ignore",
    detached: false,
  });
  // serve needs a moment before it answers, and polling is more reliable than
  // a fixed sleep on a slow machine.
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(BASE);
      if (response.ok) return server;
    } catch {
      // not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  server.kill();
  throw new Error(`serve never answered on ${BASE}`);
}

async function main() {
  await rm(OUT, { recursive: true, force: true });
  await mkdir(OUT, { recursive: true });

  const server = await serve();
  const browser = await chromium.launch({
    executablePath: "/opt/pw-browsers/chromium",
  });
  const page = await browser.newPage({
    viewport: { width: 1280, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
  });

  let frame = 0;
  const shoot = async (element) => {
    const target = element ?? page;
    await target.screenshot({
      path: path.join(OUT, `frame-${String(frame).padStart(4, "0")}.png`),
    });
    frame += 1;
  };

  const hold = async (element, count) => {
    for (let i = 0; i < count; i += 1) {
      await shoot(element);
      await page.waitForTimeout(FRAME_MS);
    }
  };

  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => window.__cityflowQueryLog?.engineReadyMs != null,
    undefined,
    { timeout: 60_000 }
  );
  await page.waitForTimeout(1200);

  // Shot one: the hour of week grid, with the service filter changing under it.
  const heatmap = page.getByTestId("chart-hour-of-week");
  await heatmap.scrollIntoViewIfNeeded();
  await page.waitForTimeout(600);
  await hold(heatmap, 8);

  // Turn the two smaller services off and back on. Each click is a query.
  for (const service of ["fhvhv", "yellow", "fhvhv", "yellow"]) {
    const chip = page.getByTestId(`filter-service-${service}`);
    if (await chip.count()) {
      await chip.click();
      await page.waitForTimeout(450);
      await hold(heatmap, 6);
    }
  }

  // Shot two: the flow map, with the number of drawn flows changing.
  const flows = page.getByTestId("chart-od-flows");
  if (await flows.count()) {
    await flows.scrollIntoViewIfNeeded();
    await page.waitForTimeout(700);
    await hold(flows, 6);
    for (const label of ["400", "50", "150"]) {
      const control = flows.getByRole("button", { name: label, exact: true }).first();
      if (await control.count()) {
        await control.click();
        await page.waitForTimeout(550);
        await hold(flows, 6);
      }
    }
  }

  await browser.close();
  server.kill();
  console.log(`wrote ${frame} frames to ${OUT}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
