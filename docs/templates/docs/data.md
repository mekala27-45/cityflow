# The data

This is the credibility document. It states every cleaning decision this
pipeline makes, what each one costs in rows, and why the threshold is where it
is. If you are deciding whether to believe a number on the dashboard, this is
the page that should decide it.

Every measured figure below is a placeholder rendered from
`web/public/data/manifest.json` by `scripts/check_published_numbers.py`, which
re-runs the query behind it against the warehouse and fails the build when the
document and the warehouse disagree. That includes the things a data document
usually copies by hand and then lets rot: the quarantine thresholds are read
from the config object the pipeline used, the injected defect rates are read
from the same place the generator read them, and the per rule removal counts and
the shipped file sizes are rendered as whole tables rather than as rows somebody
typed. If a threshold in `config/cityflow.yml` changes, this page changes with
it or the build fails.

## Scale

{{ n:source_rows }} source rows were read from {{ n:source_files }} files,
covering {{ n:months }} months from {{ month:first_period }} to
{{ month:last_period }}, across {{ n:services }} services and
{{ n:vintages }} schema vintages. {{ n:clean_rows }} rows landed in the
warehouse. {{ n:quarantined_rows }} were quarantined, which is
{{ f2:quarantine_share_pct }} percent of what was read. The fact table holds
{{ n:fct_trip_rows }} rows.

Nothing was deleted. Every rejected row is written to `quarantine_log` under the
name of the rule that caught it, the counts are on the dashboard, and the ingest
asserts after every month that

    source rows = clean rows + sum of the per rule quarantine counts

with no rule overlapping another. A pipeline that cannot say where its rejected
rows went does not get to describe itself as clean.

## Provenance

**Read this before you read any number above or below it.**

Every row of `source_audit` records the backend that produced it, and the build
manifest carries the distinct set of names it found there. For the figures in
this repository right now, that is `{{ raw:backend }}`. It is derived from the
audit table rather than from the config file, so a warehouse assembled from more
than one backend would say so here instead of presenting a blend as though it
came from one place.

When that includes `synthetic`, the figures on this page and on the dashboard
were measured on data produced by the seeded generator in
`packages/ingest/src/cityflow_ingest/synthetic.py`, and not on the published TLC
files. The seed is `{{ raw:synthetic_seed }}`, fixed, so the same window
rebuilds to the same numbers.

**What the generator reproduces.** The schema and its defects. It writes parquet
carrying the exact column names of the vintage it is asked for, so the per
vintage mapping in the next section is exercised by the generator rather than
bypassed by it. It injects each of the defects the quarantine rules catch, at
the configured rates tabulated below. It
reproduces the shape of demand well enough for the panels to be worth looking
at: separate weekday and weekend hour profiles, a day of week effect, a monthly
seasonal factor, a slow secular drift, a holiday dip read from the same seed
`dim_date` reads, and a speed by hour curve that gives the duration ridgeline a
reason to exist.

**What the generator does not reproduce.** Real trip volumes. Real fares. Real
geography of demand beyond a borough level gradient and a named ordering of busy
zones. Any finding whatsoever about New York City. A tip rate computed here is a
correct computation over generated numbers, and the thing it demonstrates is the
computation, not the city.

**The injected defect rates.** These are read out of the same config object the
generator read, so the table below cannot disagree with the pipeline. Each
entry's name is the name of the quarantine rule that catches it, except
`unknown_zone`, which is not a quarantine rule at all and has its own section
below.

| injected defect | rate |
| --- | --- |
| `non_positive_distance` | {{ f3:defect_rate_pct.non_positive_distance }} percent of rows |
| `passenger_count_zero` | {{ f3:defect_rate_pct.passenger_count_zero }} percent |
| `non_positive_fare` | {{ f3:defect_rate_pct.non_positive_fare }} percent |
| `implausible_speed` | {{ f3:defect_rate_pct.implausible_speed }} percent |
| `timestamp_out_of_period` | {{ f3:defect_rate_pct.timestamp_out_of_period }} percent |
| `dropoff_before_pickup` | {{ f3:defect_rate_pct.dropoff_before_pickup }} percent |
| `exact_duplicate` | {{ f3:defect_rate_pct.exact_duplicate }} percent |
| `unknown_zone` (kept, not quarantined) | {{ f3:defect_rate_pct.unknown_zone }} percent |

They are chosen to sit in the same order of magnitude as the rates in the real
files, so the quarantine panel is not a cartoon. They are drawn from disjoint
bands of one uniform variate, so a row carries at most one injected defect and
the realised rate of each is the configured rate rather than the configured rate
conditioned on the others not firing.

