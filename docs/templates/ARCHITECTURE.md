# Architecture

This document answers one question: why does the browser query a conformed mart
layer instead of the fact table? Everything else here exists to support that
answer, because everything else in the build was shaped by it.

A note on the numbers below. Every measured figure in this document is a
placeholder rendered from `web/public/data/manifest.json` by
`scripts/check_published_numbers.py`, and the manifest is produced by running
queries against the warehouse. The few code constants the manifest does not
carry are named rather than quoted, so there is exactly one place to read their
current value and no second copy to drift.

## The question

The warehouse holds {{ n:fct_trip_rows }} trip rows. The dashboard queries
{{ f2:shipped_mb }} MB of parquet spread over {{ n:shipped_files }} files, the
largest of which is {{ f2:largest_shipped_mb }} MB. DuckDB-WASM can read parquet
over HTTP range requests, and the fact table is parquet. So why is there a layer
in between?

There are three answers and they are not equally important.

**Size is the weakest one.** The fact table does not fit inside the
{{ f0:file_budget_mb }} MB per file budget, and GitHub refuses to serve a file
over a hundred megabytes at all. This is a real constraint and the last section
of this document is about it, but it is a constraint about hosting, and a
hosting constraint is not an architecture.

**Pruning is the second one, and it is a property of parquet rather than of this
project.** A parquet file has exactly one physical order. Row group statistics
prune a query only on columns that correlate with that order; on every other
column the reader has to look at every group to find out that it does not want
it. The dashboard filters on service, zone, hour, month, borough, day of week,
day type and origin to destination pair. No single sort of one fact table
satisfies that set. A layer of several aggregates, each sorted for the filters
its own panels apply, does.

**Conformance is the real answer.** A dashboard does not need trip rows. It
needs the additive components of every metric in the catalog, at the finest
grain any filter in the interface can reach, and nothing else. Once that is
written down, the choice is not between a fact table and an aggregate. It is
between one conformed aggregate and five bespoke extracts, one per panel, which
is what a team builds when nobody writes this paragraph. Five extracts means
five definitions of a trip count, and the quarter in which two dashboards
disagree about revenue starts the day the second extract ships.

So the workhorse is a single file, `agg_zone_hour.parquet`, at zone by hour by
day type by month by service. It is {{ n:rows.agg_zone_hour_parquet }} rows and
{{ f2:mb.agg_zone_hour_parquet }} MB, which is the whole of
{{ n:fct_trip_rows }} trip rows reduced to the grain the interface can actually
reach. Every KPI tile, the hour of week choropleth, the Pareto, the zone
ranking and the zone comparison panel read it. They cannot disagree with each
other, because there is only one set of numbers for them to read. Where a panel
genuinely needs a different grain, it gets its own file and
the docstring on its builder says why the conformed one would have been wrong:
`build_hour_of_week` is the clearest case, because reconstructing Tuesday from a
weekday average produces a grid in which five rows are identical by
construction, and a reader cannot tell that by looking at it.

The layer is trustworthy only because `cityflow reconcile` runs every metric
twice, once from `fct_trip` and once from the shipped aggregate, and fails the
build when the two disagree at any of four grains. Without that, the shipped
layer is a copy, and a copy of the truth is a second truth. See
[docs/metrics.md](docs/metrics.md).

## The pipeline, end to end

