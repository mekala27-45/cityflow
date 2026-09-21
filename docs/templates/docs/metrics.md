# The metric layer

Every number this product publishes is defined once, in `metrics/metrics.yml`.
This document is about the mechanism that makes that sentence true rather than
aspirational.

Every analytics organisation eventually spends a quarter arguing about why two
dashboards disagree about revenue. The cause is almost never a bug. It is that
the number was written twice, by two people, at two grains, and both versions
are defensible in isolation. A definitions file on its own does not fix that. A
definitions file is a document about the code, and a document about the code is
a thing the code can drift away from.

What makes this one load bearing is that each metric carries two expressions
which are checked against each other, and that a gate refuses to let the page
compute a metric the file already defines.

The layer currently defines {{ n:metric_count }} metrics, of which
{{ n:metrics_with_interval }} carry a confidence interval and the rest carry a
written statement of why they do not. Those two counts are the only figures in
this document, and they are rendered from the manifest like every other figure
in this repository. Everything else a reader might want (the current list, each
metric's description, both of its expressions, its components and its interval)
is rendered to `web/public/data/metric_catalog.json` at publish time and
displayed by the dashboard's own metric panel, which is generated from the same
file the page queries with. A second copy of the catalog, written by hand into a
document, is exactly the artefact this layer exists to abolish.

## Adding a metric, in order

Do these in this order. The order is not arbitrary: each step is the thing that
makes the next step's failure legible instead of silent.

**1. Decide the kind before you write anything.** `sum`, `mean`, `ratio` or
`proportion`. The kind decides which interval applies and whether an interval
applies at all, and `Metric.__post_init__` enforces the consequences. Getting
this wrong is the most expensive mistake available here, because a proportion
mislabelled as a mean gets a Student t interval that is wrong near the
boundaries, and nothing about the output looks unusual.

**2. Write the description, and the `interval_note` if there is no interval.**
Both are validated at load. A metric with no interval and no `interval_note`
raises `MetricLayerError` the moment the file is read, which means
`cityflow metrics list`, `cityflow publish`, `cityflow reconcile` and the
catalog write all fail together. You will not get as far as a dashboard.

**3. Name the `components`.** These are the additive columns the browser
expression needs. A metric with an empty list raises at load.

**4. Add the SQL for any new component to `COMPONENT_SQL`** in
`packages/publish/src/cityflow_publish/aggregates.py`. If you skip this,
`verify_components` raises a `KeyError` at the start of `cityflow publish`,
naming the component. That is the whole point of the `components` list: without
it, the metric would publish fine, the aggregate would simply not contain the
column, and the browser expression would return nulls in the interface. A
loud failure at build time costs a minute. Nulls in a KPI tile cost a week and
usually get reported by somebody outside the team.

**5. Write both expressions.** `warehouse` is SQL over `fct_trip`, at trip
grain, and it is the truth. `browser` is SQL over the additive columns of the
shipped aggregate, and it is what DuckDB-WASM actually runs in the page. Write
them independently. Do not derive one from the other, because two expressions
that agree only because one was copied from the other are not evidence of
anything.

**6. If it is a `proportion`, name `numerator` and `denominator`.** They are the
component columns the browser uses to build the Wilson interval. A proportion
without them raises at load, because the interval cannot be constructed in the
page from an expression alone.

**7. Rebuild the shipped layer: `make publish`.** Until you do, the aggregate on
disk does not carry the new component column.

**8. Run `make reconcile`.** This is the step that turns the definition into a
constraint. It is also the step people skip, because everything appears to work
without it.

**9. Wire the page through the catalog, never by hand.** The page reads
`metric_catalog.json` and splices the layer's own `browser_sql` into its query.
If you instead write `sum(tipped_trips) / sum(tip_obs_trips)` in a component,
`make metric-gate` fails.

**10. If a document quotes the number, add a claim and re-render.** Claims live
in `packages/publish/src/cityflow_publish/manifest.py`; run
`cityflow manifest` and then `make docs`. A figure that a query does not produce
does not go in a document.

## The two expressions, and what `cityflow reconcile` proves

`reconcile` evaluates both expressions and fails the build when they disagree by
more than a floating point tolerance. Neither expression is derived from the
other and they read different files, so agreement is evidence rather than
tautology.

It checks four grains, not one:

```
overall  ──────────────────────  the whole window, one number each side
by service  ───────────────────  one number per service
by month  ─────────────────────  one number per month
by service and month  ─────────  the cross
```

Four rather than one because a metric can agree in total and disagree
everywhere underneath. That is the usual shape of a grain bug: a filter dropped
from an aggregate, or a join that fans out for one service, cancels out in the
sum and does not cancel out in the parts. Checking only the total is how that
ships.

The comparison uses a full outer join on the dimension columns with
`is not distinct from`, so a key present on one side and absent on the other is a
disagreement rather than a row that quietly does not get compared. The tolerance
is relative and tight. These are sums of the same doubles in a different order,
so the only legitimate difference is floating point associativity; anything
larger is real.

One detail is written out rather than hidden in an alias: the fact table's month
column is derived as `date_trunc('month', pickup_date)`, and the shipped
aggregate already carries a `month` column. `_warehouse_dimensions` performs that
rename explicitly, because a silent rename is how a reconcile check ends up
comparing two different groupings and passing.

What this gives you, concretely: ship an aggregate that quietly drops a filter,
or write a metric whose warehouse expression forgets its own population, and the
build breaks with the metric name, the grain, the key and both values.

## Why `filters` is documentation and not a `WHERE` clause

`filters` describes the population a metric is defined over. It is rendered as a
comment by `warehouse_sql`. It is never applied.

This looks backwards until you follow the alternative through. If the layer
applied `filters` as a `WHERE` on the warehouse side, it would have to apply the
equivalent restriction on the browser side too. But the browser side cannot: the
shipped components are pre aggregated, and the population is already baked into
which component you read. `tip_obs_trips` is a count of the observable trips,
full stop; there is no row left to filter.

So the population has to be encoded inside each expression. `tip_rate`'s
warehouse expression says `sum(case when tip_is_observable then tip_amount end)`
and its browser expression says `sum(tip_obs_tip_sum)`. Both state the
population, in the vocabulary available to them.

And that is what makes the check work. An expression that forgets its filter
disagrees with the other expression, and `reconcile` catches it. If `filters`
were applied automatically, a metric could forget its population in one
expression and still pass, because the layer would have silently corrected it in
exactly one place. The population is stated once per side, checked against
itself, and documented in a field that cannot secretly change the answer.

## The four kinds, and which interval each takes

| kind | what it is | interval | why |
| --- | --- | --- | --- |
| `sum` | an additive total | none, and none is permitted | a total of the rows in the window is the population, not a sample of it. There is no sampling here to put an interval around, and `__post_init__` raises if you try. |
| `mean` | the mean of a per trip quantity | `student-t` | the sampling distribution is governed by the spread of the per trip values, so the interval is built from a standard error and a t critical value. |
| `proportion` | a count over a count | `wilson`, and it is mandatory | see below. |
| `ratio` | a ratio of two sums | none, with a mandatory `interval_note` | see below. |

**A proportion must carry Wilson.** The layer refuses any other choice, with a
message that says why: the normal approximation is wrong at the counts a zone
and hour filter produces, which is exactly when somebody reads it. A zone on
this map carries a few dozen trips between three and five in the morning. At
those counts the Wald interval leaves the unit interval, and at zero or full
success it collapses to a point of zero width, which reads on a chart as
certainty. Wilson inverts the score test instead, stays inside the unit
interval by construction, and is defined at the boundaries.
[statistics.md](statistics.md) has the arithmetic.

**A sum must carry none.** `trips` is a count of the rows that passed every
quarantine rule in the selected window. It is not an estimate of some larger
population of trips. Putting an interval on it would imply a sampling process
that did not happen, and the implication is worse than the missing number
because a reader has no way to tell it is spurious.

**A ratio carries none, and has to say so.** `mean_speed_mph` is total distance
over total time, which is the number that answers "how fast does the fleet
move", because it weights a forty minute crawl more than a two minute hop. The
mean of per trip speeds answers a different question and is the one people
compute by accident. A delta method interval on a ratio of sums would be
dominated by the covariance between numerator and denominator, which the shipped
components do not carry, and quoting an interval without that term implies a
precision this layer cannot support. So the note says that, names the related
metric that does carry a proper interval, and the field is required so that the
absence is a statement rather than an oversight.

That last requirement is the general rule: **a metric with no interval must
carry an `interval_note`.** Without it, a reader cannot tell the difference
between a number that needs no interval and a number whose interval somebody
forgot. Those two look identical on a screen and they are not remotely the same
claim. Making the note mandatory turns "no interval" into a sentence somebody
had to write and a reviewer could disagree with.

`tip_rate` and `tipped_share` exist as a pair for this reason. One is a ratio of
sums, weighted by fare, and it answers how much people tip. The other is a
proportion and it answers how often. They are not the same quantity, only one of
them takes a Wilson interval, and both of their descriptions say so.

## What the enforcement gate scans for

`scripts/check_metric_layer.py` reads the component names out of
`metrics/metrics.yml` and scans `web/src` and `web/app` for two patterns.

**An aggregate over a component column.** `sum(tip_obs_tip_sum)` in a string
literal anywhere outside the allowlist is a metric being written by hand,
whatever the variable it lands in is called.

**Arithmetic between two component columns.** `row.tipped_trips /
row.tip_obs_trips` is the tipped share, computed in TypeScript. It will agree
with the definition today and drift from it the moment the definition changes,
and nothing will announce that it has.

The allowlist holds two files and each entry carries its reason in the source.
`src/lib/metrics.ts` is the catalog module: it loads `metric_catalog.json` and
splices the layer's own `browser_sql` into queries, which is the mechanism the
gate exists to protect. `src/lib/sql.ts` holds the query builders, which select
component columns and hand them to the catalog's expression; the gate's second
pattern still applies to them, so they are permitted to name a component and not
to divide one by another.

**Row level display arithmetic is deliberately not a violation, and that is a
decision rather than a gap in the regex.** The detail table derives a trip's
implied speed from its distance and its duration, and its dropoff time from its
pickup time and its duration. Those are functions of one row. They are not
aggregates over a population and the metric layer does not define them: there is
no `implied_speed` metric to drift from. They exist in the page because shipping
three exactly derivable columns at trip grain would cost a meaningful share of
the size budget for no information, which is explained in `build_detail_month`.

The distinction the gate draws is between arithmetic on a row and arithmetic
over a population, and it is the right line. A metric is a statement about a
group, and a statement about a group is the thing that can be computed two ways
and disagree. Extending the gate to catch row level division would fail the
detail table for doing exactly what the architecture asks it to do, and the
usual response to a gate that fails correct code is to disable the gate.

The test for this gate includes a deliberate violation fixture, because a gate
whose only test asserts that the current tree passes is not a gate. It is an
assertion that nobody has broken it yet.