**There is a planted level shift.** The generator applies a step change to daily
volume on {{ iso:planted_changepoint }}, so that the changepoint detector has
something real to find and the annotation on the chart can be checked against a
known answer. The step is {{ f1:planted_shift_pct.yellow }} percent on yellow,
{{ f1:planted_shift_pct.green }} percent on green, and
{{ f1:planted_shift_pct.fhvhv }} percent on for hire vehicles, which is the arm
large enough to be worth detecting. If you find a level shift in the generated
series on or near that date, that is this constant and it is not a finding. What the detector did with it is in
[statistics.md](statistics.md), and it is the only check in this pipeline with a
known right answer.

**The monthly seasonal factor is applied as a step at each month boundary.**
Real demand does not jump on the first of the month; it drifts. The generator
multiplies each day by a factor chosen per calendar month, which means volume
changes discontinuously at midnight on the first and then holds flat for the
rest of the month. This is the most consequential artefact in the generated
series, because it is exactly the shape a changepoint detector is built to find.
It is the reason most of the annotated changepoints on this build sit within a
few days of a month boundary, and the reason the changepoint panel on generated
data is largely a picture of this paragraph. On the real files the same
detector, unchanged, has no such artefact to find.

**The zone ordering is judgement, not measurement.** The generator has to decide
which zones are busy. A hash is defensible and reproducible, and the first
attempt at it put Governor's Island fourth busiest in Manhattan. Anyone who has
stood on a corner in this city sees that immediately, and once a reader sees one
thing that is obviously wrong they stop trusting the rest of the chart. So the
busy zones are named by hand in `PRIORITY_ZONES`, the tail is ordered by a fixed
hash rather than by zone id (the TLC numbered its zones alphabetically, and
ranking by id produces a choropleth that is a gradient from Alphabet City to
Yorkville, an artefact that looks exactly like a finding), and a list of parks,
cemeteries and islands is held down by a multiplier. The volumes are generated.
The ordering is a person's opinion about the city. On this build the busiest
pickup zone is {{ raw:busiest_zone }}, and {{ n:zones_for_half_of_trips }} zones
account for half of all trips with a resolved pickup: both of those are
properties of that opinion crossed with a Zipf fall off, not of New York.

To build against the real files instead, set `CITYFLOW_BACKEND=tlc` or run
`make ingest-tlc`, and read [runbook.md](runbook.md) first. The backend name is
printed on every report and written on every audit row precisely so that a
figure measured on generated data cannot be quoted without its label.

## The per vintage schema mapping

The TLC has changed the trip record schema repeatedly since 2009. Column names
change case. Timestamp columns change prefix with the service. The location
columns stop being coordinates and start being zone ids. Surcharge columns
appear in the middle of the history. The high volume for hire vehicle files are
a different shape entirely: no passenger count, no rate code, no payment type,
distance and fare under different names, no published total, and the operator
carried as a licence number that has to be looked up.

The mapping is a table, in `packages/ingest/src/cityflow_ingest/vintage.py`. It
is not inferred from whatever columns happen to be in a file.

Inference works until the day two vintages share a column name with different
semantics, and then it is wrong quietly. The table makes three things possible
that inference does not. A file that does not carry the columns its vintage
expects fails the ingest with the difference printed, rather than producing a
column of nulls nobody notices. A column the file carries that the project does
not model is distinguished from a column the project did not expect, which are
very different problems: the first is noise, the second is the TLC adding
something and is information worth printing. And a vintage can declare that it
predates zone ids, so the coordinate era files are refused with a clear message
about needing a spatial join rather than half supported.

| vintage | service | what it is |
| --- | --- | --- |
| `yellow_v1_coordinates` | yellow | raw pickup and dropoff coordinates, no zone ids. Refused. |
| `yellow_v2_zones` | yellow | zone ids arrive. The first vintage this project can read. |
| `yellow_v3_congestion` | yellow | `congestion_surcharge` appears. |
| `yellow_v4_airport_fee` | yellow | `airport_fee` appears. |
| `yellow_v5_cbd` | yellow | `cbd_congestion_fee` appears. |
| `green_v1_coordinates` | green | as yellow v1, with `lpep_` timestamp prefixes. Refused. |
| `green_v2_zones` | green | zone ids arrive. |
| `green_v3_congestion` | green | `congestion_surcharge` appears. |
| `green_v4_cbd` | green | `cbd_congestion_fee` appears. Green never gets an airport fee. |
| `fhvhv_v1` | fhvhv | no passenger count, rate code, payment type or total. `bcf` and `sales_tax` instead. |
| `fhvhv_v2_airport_fee` | fhvhv | `airport_fee` appears. |
| `fhvhv_v3_cbd` | fhvhv | `cbd_congestion_fee` appears. |