```
  source                    ingest (Python)                    warehouse (dbt)
┌──────────────────┐     ┌────────────────────────┐     ┌─────────────────────────┐
│ TLC parquet over │     │ registry: resolve one  │     │ stg_trips      (view)   │
│ HTTPS            │     │   month to a local file│     │ stg_zones      (view)   │
│        or        │────▶│ vintage: pick the      │────▶│ stg_quarantine (view)   │
│ seeded generator │     │   column map by era    │     │ stg_source_audit (view) │
│                  │     │ normalize: one schema  │     │ int_trip_geography      │
│ one month at a   │     │ quarantine: label,     │     │            (ephemeral)  │
│ time, released   │     │   never filter         │     │ fct_trip   (incremental)│
│ after landing    │     │ land: stg_trip,        │     │ dim_date dim_hour       │
│                  │     │   quarantine_log,      │     │ dim_zone dim_service    │
│                  │     │   quarantine_sample,   │     │ dim_ratecode            │
│                  │     │   source_audit         │     │ mart_quarantine         │
│                  │     │                        │     │ mart_null_rates         │
│                  │     │ balance check:         │     │ mart_source_freshness   │
│                  │     │ source = clean +       │     │                         │
│                  │     │   sum(quarantined)     │     │ dbt tests run here      │
└──────────────────┘     └────────────────────────┘     └───────────┬─────────────┘
                                                                    │
                                                                    ▼
     browser                    shipped layer                  publish (Python)
┌──────────────────┐     ┌────────────────────────┐     ┌─────────────────────────┐
│ DuckDB-WASM      │     │ agg_zone_hour          │     │ aggregates: components  │
│ registers URLs,  │◀────│ agg_hour_of_week       │◀────│   only, never rates     │
│ reads footers    │     │ agg_daily              │     │ analysis: STL, PELT,    │
│ and column chunks│     │ agg_daily_decomposition│     │   Wilson, BH correction │
│ by range request │     │ agg_od_flow            │     │ catalog: metrics.yml    │
│                  │     │ agg_duration_dist      │     │   rendered to JSON      │
│ metric layer     │     │ agg_fare_distance      │     │ lineage: from the dbt   │
│ splices          │     │ detail_<month>         │     │   manifest, not by hand │
│ browser_sql from │     │ dim_* mart_*           │     │ manifest: every claim,  │
│ metric_catalog   │     │ zones.geojson          │     │   re-measured           │
│                  │     │ manifest.json          │     │                         │
└──────────────────┘     └────────────────────────┘     └─────────────────────────┘
```

The two halves of the warehouse meet at exactly one place. The Python ingest
owns `stg_trip`, `quarantine_log`, `quarantine_sample` and `source_audit`; dbt
owns everything from the staging views down, and declares those four tables in
`transform/models/staging/_sources.yml`. Nothing else crosses.

The ingest works one month at a time and deletes each source file once its rows
are landed. That is not tidiness. A year of high volume for hire records is
hundreds of millions of rows, and the raw parquet for it does not fit next to
the warehouse on a small disk. Peak disk is one month of source plus the
warehouse, rather than a full window of both.

## The two ingest backends

`config/cityflow.yml` selects `tlc` or `synthetic`, and `CITYFLOW_BACKEND`
overrides it. Every row of `source_audit` records which one produced it, and the
manifest carries the set: this build is `{{ raw:backend }}`, across
{{ n:source_files }} source files, {{ n:months }} months, {{ n:services }}
services and {{ n:vintages }} schema vintages.

The `tlc` backend reads the published parquet over HTTPS with `curl`, to a
temporary name, moved into place only on success. A Python HTTP client would be
fewer lines and would leave a truncated parquet behind on an interrupted
transfer, which reads as a valid file with fewer rows.

The `synthetic` backend is a seeded generator, and it is not an apology for the
absence of the real thing. It is what makes three otherwise impossible things
possible.

The first is testing the quarantine rules at all. A rule needs a fixture that
contains the defect it claims to catch, at a realistic rate and at a realistic
scale. Asserting that a rule fires on a hand written three row table proves the
SQL parses. Asserting that it catches a known number of rows out of nineteen
million, and that the rules partition the rejected set without overlapping,
proves the rule. The generator injects each defect from disjoint bands of a
single uniform draw, so a row carries at most one injected defect and each
realised rate is the configured rate rather than the configured rate conditioned
on the others not firing.

The second is continuous integration. CI cannot download eight gigabytes of
parquet on every push, and a pipeline whose tests only run against data CI
cannot fetch is a pipeline with no tests. The `pipeline` job in `ci.yml` runs
the entire build end to end at `--scale 0.002`, into a scratch tree, and then
asserts with `git diff --exit-code` that the scratch tree did not leak back into
the repository. That last step matters: a build that rewrote the committed
shipped layer would make the published figures gate pass against numbers it had
just written.

The third is the demo. A public dashboard has to work on first click with no key
and no setup.

