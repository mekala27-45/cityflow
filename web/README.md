# cityflow dashboard

A single page over the cityflow warehouse. Six panels, one filter row, and no
server: the browser opens the parquet files directly and runs SQL against them
with DuckDB compiled to WebAssembly.

Built as a static export for GitHub Pages at `https://mekala27-45.github.io/cityflow/`.

## Data provenance

Every figure on this page is measured on a seeded generator that reproduces the
TLC trip record schema and its known defects. It is **not** measured on the
published TLC trip record files.

The generator writes months that look like TLC months: the same column names and
types, the same schema changes at the same points in the history
(`congestion_surcharge` appearing in 2019, `airport_fee` in 2022,
`cbd_congestion_fee` in 2025), the same absent columns in the high volume for
hire vehicle feed, and the same classes of bad row the real files contain,
including dropoffs before pickups, implausible speeds, non positive fares and
exact duplicates. The pipeline that reads it, the quarantine rules that reject
rows, the metric definitions, the intervals and the changepoint search are all
real and all run against that source. The trips themselves are not.

The page does not take this on trust. `mart_source_freshness` carries a
`backend` column written at ingest, the banner reads it and says what it finds;
if the pipeline is ever pointed at the published files the banner changes without
anyone editing the dashboard.

Where this matters for reading the charts: absolute volumes are the generator's,
not the city's, so no number here should be quoted as a fact about New York. The
shapes the pipeline finds in it, the share of rows each quarantine rule catches,
the zones whose tipping rate does and does not survive a multiple comparison
correction, and the cost of a query in bytes, are all real measurements of real
machinery.

## Architecture

```
public/data/*.parquet        the warehouse output, served as static files
        |  HTTP range requests, one column chunk at a time
        v
duckdb-wasm in a web worker  registerFileURL(..., DuckDBDataProtocol.HTTP, false)
        |  Arrow
        v
useQuery hook                cancellation, error state, LRU on the SQL text
        |
        v
panels                       Observable Plot, MapLibre, one hand built D3 graph
```

Three things are worth knowing before changing anything here.

**Nothing is fetched from a CDN.** The DuckDB runtime, its worker, the MapLibre
worker and every data file are served from this origin under the site's base
path. `scripts/prepare-assets.mjs` copies them out of `node_modules` at build
time. There is no `<script src="https://...">` anywhere and no font is fetched at
runtime; the type is a system stack. The smoke test asserts that no request
leaves the origin.

**duckdb-wasm is pinned to 1.28.0 deliberately.** From 1.29.0 the parquet reader
moved out of the core module into an extension the engine downloads from
`extensions.duckdb.org` on first use. That is an uncontrolled third party
dependency on a page whose whole claim is that it is self contained, and on a
network that cannot reach it the first query traps rather than failing cleanly.
1.28.0 is the last release with parquet statically linked.

**MapLibre needs its worker URL set.** MapLibre resolves its own worker from
`import.meta.url`, which after bundling points inside a Next chunk directory that
holds no worker file. Nothing errors: the GeoJSON source simply never reports
itself loaded and the map paints its background and nothing else.
`src/components/map-canvas.tsx` calls `setWorkerUrl` with a copy under the base
path.

## First paint does not wait on WASM

`scripts/precompute.mjs` runs at build time and writes `public/bootstrap.json`,
about 200 kB: the daily series for the whole published window, the STL trend, the
168 cell hour of week grid, the top zones and the source provenance. Every field
in it is read by something and rounded to the precision it is read at, because
this payload sits in front of the first number on screen. The page renders it,
then swaps in live query results when the engine is ready. Measured in headless
Chromium against the built export over 67 MB of shipped parquet, the first KPI
value is on screen around 600 ms and the engine is ready around 700 ms.

The precompute reads its aggregates out of `metric_catalog.json` for the same
reason the browser does.

## What the build publishes about itself

`manifest.json` is the build's own record: the commit, the backend, the published
window, every figure the written documents quote, the quarantine counts by rule,
the shipped file sizes and the query benchmark. The provenance banner and the
filter window are read from it rather than derived a second way, so the page and
the prose cannot disagree about what was published, and a regenerated warehouse
moves the dashboard's date range without a code change.

`bench.json` is the query benchmark, shown in panel five. Latency is measured
locally against the same parquet the browser reads, so it is a lower bound with
no network and no WebAssembly in it. Bytes over the wire cannot be measured
there, so the smoke test writes `tests/query-log.json`, an array of
`{label, bytes, requests}` whose labels match the benchmark's query names, and
`scripts/bench_queries.py` folds it in on its next run. Only queries that read
the same file as the benchmark row are labelled: attaching one query's byte count
to another query's file would be worse than leaving the column blank.