The date boundaries are in the code, one per vintage, and they are the month a
column first appears **in a published file**, not the month the rule took
effect. Those two differ, and the file is what we read.

Every vintage maps onto one canonical row, `CANONICAL_COLUMNS` in
`normalize.py`, and nothing downstream reads a vintage column name. The staging
layer renames nothing, because the ingest already made the naming decisions
across every published vintage and a second vocabulary would mean every
conversation about a column starts by asking which name is meant.

Two normalization decisions need stating.

**The for hire total is reconstructed, and it is not the same construction as
the yellow total.** Those files publish no total, so `_total_expression` sums
base fare, tolls, tips, black car fund, sales tax, congestion surcharge, airport
fee and CBD fee. Missing parts are coalesced to zero *inside that sum only*,
because a component a vintage does not collect genuinely contributed nothing to
what the rider paid, which is a different claim from saying the surcharge itself
was zero. The `revenue` metric's description says so, so that nobody compares a
for hire total to a yellow total without knowing the difference.

**`service_zone` is derived here, not read from the official lookup.** The TLC
publishes an authoritative lookup carrying that column. This repository does not
use it. `_service_zone` in `packages/publish/src/cityflow_publish/geometry.py`
derives the value from the two facts that are actually in the shapefile: the
three airport zone ids by id, Manhattan is the yellow zone, Newark is EWR, every
other borough is a boro zone, and the unknown codes get `N/A`. That is stated
plainly rather than presented as a sourced column, because a derived column
wearing the costume of a sourced one is how a downstream consumer ends up
trusting it more than it deserves.

## The quarantine rules

Rules are evaluated in the order below and the first match wins, so a row
carries exactly one label and the per rule counts sum to the quarantine total
without double counting. The order is worst first: a row whose timestamps are
nonsense is labelled for that rather than for the implausible speed the nonsense
timestamps imply.

Every threshold in the table below is read from the same config object the
pipeline compared against, so the document and the code cannot disagree about
what was applied.

| rule | catches | threshold | why the threshold is where it is |
| --- | --- | --- | --- |
| `null_timestamp` | pickup or dropoff is null | none | a trip with no timestamp has no duration, no speed and no slot in the date dimension. |
| `timestamp_out_of_period` | pickup outside the file's own month | {{ n:threshold.period_tolerance_days }} days either side | the published files contain trips dated years outside their own month, including 2001 and 2098. Two days absorbs a genuine trip that starts on the last night of a month and is recorded in the next file, and admits none of the nonsense. |
| `dropoff_before_pickup` | a zero or negative duration | none | not a threshold question. Any duration at or below zero is a record error. |
| `duration_too_short` | implausibly brief trips | under {{ n:threshold.min_trip_seconds }} seconds | below this it is a meter toggled on and off, not a trip. Low enough that a genuine short hop survives. |
| `duration_too_long` | implausibly long trips | over {{ f0:threshold.max_trip_hours }} hours | a meter left running. Above this the duration is measuring a driver's shift, not a journey. |
| `non_positive_distance` | distance null, zero or negative | none | the largest rule by volume. A zero distance produces an infinite implied speed and a meaningless revenue per mile. |
| `distance_too_long` | trips beyond the service area | over {{ f0:threshold.max_trip_miles }} miles | beyond the furthest plausible destination for a metered trip originating in the service area. Deliberately generous, so a genuine long run to a neighbouring state survives. |
| `non_positive_fare` | fare null, zero or negative | none | a trip that charged nothing did not happen, or happened and was not recorded. Either way it cannot enter a fare distribution. |
| `fare_too_large` | fare above the ceiling | over {{ f0:threshold.max_fare_amount }} dollars | a data entry error. Set well above any legitimate flat rate or long run plus tolls. |
| `implausible_speed` | distance over duration above the ceiling | over {{ f0:threshold.max_implied_speed_mph }} mph | above this is a record error, not a fast driver: it exceeds any sustained speed achievable on the streets, bridges and parkways in the service area, while leaving a legitimate airport run at highway speed alone. |
| `passenger_count_invalid` | zero, negative, or above the fleet maximum | over {{ n:threshold.max_passenger_count }} passengers | the largest licensed vehicle in the fleet. Null is deliberately left alone: the for hire files do not record passenger count at all, and not recording a value is not the same as recording nonsense. |
| `negative_component` | a negative tip, toll or surcharge | none | these appear in the published files as reversal rows that were never matched to their original. |
| `exact_duplicate` | a byte for byte repeat within one file | none | usually a re submitted batch. Found by a window over a business key rather than by a predicate, so it is evaluated after the rule list. The business key deliberately excludes `trip_id`, which we assign and which is unique by construction. The first occurrence is kept. |