The interface is deliberately identical. The same vintage mapping, the same
normalizer and the same quarantine rules run over both backends, so the
synthetic path exercises the production path instead of bypassing it. The
generator emits files carrying the exact column names of the vintage it was
asked for, which means the per vintage mapping is tested by the generator rather
than short circuited by it. The one difference, the backend name, is written on
every row of `source_audit`, printed on every report, and carried in the
manifest, because a number measured on generated data has to be labelled
everywhere it appears. What the generator reproduces and what it does not is
stated at length in [docs/data.md](docs/data.md).

The committed default is `synthetic` for a blunt reason: this repository was
built on a machine whose egress policy denies the TLC distribution host, and a
default that cannot run on the machine it was written on is not a default. CI is
in the same position permanently.

## The star schema

`fct_trip` is one row per trip. The conformed dimensions are `dim_date`,
`dim_hour`, `dim_zone`, `dim_service` and `dim_ratecode`, and three marts carry
the build's own health: `mart_quarantine`, `mart_null_rates` and
`mart_source_freshness`. All of them ship, so the browser joins against the same
dimensions the warehouse does rather than against a copy assembled in
TypeScript. dbt builds {{ n:dbt_models }} models and runs {{ n:dbt_tests }} data
tests over them, and declares {{ n:dbt_exposures }} exposures, one per dashboard
panel, so the lineage graph the page draws is the graph dbt actually built.

Four decisions in that schema are worth the words.

**`dim_date` is generated from the fact, not from a hardcoded range.** A build
window that grows cannot then leave the date dimension short, which would
quietly drop the new months out of every date joined report. The holiday list
comes from a seed rather than a `case` expression, because a holiday list is
data: it changes every year, and buried in a case expression nobody finds it
when the next year has to be added. The generator reads the same seed, so it
cannot dip volume on a day the warehouse does not think is a holiday. Two copies
of a holiday list is one copy too many, and the disagreement would show up as a
bump in the STL remainder that looks exactly like a finding.

**`dim_ratecode` carries two members the TLC does not publish.** Member zero is
"not applicable", for the for hire vehicle trips that record no rate code at
all; member ninety nine is the TLC's own unknown, and `fct_trip` folds any code
outside the published range into it. A null foreign key is a row that falls out
of every rate code breakdown without anybody being told.

**`dim_service` is written out rather than selected distinct from the fact.** A
dimension built from whatever happens to be in the data cannot show that a
service has stopped appearing, which is the one thing a service list is for.

**Zone resolution lives in `int_trip_geography`, not in `fct_trip`.** It carries
a policy decision, and a policy decision spread across two joins inside a fact
table is a decision nobody will find again. The policy: a zone id that does not
resolve in the dimension is treated as unknown, exactly like the TLC's own
unknown codes, because the alternative is a null `has_known_geography`, and a
null there reads as "no" in some tools and as "yes" in others.

`fct_trip` is incremental, and its predicate is an anti join on `source_period`
rather than "greater than the newest loaded month". A month arrives whole, from
one published file, so a month is either fully loaded or absent. Comparing
against the maximum would silently refuse a backfill of an older month, which is
exactly the case where an incremental model loses data without erroring.

## Why the shipped layer publishes components and never rates

No file in `web/public/data` contains a rate. They contain numerators and
denominators, and `metrics/metrics.yml` defines the division.

The reason is that a published rate cannot be re-aggregated. Take two zones with
tip rates of ten percent and twenty percent. The combined tip rate is not
fifteen percent unless the two zones happen to carry identical fare totals; it
is the sum of the tips over the sum of the fares, and the arithmetic mean of the
two rates weights each zone equally regardless of how much money went through
it. Averaging two zones' tip rates is therefore wrong for both of them, and it
is wrong in a direction that depends on the data rather than in a direction a
reader could correct for.

This is not a hypothetical. The moment a column called `tip_rate` exists in a
parquet file that a browser can query, somebody writes `avg(tip_rate) group by
borough`, and the result is plausible, wrong, and impossible to distinguish from
the right answer by looking at it. Shipping `tip_obs_tip_sum` and
`tip_obs_fare_sum` makes that mistake unavailable: there is nothing to average.
Sums can be added at any grain a filter produces, which is the whole reason this
layer is built out of sums.

The mechanism that keeps it honest is the `components` list in the metric
definition. `verify_components` checks, at publish time, that every component
any metric names is a column the aggregate builders can actually emit. A metric
that needs a component nobody publishes fails the build rather than returning
nulls in the interface.

