# Results

Every figure on this page was produced by a query against the warehouse and
substituted into this document by `scripts/check_published_numbers.py`. Nothing
here was typed by hand, and the gate fails the build if this file and the
warehouse disagree. Built from commit `b21e1fb` on
`2026-09-21T21:23:21.188996+00:00`.

**Provenance.** These figures were measured on the `synthetic` backend
over 42 months, July 2021 to
December 2024. Read
[the provenance section of docs/data.md](docs/data.md#provenance) before quoting
any of them. The short version: the generator reproduces the TLC schema and its
defects, not New York City. Run `make ingest-tlc` and this document re-renders
itself from the real files.

---

## 1. Scale

| | |
| --- | --- |
| Source rows read | 92,634,577 |
| Rows landed in the warehouse | 92,208,701 |
| Rows quarantined | 425,876 (0.4597 percent) |
| Source files | 126 across 3 services and 42 months |
| Schema vintages exercised | 5 |
| Rows in `fct_trip` | 92,208,701 |
| Ingest wall time | 217 seconds |
| dbt models | 14, with 150 data tests |

The window spans 5 schema vintages rather than one, on purpose.
A build window inside a single vintage exercises the per vintage mapping in
exactly one way and proves nothing about it. This one crosses the month the
airport fee column appears in the published files, which is what makes the null
rate panel show a real schema change and what stops
`assert_surcharge_null_before_introduction` passing vacuously.

## 2. Quarantine

Nothing is deleted. Every rejected row is written to a log with the rule that
caught it, the counts are on the data health panel, and the identity
`source rows = clean rows + sum(quarantine counts by rule)` is asserted after
every month. Rules are evaluated first match wins, so a row carries exactly one
label and these counts sum to the total without double counting.

| rule | rows caught | share of source |
| --- | --- | --- |
| `non_positive_distance` | 286,664 | 0.3095 percent |
| `non_positive_fare` | 50,819 | 0.0549 percent |
| `implausible_speed` | 38,930 | 0.0420 percent |
| `passenger_count_invalid` | 27,263 | 0.0294 percent |
| `timestamp_out_of_period` | 10,957 | 0.0118 percent |
| `dropoff_before_pickup` | 7,390 | 0.0080 percent |
| `exact_duplicate` | 3,557 | 0.0038 percent |
| `duration_too_long` | 294 | 0.0003 percent |
| `distance_too_long` | 2 | 0.0000 percent |

9 of the twelve defined rules fired over this
window. The thresholds are in `config/cityflow.yml` and every one of them is
explained in [docs/data.md](docs/data.md); the pipeline and the document read
the same object, so they cannot drift.

**Unknown zones are deliberately not quarantined.** Zone ids 264 and 265 mean
the TLC could not determine the location. They carry
1,574,148 trips, 1.71 percent of
the fact table. They are kept, flagged, and excluded from geographic aggregates
only, with the exclusion and its share stated on the map itself. Dropping them
would be the easy version and it would bias every citywide total downwards.

## 3. The tip rate

| | share of fare |
| --- | --- |
| Averaged over every payment type, yellow and green | 14.1453 percent |
| Card payments only, where a tip is observable | 20.3497 percent |
| Understatement | 43.86 percent of the correct figure |

3,402,805 trips were settled in cash, which is
26.69 percent of metered trips. Each records a tip
of zero because no tip passed through the meter. `tip_pct` is NULL on
3,887,107 rows for that reason and is never zero, and three
singular dbt tests hold that line: no `tip_pct` on an unobservable payment, no
zero `tip_pct` on a cash trip, and the fact table row count matching staging so
a filter cannot quietly remove the population being argued about.

For hire vehicle trips carry no payment type column because every one of them
is settled in the app. That is a constant rather than a missing value, so their
zeroes are real zeroes and they are included.

## 4. The schema change

The airport fee column is entirely null for 54
service months, through December 2024, and starts
carrying values in January 2022. The congestion
surcharge is entirely null for 0 service
months and the central business district fee for
126, because neither exists in the vintages this window
covers for the services that do not collect them.

A column a vintage does not have is modelled as NULL, never as zero. Zero would
say "we charged nothing", the series would run flat into a step, and somebody
would eventually explain that step in a slide. `mart_null_rates` puts the
transition on screen: a null share of exactly 1.0 followed by a drop is a column
being introduced, not a data quality problem, and the panel says so.

## 5. The geometry

The official TLC shapefile holds 263 polygon
records covering 260 distinct zone ids.
2 zones are split across several records and are dissolved
by zone id. 3 zone ids that the trip records
reference have no polygon at all, because the mapping folded them into their
neighbours; they are carried with `has_geometry` false rather than dropped.
`dim_zone` therefore holds exactly 265 rows, one per zone id
from 1 to 265, and `zones.geojson` holds 260 features.

This was found by a fact table that came out heavier than its own staging
model. The size of that difference is described rather than quoted, because it
was measured on a build at a scale this repository no longer runs at, and a
figure this document cannot re-derive is a figure it does not state.

A duplicate zone id fans out any join on it and inflates every count
downstream; an absent one vanishes from an inner join. Both failures are
silent. There is now a test that rebuilds both zone joins from staging and
asserts the row count is unchanged.

## 6. Concentration

23 zones carry half of all trips with a known
pickup location. The busiest is East Williamsburg. The Pareto panel draws
both the per zone share and the cumulative line on one axis, because two marks
that are shares of the same total belong on one axis, and a second axis there
would be two charts pretending to be one.

## 7. Statistics

**Wilson intervals** on every published proportion, not the normal
approximation. The approximation is wrong at exactly the counts a zone and hour
filter produces, which is exactly when somebody reads it.

**Multiple comparisons.** 757 zone comparisons were run,
each zone against its own service's rate rather than against a pooled one: app
trips mostly do not tip and card trips mostly do, so a pooled zone rate measures
service mix rather than tipping, and every Manhattan zone would come back
different for the wrong reason.

25 of those comparisons have a raw p
value at or below 0.05. Testing 757 hypotheses at five
percent would be expected to flag about
38 by chance alone, which is what
happened. After Benjamini-Hochberg at q = 0.05,
0 survive. The panel states the comparison count
on screen and marks only survivors.

**Changepoints.** The search runs on the STL trend component rather than the
observed series, because observed volume carries a weekly cycle with an
amplitude larger than most level shifts and a detector cannot tell a shift from
the sixth Friday in a row. PELT found 35 candidates;
25 moved the level by less than
6 percent and are not annotated, leaving
10 on the chart. A detected shift is a change in
level, not a cause, and the panel says so.

**The one check with a known right answer.** The generator plants a single level
shift in for hire volume on 2024-08-12, a
-12.0 percent step. The detector placed a
changepoint 1 day from that date and
measured the step at
-7.59 percent over the surrounding
four weeks. The measured figure is smaller than the planted one because it is a
twenty eight day mean on either side of a trend that was already moving, which
is the honest thing for it to be.

## 8. The shipped layer

92,208,701 rows at trip grain are summarized into
67.38 MB across 23 files, the largest of them
31.77 MB against a 95 MB per file
budget.

| file | rows | MB |
| --- | --- | --- |
| `agg_daily.parquet` | 3,840 | 0.20 |
| `agg_daily_decomposition.parquet` | 3,840 | 0.10 |
| `agg_duration_dist.parquet` | 4,281 | 0.01 |
| `agg_fare_distance.parquet` | 2,204 | 0.04 |
| `agg_hour_of_week.parquet` | 124,446 | 3.98 |
| `agg_od_flow.parquet` | 2,926,052 | 31.77 |
| `agg_zone_hour.parquet` | 929,826 | 25.03 |
| `detail_2024_06.parquet` | 303,524 | 5.93 |
| `dim_date.parquet` | 1,280 | 0.01 |
| `dim_hour.parquet` | 24 | 0.00 |
| `dim_ratecode.parquet` | 8 | 0.00 |
| `dim_service.parquet` | 3 | 0.00 |
| `dim_zone.parquet` | 265 | 0.01 |
| `mart_null_rates.parquet` | 630 | 0.00 |
| `mart_quarantine.parquet` | 790 | 0.01 |
| `mart_source_freshness.parquet` | 126 | 0.00 |
| `zone_comparisons.parquet` | 757 | 0.03 |
| `bench.json` |  | 0.00 |
| `changepoints.json` |  | 0.00 |
| `lineage.json` |  | 0.01 |
| `manifest.json` |  | 0.01 |
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
| pulse | KPI tiles, whole window | 1 | 31.01 | 33.99 | 34.06 | 25.03 | 15 | 0 percent |
| pulse | Daily volume with trend | 3,840 | 5.59 | 5.88 | 8.61 | 0.20 | 1 | 0 percent |
| pulse | Hour of week grid | 168 | 1.94 | 2.01 | 2.02 | 3.98 | 2 | 0 percent |
| pulse | Hour of week, one service | 168 | 2.14 | 2.29 | 2.33 | 3.98 | 2 | 50 percent |
| geography | Choropleth, trips by zone | 265 | 7.18 | 8.80 | 9.21 | 25.03 | 15 | 0 percent |
| geography | One zone drilled, every hour | 24 | 3.59 | 4.22 | 4.25 | 25.03 | 15 | 60 percent |
| geography | Top origin destination flows | 150 | 35.75 | 37.19 | 37.62 | 31.77 | 45 | 0 percent |
| geography | Flows out of one zone | 263 | 3.09 | 3.38 | 3.46 | 31.77 | 45 | 87 percent |
| behavior | Duration ridgeline | 1,440 | 2.27 | 2.31 | 2.34 | 0.01 | 1 | 0 percent |
| behavior | Fare against distance hexbin | 1,261 | 2.38 | 2.45 | 2.46 | 0.04 | 1 | 0 percent |
| mix | Service share by month | 126 | 9.87 | 10.04 | 10.41 | 25.03 | 15 | 0 percent |
| geography | Detail month, one zone at trip grain | 500 | 10.13 | 12.17 | 12.86 | 5.93 | 3 | 33 percent |

Median p95 across the panel queries is 5.88 ms and the
slowest is 37.19 ms.

The pruning column is the interesting one. It is computed from the parquet
footer, not from a timing: for each query's filter it reads the column
statistics of every row group and counts how many can be skipped outright.
4 of the 12 queries carry a
filter that prunes, and the best of them skips
87 percent of the file's row groups. That number is
a property of how each file was sorted and written, and it is what turns a scan
into two HTTP range requests.

**Bytes actually pulled over the wire** cannot be measured here, because a local
read is a file read. The Playwright smoke test counts the range requests
DuckDB-WASM issues in the browser and `scripts/bench_queries.py` folds that log
into the table above. Over the 6 panel queries whose
browser traffic was captured, DuckDB-WASM pulled
9,728,953.00 bytes in 195 range
requests from files totalling 61,028,941.00 bytes on disk.
That is 15.9 percent of the bytes those files
contain, and zero whole file reads.

The clearest single case is the Hour of week grid query, which
fetched 448.8 KB from a
3.98 MB file. Nothing about that is a property of
DuckDB: it is a property of having sorted the file on the column the query
filters, so the row groups the query does not need are skippable from the
footer.

## 10. Limitations

These are real limits, not a disclaimer.

**The figures are generated, not measured from New York.** Stated at the top,
in the README, in `docs/data.md` and on the dashboard itself. The pipeline, the
schema handling, the quarantine rules, the metric layer and the statistics are
the real thing and run identically on the published files. The volumes, fares
and geography of demand are not a measurement of the city. The zone boundaries,
names and boroughs are the real TLC shapefile.

**The generator applies its monthly seasonal factor as a step at each month
boundary.** Real demand does not jump on the first of the month. This is
visible to the changepoint search, which is part of why
25 candidates fall below the effect floor.

**Zone popularity ordering is judgement.** The busiest zones were named from
domain knowledge rather than measured, because a hash based ordering put
Governor's Island fourth busiest in Manhattan and a reader who has stood on a
corner in this city would see that immediately. The volumes are generated; the
ordering is a choice, and `docs/data.md` says so.

**The detail month is yellow and green only.** A month of for hire records does
not fit inside the per file budget, and splitting it across files to get under
a per file limit would be dodging the budget rather than meeting it. The
interface says which services the drill down covers.

**`service_zone` is derived, not sourced.** It comes from borough plus the
three airport zone ids rather than from the official lookup file, which this
machine could not fetch. `docs/data.md` states the derivation.

**The trend, the seasonal decomposition and the changepoints describe this
window only.** 42 months is enough for a weekly seasonal and a
trend. It is not enough to say anything about annual seasonality, and this
document does not.