A sample of up to `QUARANTINE_SAMPLE_PER_RULE` rejected rows per rule per month
is kept in `quarantine_sample`, so the data health panel can show a reader what
a caught row actually looks like rather than only how many there were.

### What each rule removed

{{ table:quarantine_by_rule }}

**Read the share column carefully, because its denominator is a trap.** These
shares are taken against every source row in the window, which is what makes the
column sum to the headline {{ f4:quarantine_share_pct }} percent exactly. That
is the right denominator for "how much of this build did this rule remove". It
is the wrong denominator for "how often does this rule fire", and the difference
is not small. `passenger_count_invalid` cannot fire on a for hire vehicle file
at all, because those files carry no passenger count column, and for hire
records are the large majority of the window. Measured against the rows it could
actually have caught, that rule fires several times more often than the table
says. The same caveat applies in a milder form to every rule whose predicate
reads a column that only some vintages carry. If you want a per population rate,
`mart_quarantine` carries `source_rows` per service and per period and the data
health panel cuts it that way.

Of the twelve defined rules plus the duplicate check,
{{ n:quarantine_rules_fired }} fired over this window. The ones that never fired
are `null_timestamp`,
`duration_too_short`, `fare_too_large` and `negative_component`, and that is a
statement about the fixture rather than about the rules. The generator injects
no null timestamps, no sub minimum durations, no absurd fares and no negative
components, so those four have nothing to catch here. On the published files all
four fire. A rule with a zero count is not evidence that the rule is
unnecessary; it is evidence about the data it was pointed at, and this is the
kind of thing a generated fixture cannot tell you.

Two rules fired on rows nobody planted. `duration_too_long` and
`distance_too_long` caught the tail of the generated distance and duration
distributions rather than an injected defect, which is a small piece of evidence
that the rules are catching a class of row and not just the exact shape of the
thing that was put in front of them.

## The tip trap

This is the decision that most changes a published number, and it is the reason
this project exists in its current shape.

Yellow and green trips record a `payment_type` code. On a cash trip, the meter
records a tip of zero. That zero does not mean the passenger left nothing. It
means no tip passed through the meter, because the tip, if there was one, was
cash handed to the driver. The zero is a missing value wearing a number, and it
is indistinguishable from a real zero once it is inside an average.

On this build, {{ n:cash_trips }} trips were settled in cash, which is
{{ f1:cash_share_of_metered_pct }} percent of yellow and green trips. If you
average `tip_amount / fare_amount` over all of them, you get
{{ f2:tip_rate_naive_pct }} percent. If you average it only over the trips whose
payment channel actually records a tip, you get {{ f2:tip_rate_correct_pct }}
percent. The correct figure is {{ f1:tip_rate_understatement_pct }} percent
higher than the naive one. That is the gap between a number that looks fine and
a number that is right, and it is roughly the gap between most published New
York tip rates and the truth.

So the modelling decision is: **null, not zero.**

`tip_is_observable` is computed in the normalizer, not at query time, and
travels with the row. The payment channel is the thing that decides whether a
recorded zero means anything, and deciding that in six different places at query
time is how six different tip rates get published. In `fct_trip`, `tip_pct` is

```sql
case when tip_is_observable then tip_amount / nullif(fare_amount, 0) end
```

with no `else`. {{ n:tip_pct_null_rows }} rows carry a null `tip_pct` for that
reason. A null is not a defect in this column; it is the correct answer to a
question the data cannot answer.

Two dbt tests hold the line from opposite directions.
`assert_tip_pct_null_for_unobservable` fails if a cash, disputed or no charge
trip ever acquires a tip percentage. `assert_tip_pct_never_zero_for_cash` aims
at the specific mistake of coalescing an unobservable tip to zero, which is what
somebody will eventually do to make a chart stop showing gaps.