## The metric layer

`public/data/metric_catalog.json` carries, per metric, a `browser_sql`
expression over the aggregate columns. The dashboard lifts the projection out of
that expression and splices it into its own queries over a CTE named `agg`
(`src/lib/metrics.ts`). No component sum is divided by another anywhere in this
codebase. A definition change in dbt reaches every panel without a TypeScript
edit, and panel six prints the exact SQL, for both engines, with a copy button.

Row level display fields in a detail table are a different thing and are computed
where they are shown.

Each metric declares exactly one of `interval` and `interval_note`. The first
names a method the page applies; the second is the catalog's own sentence
explaining why the metric takes no interval, printed verbatim wherever the metric
appears. `MetricCatalog` throws on a metric carrying neither, and the page renders
that failure rather than starting up with figures it cannot annotate.

## Design rules this page holds itself to

- **No dual axes.** Two measures of different scale become two charts, small
  multiples, or both indexed to a common base. Panel four shows service volume
  twice, once indexed and once as a share, rather than once on two axes. The
  Pareto puts its bars and its cumulative line on one percentage scale, because
  both are shares of the same denominator. Facet scales (`fy`) appear in the
  small multiples; a facet is not a second measure axis.
- **No rate without an interval where the data ships one.** Proportions with a
  `wilson` entry in the catalog carry a Wilson interval; means with `student-t`
  carry one. Where the catalog names no method it carries an `interval_note`
  instead, a sentence saying why, and the page prints that sentence rather than
  inventing a band or leaving a bare number. The two fields are complements: the
  loader rejects a catalog in which any metric has neither, and says so on screen.
  Every period over period delta carries a Welch interval on daily means.
- **No pie charts, no rainbow scales for magnitude, no 3D.**
- **The palette is validated, not asserted.** `scripts/validate_palette.js`
  checks lightness and chroma separation, contrast against the surface, delta E
  for normal vision and for three kinds of colour vision deficiency, and
  monotonic lightness for ordinal ramps. It found two real failures in an earlier
  version: a red and a blue within 0.014 lightness of each other, and a light
  sequential ramp whose first step was under the ordinal floor. Both are fixed in
  `src/lib/palette.ts`, with the reason recorded next to the values. The
  diverging scale is validated one arm at a time from the midpoint outwards; the
  midpoint is deliberately below the contrast floor, because a diverging zero is
  meant to recede.
- **Hues are fixed by entity, never by rank.** For hire vehicles are cyan, yellow
  is amber, green is emerald, in every chart, whatever the filter leaves. Status
  colours are reserved for state and never reused as a series colour.
- **Unknown zones never enter a geographic aggregate**, and every chart that
  excludes them says so on screen with the share it cost.

## Running it

```
npm install
npm run lint          # eslint, flat config
npx tsc --noEmit      # strict, noUncheckedIndexedAccess
npm run build         # prepare-assets, precompute, next build to out/
```

`npm run build` writes a fully static export into `out/`, base path `/cityflow`.

To serve it the way GitHub Pages does, including the range requests DuckDB needs:

```
node tests/static-server.mjs      # http://127.0.0.1:4173/cityflow/
```

## Tests

```
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers npx playwright test
```

`tests/smoke.spec.ts` loads the built export, writes `tests/query-log.json` for
the benchmark script, waits for the engine, asserts that
every panel renders a chart, that no query failed, that every parquet read was a
range request and none was a whole file download, that no request left the
origin, that a filter change moves a displayed number, and that the table toggle
swaps the chart for its rows. It also checks that the hour of week grid draws 168
cells and that no two of its seven day rows are identical or proportional to each
other, which is the assertion that catches a grid reconstructed from a shared
profile rather than measured. It writes one screenshot per panel into
`tests/screenshots/` and checks that the page does not scroll sideways at
390 pixels wide.

## Instrumentation

Every query is timed and every byte the worker pulls is counted, by wrapping
`XMLHttpRequest` inside the worker, which is where DuckDB issues its Range
requests. A HEAD carries no body and is not counted. The records are on
`window.__cityflowQueryLog` for an external benchmark script, and the panel at
the bottom right of the page reads the same store. `window.__cityflowQuery(sql)`
runs ad hoc SQL against the live engine from a console.

## Known gaps

- **Several marts carry no date or no day type**, so those filters reach some
  charts and not others. Each chart states which filters it answers to rather
  than letting a control appear to do something it cannot.
- **`zone_comparisons` is computed once over the whole source window**, so the
  date and day type filters do not reach the zone ranking at all.