## Sort keys, row group sizing and range requests

A parquet row group is the unit of two different things at once: statistics
based pruning, and the HTTP range request that fetches it. That coincidence is
what the shipped layer is designed around.

Each aggregate is sorted on the columns its panels filter by, and the sort key
is recorded on the `AggregateResult` the builder returns:

| file | sorted by |
| --- | --- |
| `agg_zone_hour.parquet` | service, pu_zone_id, hour, month |
| `agg_hour_of_week.parquet` | service, borough, day_of_week, hour, month |
| `agg_daily.parquet` | service, date_day |
| `agg_od_flow.parquet` | service, pu_zone_id, do_zone_id, month |
| `agg_duration_dist.parquet` | service, hour, minute_bucket |
| `agg_fare_distance.parquet` | service, hex_q, hex_r |
| `detail_<month>.parquet` | pu_zone_id, pickup_ts |

Sorting on the filter is what turns "scan the file" into "fetch two row groups".
With the leading column of the sort key in the `where` clause, the min and max
statistics on every other row group exclude it outright, and DuckDB never asks
the network for those bytes.

That claim is measured rather than asserted, and it is measured twice. Locally,
`scripts/bench_queries.py` reads each file's footer and counts, for each query's
filter, how many row groups could be skipped outright:
{{ n:bench_queries_pruned }} of the {{ n:bench_queries }} panel queries carry a
filter that prunes, and the best of them skips
{{ f0:bench_best_pruning_pct }} percent of its file's row groups. Latency there
is a lower bound with no network and no WebAssembly, and it sits at
{{ f2:bench_p95_median_ms }} ms at the median p95, {{ f2:bench_p95_max_ms }} ms
at the worst.

Bytes over the wire cannot be measured locally, because a local read is a file
read. The Playwright smoke test counts the range requests DuckDB-WASM actually
issues and the bench script folds that log in. Across the panel queries whose
browser traffic was captured, DuckDB-WASM pulled
{{ f1:bench_browser_fetched_pct }} percent of the bytes those files contain and
read no file whole. The sharpest single case is the
{{ raw:bench_tightest_query }} query, which fetched {{ f1:bench_tightest_kb }}
KB out of a {{ f2:bench_tightest_file_mb }} MB file. Nothing about that is a
property of DuckDB. It is a property of having sorted the file on the column the
query filters. The full table is in [RESULTS.md](RESULTS.md).

Row group size is the tuning knob and it cuts both ways. Smaller groups prune
more finely and cost more footer metadata; larger groups amortise the metadata
and force the reader to fetch data it does not want. `AGGREGATE_ROW_GROUP` in
`packages/publish/src/cityflow_publish/aggregates.py` is chosen so that a zone
and hour slice of `agg_zone_hour` lands inside one or two groups at this grain,
which is what makes a filtered query a two range fetch rather than a scan.
`DETAIL_ROW_GROUP` is larger, because the detail extract is read by scrolling a
table rather than by filtering to a point.

Two supporting decisions make the range requests work at all. The browser
registers file URLs rather than bytes, with fetch deferred, so opening the page
does not cost the whole shipped directory; DuckDB opens a file lazily on first
reference and then reads only the footers and column chunks a query needs.
And `duckdb-wasm` is pinned below the release in which the parquet reader moved
out of the core module into an extension fetched from an external host at first
use. That request is unreachable on this network and would be an uncontrolled
third party dependency on the deployed page even where it is reachable. The
`web` job in CI greps the built export for any third party origin and fails if
one appears.

## The size constraint, which shaped the design rather than surprising it

GitHub refuses a file over a hundred megabytes and warns past fifty. A
repository that serves its own demo from Pages therefore has a hard per file
ceiling, and the expensive way to discover it is at push time, after the
warehouse has been rebuilt.

So the budget is configuration, not folklore. `shipped_file_budget_mb` is
{{ f0:file_budget_mb }} MB and `shipped_total_budget_mb` is
{{ f0:total_budget_mb }} MB, both in `config/cityflow.yml`; `build_all` raises
before it returns if any file exceeds the per file budget, and
`scripts/check_shipped_size.py` re-checks the committed directory in CI against
the same config. The per file budget sits below the hundred megabyte hard limit
to leave room to be wrong about compression. The total budget exists for a
different reason: nobody clones a repository to look at a gigabyte of parquet,
and a shipped layer that keeps growing is a sign the aggregate grain has drifted
towards the fact table.