**For hire vehicle zeroes are real zeroes, and they are included.** Those trips
carry no payment type column because every one of them is settled in the app.
That is not a missing value, it is a constant, and `_payment_expression` returns
the literal `'app'` for them rather than a null. When an app trip records no
tip, the app saw the transaction and there was no tip. Excluding those zeroes
would be the mirror image of the yellow mistake: it would report the tip rate
among people who tipped, which is close to a tautology.

The consequence is that the two services answer the same question differently,
and the metric layer says so. `tip_rate` is a ratio of sums over the observable
population. `tipped_share` is the proportion of observable trips that left
anything at all. They measure how much people tip and how often, they are not
the same number, and only one of them takes a Wilson interval. See
[metrics.md](metrics.md) and [statistics.md](statistics.md).

## Unknown zones 264 and 265

The TLC's zone ids 264 and 265 mean the location could not be determined. 264 is
labelled Unknown and 265 is Outside of NYC. They are not a data quality problem
to be cleaned away. They are the single most common way a New York taxi analysis
goes quietly wrong.

On this build they carry {{ n:unknown_zone_trips }} trips, which is
{{ f2:unknown_zone_share_pct }} percent of the fact table. Those trips are real.
They have real timestamps, real distances and real fares. They contribute to
every revenue total and every trip count. What they do not have is a polygon,
and so they join to nothing.

The failure mode is an inner join. Join `fct_trip` to `dim_zone` on zone id with
an inner join and those rows vanish, silently, and every borough share on the
map moves without anything erroring. The totals on the map stop agreeing with
the totals on the KPI tiles and the difference is too small to notice and too
large to ignore once somebody does.

So the policy is: **keep, flag, and exclude from geographic aggregates only.**

- `prepare_zones` emits both ids as rows in `dim_zone` with a null geometry and
  `is_unknown` true, so every downstream consumer has to decide what to do with
  them rather than never seeing them.
- `int_trip_geography` resolves the flags with a left join and
  `coalesce(..., true)`, so an id that fails to resolve is treated as unknown
  rather than as null. A null `has_known_geography` reads as "no" in some tools
  and as "yes" in others.
- `fct_trip` carries `has_known_geography` as a real boolean.
- `build_od_flow` filters on it, and it is the only shipped builder that does,
  because a flow from unknown to unknown is not a flow. The docstring says so
  and the metric layer publishes `unknown_zone_share` precisely so the size of
  the omission is visible rather than silent.
- `assert_unknown_zones_present_but_flagged` fails in both directions: if the
  unknown rows have all disappeared, something upstream started dropping rows
  instead of labelling them; if they are present but not flagged, they will be
  drawn on a map at coordinates they do not have.

The dashboard states the excluded share on the geography panel. A reader who
wants to know how much of the city is missing from the map is told, on the map.

## The shapefile

The official TLC taxi zone shapefile does not contain one polygon per zone id,
and assuming it does breaks a warehouse in a way that does not error.

It holds {{ n:shapefile_polygon_records }} polygon records covering
{{ n:zones_with_geometry }} distinct zone ids. {{ n:zones_dissolved }} zones are
split across several records, accounting for
{{ n:zones_multi_part_polygons }} of those records between them: Corona is two
polygons, and the three harbour islands are three.
{{ n:zones_without_geometry }} ids that the trip records can and do reference
have no polygon at all, because the mapping folded them into their neighbours:
57, 104 and 105. Add the two unknown codes and `dim_zone` holds
{{ n:distinct_zones }} rows, which is every id the TLC publishes, while
`zones.geojson` carries {{ n:geojson_features }} features, one per id that has a
shape to draw.

**What went wrong before it was fixed.** The zone reference was published one row
per shapefile record. The two multi part zones therefore appeared two and three
times. Joining the fact table to that reference on zone id fanned out every trip
that touched one of those zones, and the fact table came out heavier
than its own staging model, spread across every month in the window. Nothing
errored. Nothing looked wrong. Every total was simply slightly too high, in a
direction and a magnitude that no eyeball would catch, and only a row count
comparison would ever have said so.

**How it was fixed.** `prepare_zones` gathers every polygon record under its zone
id first, dissolves the parts with `unary_union`, and emits one row per zone. The
three geometryless ids are emitted explicitly from `GEOMETRYLESS_ZONE_IDS`,
carrying their real name and borough with `has_geometry` false. That distinction
matters and `dim_zone.sql` spells it out: a zone with no geometry is a real place
whose trips have a known location that simply cannot be drawn, while an unknown
code is a trip whose location was never resolved. Counting the first as the
second would overstate the unresolved share.

