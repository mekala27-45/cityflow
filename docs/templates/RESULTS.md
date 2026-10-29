# Results

Every figure on this page was produced by a query against the warehouse and
substituted into this document by `scripts/check_published_numbers.py`. Nothing
here was typed by hand, and the gate fails the build if this file and the
warehouse disagree. Built from commit `{{ raw:commit }}` on
`{{ raw:generated_at }}`.

**Provenance.** These figures were measured on the `{{ raw:backend }}` backend
over {{ n:months }} months, {{ month:window.start }} to
{{ month:window.end }}: the trip records the TLC publishes,
{{ n:source_files }} files and {{ f1:source_gb }} GB, read by
`.github/workflows/refresh.yml` on a GitHub Actions runner. Read
[the provenance section of docs/data.md](docs/data.md#provenance) before quoting
any of them, for what the build found in the files before it read a row.

---

## 1. Scale

| | |
| --- | --- |
| Source rows read | {{ n:source_rows }} |
| Rows landed in the warehouse | {{ n:clean_rows }} |
| Rows quarantined | {{ n:quarantined_rows }} ({{ f4:quarantine_share_pct }} percent) |
| Source files | {{ n:source_files }} across {{ n:services }} services and {{ n:months }} months |
| Schema vintages exercised | {{ n:vintages }} |
| Rows in `fct_trip` | {{ n:fct_trip_rows }} |
| Ingest wall time | {{ f0:ingest_seconds }} seconds |
| dbt models | {{ n:dbt_models }}, with {{ n:dbt_tests }} data tests |

The window spans {{ n:vintages }} schema vintages rather than one, on purpose.
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

{{ table:quarantine_by_rule }}

{{ n:quarantine_rules_fired }} of the twelve defined rules fired over this
window. The thresholds are in `config/cityflow.yml` and every one of them is
explained in [docs/data.md](docs/data.md); the pipeline and the document read
the same object, so they cannot drift.

**Unknown zones are deliberately not quarantined.** Zone ids 264 and 265 mean
the TLC could not determine the location. They carry
{{ n:unknown_zone_trips }} trips, {{ f2:unknown_zone_share_pct }} percent of
the fact table. They are kept, flagged, and excluded from geographic aggregates
only, with the exclusion and its share stated on the map itself. Dropping them
would be the easy version and it would bias every citywide total downwards.

## 3. The tip rate

| | share of fare |
| --- | --- |
| Averaged over every payment type, yellow and green | {{ f4:tip_rate_naive_pct }} percent |
| Card payments only, where a tip is observable | {{ f4:tip_rate_correct_pct }} percent |
| Understatement | {{ f2:tip_rate_understatement_pct }} percent of the correct figure |

{{ n:cash_trips }} trips were settled in cash, which is
{{ f2:cash_share_of_metered_pct }} percent of metered trips. Each records a tip
of zero because no tip passed through the meter. `tip_pct` is NULL on
{{ n:tip_pct_null_rows }} rows for that reason and is never zero, and three
singular dbt tests hold that line: no `tip_pct` on an unobservable payment, no
zero `tip_pct` on a cash trip, and the fact table row count matching staging so
a filter cannot quietly remove the population being argued about.

For hire vehicle trips carry no payment type column because every one of them
is settled in the app. That is a constant rather than a missing value, so their
zeroes are real zeroes and they are included.

## 4. The schema change

The airport fee column is entirely null for {{ n:airport_fee_null_months }}
service months, through {{ month:airport_fee_last_all_null }}, and starts
carrying values in {{ month:airport_fee_first_populated }}. The congestion
surcharge is entirely null for {{ n:congestion_surcharge_null_months }} service
months and the central business district fee for
{{ n:cbd_fee_null_months }}, because neither exists in the vintages this window
covers for the services that do not collect them.

A column a vintage does not have is modelled as NULL, never as zero. Zero would
say "we charged nothing", the series would run flat into a step, and somebody
would eventually explain that step in a slide. `mart_null_rates` puts the
transition on screen: a null share of exactly 1.0 followed by a drop is a column
being introduced, not a data quality problem, and the panel says so.

## 5. The geometry

The official TLC shapefile holds {{ n:shapefile_polygon_records }} polygon
records covering {{ n:zones_with_geometry }} distinct zone ids.
{{ n:zones_dissolved }} zones are split across several records and are dissolved
by zone id. {{ n:zones_without_geometry }} zone ids that the trip records
reference have no polygon at all, because the mapping folded them into their
neighbours; they are carried with `has_geometry` false rather than dropped.
`dim_zone` therefore holds exactly {{ n:distinct_zones }} rows, one per zone id
from 1 to 265, and `zones.geojson` holds {{ n:geojson_features }} features.

This was found by a fact table that came out heavier than its own staging
model. The size of that difference is described rather than quoted, because it
was measured on a build at a scale this repository no longer runs at, and a
figure this document cannot re-derive is a figure it does not state.

A duplicate zone id fans out any join on it and inflates every count
downstream; an absent one vanishes from an inner join. Both failures are
silent. There is now a test that rebuilds both zone joins from staging and
asserts the row count is unchanged.

## 6. Concentration

{{ n:zones_for_half_of_trips }} zones carry half of all trips with a known
pickup location. The busiest is {{ raw:busiest_zone }}. The Pareto panel draws
both the per zone share and the cumulative line on one axis, because two marks
that are shares of the same total belong on one axis, and a second axis there
would be two charts pretending to be one.

## 7. Statistics

**Wilson intervals** on every published proportion, not the normal
approximation. The approximation is wrong at exactly the counts a zone and hour
filter produces, which is exactly when somebody reads it.

**Multiple comparisons.** {{ n:zone_comparisons }} zone comparisons were run,
each zone against its own service's rate rather than against a pooled one: app
trips mostly do not tip and card trips mostly do, so a pooled zone rate measures
service mix rather than tipping, and every Manhattan zone would come back
different for the wrong reason.

{{ n:zone_comparisons_naive_significant }} of those comparisons have a raw p
value at or below 0.05, where testing {{ n:zone_comparisons }} hypotheses at
five percent would flag about {{ f0:zone_comparisons_expected_by_chance }} by
chance alone. After Benjamini-Hochberg at q = 0.05,
{{ n:zone_comparisons_after_bh }} survive. At these sample sizes the correction
is not what separates the zones; a difference of a fraction of a point is
significant behind a million trips, so the panel shows the size of each
difference with its interval, states the comparison count, and marks only
survivors.

**Changepoints.** The search runs on the STL trend component rather than the
observed series, because observed volume carries a weekly cycle with an
amplitude larger than most level shifts and a detector cannot tell a shift from
the sixth Friday in a row. PELT found {{ n:changepoints_found }} candidates;
{{ n:changepoints_below_effect_floor }} moved the level by less than
{{ f0:changepoint_min_effect_pct }} percent and are not annotated, leaving
{{ n:changepoints_annotated }} on the chart. A detected shift is a change in
level, not a cause, and the panel says so.

**The largest shift.** The biggest annotated change in level is
{{ raw:changepoint_largest }}. The detector says when the level moved and by
how much; why is a question for the calendar, and the panel leaves it there.
The detector itself is checked where the answer is known:
`packages/stats/tests/test_pelt.py` plants changepoints in a series, fails
unless PELT finds each one close to where it was put, and checks the pruned
search against unpruned optimal partitioning.

## 8. The shipped layer

{{ n:fct_trip_rows }} rows at trip grain are summarized into
{{ f2:shipped_mb }} MB across {{ n:shipped_files }} files, the largest of them
{{ f2:largest_shipped_mb }} MB against a {{ f0:file_budget_mb }} MB per file
budget.

{{ table:shipped }}

No aggregate contains a rate. Each contains the numerator and the denominator,
and the metric layer defines the division. A published rate cannot be
re-aggregated: averaging the tip rates of two zones gives the wrong answer for
both together, and somebody always does it. Sums can be added, which is the
whole reason this layer is built out of sums.

## 9. Query latency and row group pruning

Measured locally against the same parquet the browser reads, {{ n:bench_queries }}
queries, fifteen repeats each after a warm run. Latency here is the lower bound:
no network, no WebAssembly.

{{ table:bench }}

Median p95 across the panel queries is {{ f2:bench_p95_median_ms }} ms and the
slowest is {{ f2:bench_p95_max_ms }} ms.

The pruning column is the interesting one. It is computed from the parquet
footer, not from a timing: for each query's filter it reads the column
statistics of every row group and counts how many can be skipped outright.
{{ n:bench_queries_pruned }} of the {{ n:bench_queries }} queries carry a
filter that prunes, and the best of them skips
{{ f0:bench_best_pruning_pct }} percent of the file's row groups. That number is
a property of how each file was sorted and written, and it is what turns a scan
into two HTTP range requests.

**Bytes actually pulled over the wire** cannot be measured here, because a local
read is a file read. The Playwright smoke test counts the range requests
DuckDB-WASM issues in the browser and `scripts/bench_queries.py` folds that log
into the table above. Over the {{ n:bench_browser_queries }} panel queries whose
browser traffic was captured, DuckDB-WASM pulled
{{ f2:bench_browser_bytes }} bytes in {{ n:bench_browser_requests }} range
requests from files totalling {{ f2:bench_browser_file_bytes }} bytes on disk.
That is {{ f1:bench_browser_fetched_pct }} percent of the bytes those files
contain, and zero whole file reads.

The clearest single case is the {{ raw:bench_tightest_query }} query, which
fetched {{ f1:bench_tightest_kb }} KB from a
{{ f2:bench_tightest_file_mb }} MB file. Nothing about that is a property of
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
three airport zone ids rather than from the official lookup file, which this
machine could not fetch. `docs/data.md` states the derivation.

**The trend, the seasonal decomposition and the changepoints describe this
window only.** {{ n:months }} months is enough for a weekly seasonal and a
trend. It is not enough to say anything about annual seasonality, and this
document does not.