The constraint is visible in the design rather than bolted onto it.

- The workhorse aggregate is at zone by hour by day type by month, not zone by
  hour by day, because the day grain multiplies the file by thirty for a filter
  the interface does not offer. As built it is
  {{ f2:mb.agg_zone_hour_parquet }} MB.
- `agg_hour_of_week` is at borough grain, not zone grain, because the heatmap's
  own filter is borough and the zone grain would multiply that file by forty for
  a chart that never shows a zone. At borough grain it is
  {{ f2:mb.agg_hour_of_week_parquet }} MB.
- The largest shipped file is `agg_od_flow.parquet` at
  {{ f2:mb.agg_od_flow_parquet }} MB over
  {{ n:rows.agg_od_flow_parquet }} rows, because an origin to destination matrix
  is quadratic in zones and there is no coarser grain that still answers the
  question the flow map asks. It is the file to watch as the window grows.
- The trip grain extract covers one month, and yellow and green only. It is
  {{ n:rows.detail_2024_06_parquet }} rows and
  {{ f2:mb.detail_2024_06_parquet }} MB. A month of for hire records does not
  fit in the per file budget, and splitting it across eight files to get under a
  per file limit would be dodging the budget rather than meeting it. The
  interface says which services the detail covers rather than letting a reader
  assume.
- That extract omits dropoff timestamp, total amount and implied speed. Each is
  an exact function of columns that are shipped, and at trip grain those three
  columns are a meaningful share of the budget. They are display fields, not
  metrics, so the page derives them, and the metric layer enforcement gate is
  scoped to say so explicitly rather than leaving it to be argued about later.

The result is {{ n:fct_trip_rows }} fact rows summarised into
{{ f2:shipped_mb }} MB across {{ n:shipped_files }} files, which is
{{ f0:summarization_ratio }} source rows for every megabyte a reader downloads.
The per file breakdown is in [RESULTS.md](RESULTS.md).

## Where each gate sits

Nothing here is advisory. Every one of these fails the build.

| gate | command | runs against | catches |
| --- | --- | --- | --- |
| formatting, types, SQL style, dashes | `make lint` | the tracked tree | ruff, `mypy --strict`, sqlfluff, and `check_no_em_dash.py` |
| unit tests with a coverage floor | `make test` | the packages | logic, plus the deliberate metric layer violation fixture |
| balance | inside `cityflow ingest` | each month, at runtime | source rows not equal to clean plus quarantined, which means a row went missing without a rule claiming it |
| schema difference | inside `cityflow ingest` | each source file | a column the vintage map expects and the file lacks, which stops the build; an unexpected column, which is printed as information |
| dbt tests | `make transform` | the warehouse | {{ n:dbt_tests }} data tests, including the singular assertions for the zone join fan out and the tip nullability rules |
| component coverage | inside `cityflow publish` | `metrics.yml` against `COMPONENT_SQL` | a metric whose component no aggregate emits |
| per file and total size | inside `cityflow publish`, and `make size` | the shipped directory | a file over budget, and an unexpected artifact type in the shipped directory |
| reconcile | `make reconcile` | warehouse against shipped layer | any of the {{ n:metric_count }} metrics whose two expressions disagree, at four grains |
| published figures | `make claims` | the rendered documents | a figure in a document that the warehouse does not produce |
| metric layer enforcement | `make metric-gate` | `web/src` and `web/app` | a metric computed in the page instead of read from the catalog |
| palette | `make palette` | the eight scale runs | a colour scale that fails contrast, separation or monotonicity in either mode |
| self contained export | `web` job in `ci.yml` | `web/out` | any third party origin reaching the built page |
| smoke test | `make web-test` | the built export | a panel that does not render, and the query log the bench script folds in |

The four CI jobs map onto those: `lint`, `test`, `gates` and `pipeline`, plus
`web`. `gates` deliberately runs against the committed shipped layer and the
committed documents rather than rebuilding them, because what it is checking is
what a reader will actually see.