Three guards keep it fixed. `assert_fct_trip_zone_join_does_not_fan` rebuilds the
zone join on its own and asserts it does not change the row count, deliberately
rebuilt rather than read off `fct_trip` so a failure points at the dimension and
not at the incremental predicate. `assert_dim_zone_counts` asserts the two counts
as written out literals, so that a change in the geometry step which drops or
splits a zone is a deliberate edit with a reason in a commit message rather than
a map with a hole in it that nobody can date. And `part_count` is carried into
`dim_zone`, so a zone that gains a part is visible rather than inferred.

Three more decisions in that step are worth knowing. The simplification runs in
the source projection, EPSG:2263, in US survey feet, before reprojection to
EPSG:4326, because a tolerance in feet is a distance a person can reason about
and a tolerance in degrees after reprojection is not. Coordinates are rounded to
a precision well below the simplification tolerance, so the rounding throws away
nothing the simplification has not already thrown away and removes about a fifth
of the bytes. And the centroid is taken on the full geometry rather than the
simplified one, so the flow map arcs land where the zone actually is; for the
three harbour islands that puts the point in the water between them, which is
the honest answer for a zone made of three islands.

## Surcharge columns are null before introduction, never zero

Four surcharge columns arrive part way through the published history. A vintage
that does not have a column produces a typed NULL for it, through `_nullable` in
the normalizer. It never produces a zero.

Zero would say "this was charged and came to nothing". The series would show a
flat line running into a step, and somebody would eventually explain that step
in a slide. NULL says "not collected", which is what happened.

`airport_fee` is the measurable case in this window, and it is the reason the
window starts where it does. `mart_null_rates` watches it per service and per
source period, and reports {{ n:airport_fee_null_months }} service months in
which the column is entirely null. The first period in which it carries a value
is {{ month:airport_fee_first_populated }}, which is the month the column appears
in the published files. That step is visible on the null rate panel as a step,
not averaged away, because the mart counts per period rather than over the
window.

The last service month in which `airport_fee` is entirely null is
{{ month:airport_fee_last_all_null }}, which is the last month of the window,
and that is not a contradiction. Green never receives the column at all: there
is no `green_v*_airport_fee` vintage, so every green month from
{{ month:window.start }} to {{ month:window.end }} is legitimately all null. A
null rate panel that only showed the earliest all null month would make this
look like a historical boundary. It is a per service property of the schema, and
the mart is cut per service so that it reads as one.

The other two surcharges bracket the window rather than sitting inside it.
`congestion_surcharge` arrives at the `*_v3_congestion` and `fhvhv_v1`
boundaries, all of which precede this window, so it is entirely null in
{{ n:congestion_surcharge_null_months }} service months, which is none of them.
`cbd_congestion_fee`
arrives at the `*_cbd` boundaries, which follow this window, so it is entirely
null in all {{ n:cbd_fee_null_months }} service months here, which is every
service month there is. Those two are the control cases: a column that is never
null and a column that is always null, either side of one that changes. The
build window in `config/cityflow.yml` is chosen to span the introduction in the
middle on purpose, because a window that sits inside a single vintage proves
nothing about the vintage mapping and lets
`assert_surcharge_null_before_introduction` pass vacuously while looking like a
test.

There is one place a surcharge is coalesced to zero, and it is deliberate.
`fct_trip.surcharge_total` sums the components with `coalesce(..., 0)`, because a
charge that did not exist contributed nothing to what the rider paid, and a null
there would null the whole total and take the trip out of every fare composition
chart. Whether the column existed at all is a separate question, and
`mart_null_rates` is where it is answered.

## What the build watches about itself

Three marts ship alongside the aggregates and are read by the data health panel.

`mart_quarantine` carries the per rule removal counts as a share of what the
source actually held. The absolute count alone is close to useless: a rule that
removes forty thousand rows is alarming until you know the month held twenty
million. The share is what a reader can compare between months and between
services.

`mart_null_rates` watches five columns that all arrive part way through the
history, per service and per source period. The columns are listed as written
literals in a union rather than produced by an unpivot, so adding one to the
watch list is a visible edit rather than a silent change in what the panel
covers.

`mart_source_freshness` carries `has_gap`, which exists because a missing month
is invisible in a line chart: the line joins the two months either side of the
hole and reads as a smooth decline. Flagging the gap lets the panel break the
line instead of drawing through it. It also keeps the byte count and the elapsed
seconds per ingest, so the panel can say whether a month is small because the
source was small or because the read was cut short. This build spent
{{ f0:ingest_seconds }} seconds in ingest across every month.
