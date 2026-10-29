# Results

Every figure on this page was produced by a query against the warehouse and
substituted into this document by `scripts/check_published_numbers.py`. Nothing
here was typed by hand, and the gate fails the build if this file and the
warehouse disagree. Built from commit `240cae6` on
`2026-10-29T07:45:48.271752+00:00`.

**Provenance.** These figures were measured on the `tlc` backend
over 48 months, January 2021 to
December 2024: the trip records the TLC publishes,
144 files and 24.1 GB, read by
`.github/workflows/refresh.yml` on a GitHub Actions runner. Read
[the provenance section of docs/data.md](docs/data.md#provenance) before quoting
any of them, for what the build found in the files before it read a row.

---

## 1. Scale

| | |
| --- | --- |
| Source rows read | 1,012,369,990 |
| Rows landed in the warehouse | 1,004,405,361 |
| Rows quarantined | 7,964,629 (0.7867 percent) |
| Source files | 144 across 3 services and 48 months |
| Schema vintages exercised | 5 |
| Rows in `fct_trip` | 1,004,405,361 |
| Ingest wall time | 1,374 seconds |
| dbt models | 14, with 150 data tests |

The window spans 5 schema vintages rather than one, on purpose.
A build window inside a single vintage exercises the per vintage mapping in
exactly one way and proves nothing about it. This one crosses the month the
airport fee was first charged, which is what makes the null rate panel show a
real schema change and what stops
`assert_surcharge_null_before_introduction` passing vacuously.

## 2. Quarantine

Nothing is deleted. Every rejected row is written to a log with the rule that
caught it, the counts are on the data health panel, and the identity
`source rows = clean rows + sum(quarantine counts by rule)` is asserted after
every month. Rules are evaluated first match wins, so a row carries exactly one
label and these counts sum to the total without double counting.

| rule | rows caught | share of source |
| --- | --- | --- |
| `passenger_count_invalid` | 2,367,465 | 0.2339 percent |
| `non_positive_distance` | 2,250,758 | 0.2223 percent |
| `non_positive_fare` | 2,188,663 | 0.2162 percent |
| `duration_too_short` | 734,191 | 0.0725 percent |
| `dropoff_before_pickup` | 159,956 | 0.0158 percent |
| `duration_too_long` | 156,521 | 0.0155 percent |
| `implausible_speed` | 83,498 | 0.0082 percent |
| `distance_too_long` | 14,288 | 0.0014 percent |
| `exact_duplicate` | 7,204 | 0.0007 percent |
| `timestamp_out_of_period` | 1,981 | 0.0002 percent |
| `fare_too_large` | 82 | 0.0000 percent |
| `negative_component` | 22 | 0.0000 percent |

12 of the 13 defined rules fired over
this window. The thresholds are in `config/cityflow.yml` and every one of them is
explained in [docs/data.md](docs/data.md); the pipeline and the document read
the same object, so they cannot drift.

**Unknown zones are deliberately not quarantined.** Zone ids 264 and 265 mean
the TLC could not determine the location. They carry
36,477,941 trips, 3.63 percent of
the fact table. They are kept, flagged, and excluded from geographic aggregates
only, with the exclusion and its share stated on the map itself. Dropping them
would be the easy version and it would bias every citywide total downwards.

## 3. The tip rate

| | share of fare |
| --- | --- |
| Averaged over every payment type, yellow and green | 20.2584 percent |
| Card payments only, where a tip is observable | 25.9255 percent |
| Understatement, as a share of the naive figure | 27.97 percent |

25,920,891 trips were settled in cash, which is
17.70 percent of metered trips. Each records a tip
of zero because no tip passed through the meter. `tip_pct` is NULL on
35,027,630 rows, the cash trips and every other payment that
cannot carry a tip, and is never zero, and three
singular dbt tests hold that line: no `tip_pct` on an unobservable payment, no
zero `tip_pct` on a cash trip, and the fact table row count matching staging so
a filter cannot quietly remove the population being argued about.

For hire vehicle trips carry no payment type column because every one of them
is settled in the app. That is a constant rather than a missing value, so their
zeroes are real zeroes and they are included.

## 4. The schema change

The airport fee column starts carrying values in
April 2021. It is entirely null in
54 service months, the last of them
December 2024, because green never collects it: every
green month in the window is null, as is every yellow and for hire month before
the fee. The congestion surcharge, which every vintage in this window collects,
is entirely null in 0 service months, and
the central business district fee, which arrives after the window, in
144.

A column a vintage does not have is modelled as NULL, never as zero. Zero would
say "we charged nothing", the series would run flat into a step, and somebody
would eventually explain that step in a slide. `mart_null_rates` puts the
transition on screen: a null share of exactly 1.0 followed by a drop is a column
being introduced, not a data quality problem, and the panel says so.

## 5. The geometry

The TLC zone shapefile, as the published build fetched it, holds
263 polygon records covering
263 distinct zone ids, so 0 zones
needed dissolving and 0 ids the trip records can
name have no polygon. `dim_zone` holds exactly 265 rows, one
per zone id from 1 to 265, the two unknown codes among them, and
`zones.geojson` holds 263 features.

The older copy the first builds read was not so tidy. It repeated Corona's id
and the harbour islands' id across several records and had no polygon for
three ids the trip records use, which was found by a fact table that came out
heavier than its own staging model. The size of that difference is described
rather than quoted, because it was measured on a build this repository no
longer runs, and a figure this document cannot re-derive is a figure it does
not state. The dissolve and the rows for zones with no polygon stay, and the
tests run them against a fixture of that older layout, so they hold whichever
copy the next build meets.

A duplicate zone id fans out any join on it and inflates every count
downstream; an absent one vanishes from an inner join. Both failures are
silent. There is now a test that rebuilds both zone joins from staging and
asserts the row count is unchanged.

## 6. Concentration

50 zones carry half of all trips with a known
pickup location. The busiest is JFK Airport. The Pareto panel draws
both the per zone share and the cumulative line on one axis, because two marks
that are shares of the same total belong on one axis, and a second axis there
would be two charts pretending to be one.

## 7. Statistics

**Wilson intervals** on every published proportion, not the normal
approximation. The approximation is wrong at exactly the counts a zone and hour
filter produces, which is exactly when somebody reads it.

**Multiple comparisons.** 738 zone comparisons were run,
each zone against its own service's rate rather than against a pooled one: app
trips mostly do not tip and card trips mostly do, so a pooled zone rate measures
service mix rather than tipping, and every Manhattan zone would come back
different for the wrong reason.

706 of those comparisons have a raw p
value at or below 0.05, where testing 738 hypotheses at
five percent would flag about 37 by
chance alone. After Benjamini-Hochberg at q = 0.05,
705 survive. At these sample sizes the correction
is not what separates the zones; a difference of a fraction of a point is
significant behind a million trips, so the panel shows the size of each
difference with its interval, states the comparison count, and marks only
survivors.

**Changepoints.** The search runs on the STL trend component rather than the
observed series, because observed volume carries a weekly cycle with an
amplitude larger than most level shifts and a detector cannot tell a shift from
the sixth Friday in a row. PELT found 35 candidates;
4 moved the level by less than
6 percent and are not annotated, leaving
31 on the chart. A detected shift is a change in
level, not a cause, and the panel says so.

**The largest shift.** The biggest annotated change in level is
in green taxi volume on 2021-12-23, down 41.2 percent between the four weeks before and the four weeks after. The detector says when the level moved and by
how much; why is a question for the calendar, and the panel leaves it there.
The detector itself is checked where the answer is known:
`packages/stats/tests/test_pelt.py` plants changepoints in a series, fails
unless PELT finds each one close to where it was put, and checks the pruned
search against unpruned optimal partitioning.

## 8. The shipped layer

1,004,405,361 rows at trip grain are summarized into
150.16 MB across 23 files, the largest of them
55.20 MB against a 95 MB per file
budget.

| file | rows | MB |
| --- | --- | --- |
| `agg_daily.parquet` | 4,386 | 0.28 |
| `agg_daily_decomposition.parquet` | 4,386 | 0.12 |
| `agg_duration_dist.parquet` | 4,392 | 0.01 |
| `agg_fare_distance.parquet` | 7,940 | 0.14 |
| `agg_hour_of_week.parquet` | 131,115 | 5.15 |
| `agg_od_flow.parquet` | 4,407,534 | 55.20 |
| `agg_zone_hour.parquet` | 1,235,387 | 39.25 |
| `detail_2024_06.parquet` | 3,439,819 | 49.68 |
| `dim_date.parquet` | 1,463 | 0.01 |
| `dim_hour.parquet` | 24 | 0.00 |
| `dim_ratecode.parquet` | 8 | 0.00 |
| `dim_service.parquet` | 3 | 0.00 |
| `dim_zone.parquet` | 265 | 0.01 |
| `mart_null_rates.parquet` | 720 | 0.01 |
| `mart_quarantine.parquet` | 1,224 | 0.01 |
| `mart_source_freshness.parquet` | 144 | 0.00 |
| `zone_comparisons.parquet` | 738 | 0.03 |
| `bench.json` |  | 0.00 |
| `changepoints.json` |  | 0.01 |
| `lineage.json` |  | 0.01 |
| `manifest.json` |  | 0.02 |
| `metric_catalog.json` |  | 0.01 |
| `zones.geojson` |  | 0.20 |

No aggregate contains a rate. Each contains the numerator and the denominator,
and the metric layer defines the division. A published rate cannot be
re-aggregated: averaging the tip rates of two zones gives the wrong answer for
both together, and somebody always does it. Sums can be added, which is the
whole reason this layer is built out of sums.

## 9. Query latency and row group pruning

Measured locally against the same parquet the browser reads, 12
queries, fifteen repeats each after a warm run. Latency here is the lower bound:
no network, no WebAssembly.

| panel | query | rows returned | p50 ms | p95 ms | p99 ms | file MB | row groups | pruned |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pulse | KPI tiles, whole window | 1 | 25.89 | 26.23 | 26.26 | 39.25 | 19 | 0 percent |
| pulse | Daily volume with trend | 4,386 | 4.57 | 4.72 | 4.82 | 0.28 | 1 | 0 percent |
| pulse | Hour of week grid | 168 | 1.87 | 2.35 | 2.44 | 5.15 | 3 | 0 percent |
| pulse | Hour of week, one service | 168 | 1.64 | 1.76 | 1.98 | 5.15 | 3 | 33 percent |
| geography | Choropleth, trips by zone | 263 | 4.89 | 5.04 | 5.56 | 39.25 | 19 | 0 percent |
| geography | One zone drilled, every hour | 24 | 2.15 | 2.53 | 2.54 | 39.25 | 19 | 68 percent |
| geography | Top origin destination flows | 150 | 59.31 | 59.86 | 59.94 | 55.20 | 68 | 0 percent |
| geography | Flows out of one zone | 260 | 2.47 | 2.66 | 2.75 | 55.20 | 68 | 88 percent |
| behavior | Duration ridgeline | 1,464 | 1.68 | 1.71 | 1.72 | 0.01 | 1 | 0 percent |
| behavior | Fare against distance hexbin | 2,754 | 3.35 | 3.51 | 3.62 | 0.14 | 1 | 0 percent |
| mix | Service share by month | 147 | 16.60 | 16.84 | 16.93 | 39.25 | 19 | 0 percent |
| geography | Detail month, one zone at trip grain | 500 | 10.59 | 11.11 | 11.44 | 49.68 | 28 | 86 percent |

Median p95 across the panel queries is 4.72 ms and the
slowest is 59.86 ms.

The pruning column is the interesting one. It is computed from the parquet
footer, not from a timing: for each query's filter it reads the column
statistics of every row group and counts how many can be skipped outright.
4 of the 12 queries carry a
filter that prunes, and the best of them skips
88 percent of the file's row groups. That number is
a property of how each file was sorted and written, and it is what turns a scan
into two HTTP range requests.

**Bytes actually pulled over the wire** cannot be measured here, because a local
read is a file read. The Playwright smoke test counts the range requests
DuckDB-WASM issues in the browser and `scripts/bench_queries.py` folds that log
into the table above. Over the 6 panel queries whose
browser traffic was captured, DuckDB-WASM pulled
30,000,076 bytes in 289 range
requests from files totalling 100,027,616 bytes on disk.
That is 30.0 percent of the bytes those files
contain, and zero whole file reads.

The clearest single case is the Hour of week grid query, which
fetched 694.8 KB from a
5.15 MB file. Nothing about that is a property of
DuckDB: it is a property of having sorted the file on the column the query
filters, so the row groups the query does not need are skippable from the
footer.

## 10. Limitations

These are real limits, not a disclaimer.

**The files are the TLC's, and so are their limits.** A trip is what a meter
or a dispatch system recorded. Cash tips are not recorded at all, which is the
finding at the top of the README; a zone of 264 or 265 is a location the TLC
could not resolve; and the for hire files publish no total, so it is rebuilt
from its parts. The quarantine removes the rows that cannot be trips and names
the rule that removed each one; it does not try to repair what a meter wrote.

**The published files are revised.** The TLC reissues months, and the parquet
it republished in 2022 carries columns back to years that never collected them.
This build reads what the files say on the day it runs, and the monthly refresh
opens a pull request listing every figure that moved, so a revision shows up as
a diff rather than as a quiet change.

**The window opens in the pandemic recovery.** Volume in early 2021 is still
far below where it settles, so the trend and the changepoints over this window
are a recovery as much as a pattern, and nothing here is a claim about the years
either side of it.

**The detail month is yellow and green only.** A month of for hire records does
not fit inside the per file budget, and splitting it across files to get under
a per file limit would be dodging the budget rather than meeting it. The
interface says which services the drill down covers.

**`service_zone` is derived, not sourced.** It comes from borough plus the
three airport zone ids rather than from the TLC's lookup file, which the
machine this repository was written on could not fetch. `docs/data.md` states
the derivation.

**The trend, the seasonal decomposition and the changepoints describe this
window only.** The decomposition models a weekly cycle and a trend, and the
annual cycle stays inside the trend: 48 months is four of them, the
first spent in the recovery, which is too few to separate one from the other,
and this document does not try.
