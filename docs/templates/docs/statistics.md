# The statistics

Four methods carry the claims this dashboard makes: a Wilson score interval on
every proportion, an STL decomposition under every trend statement, PELT behind
every annotated level shift, and a Benjamini-Hochberg correction over the zone
comparisons. This document says why each one was chosen over the thing most
people reach for first, and what it would cost to get it wrong.

Measured figures below are placeholders rendered from
`web/public/data/manifest.json`. The handful of method parameters that are not
measured are named rather than quoted: `FDR_Q`, `STL_PERIOD`,
`CHANGEPOINT_MIN_SIZE` and `CHANGEPOINT_PENALTY_SCALE` live in
`packages/publish/src/cityflow_publish/analysis.py`, and the confidence level
defaults on the interval functions themselves. Each method is checked against
published worked examples and against an independent implementation, in
`packages/stats/tests`, rather than against last week's output.

## Wilson, and why not the normal approximation

A proportion on this dashboard is a count over a count: the share of a zone's
observable trips that left a tip, the share that touched an airport, the share
settled by card. The obvious interval for it is the Wald interval,

    p ± z · sqrt( p(1 - p) / n )

and it is wrong in exactly the places this dashboard puts it.

Three failures, in increasing order of how bad they look on a screen.

**It leaves the unit interval.** At small `n` with `p` near either end, the
lower bound goes below zero or the upper bound goes above one. A tipped share of
minus four percent is obviously wrong, which is the least harmful outcome,
because somebody notices.

**It collapses at the boundary.** At `p = 0` the standard error is exactly zero,
so the interval is a point of zero width. A zone with no tipped trips out of a
few dozen observable ones renders as a measurement with no uncertainty at all.
That is not a wide interval that a reader can discount; it is the visual
vocabulary of certainty, applied to the case with the least information in it.
The same happens at `p = 1`.

**Its coverage is poor well before those extremes,** in an oscillating way that
depends on `n` and `p` together, so there is no simple rule of thumb that
rescues it.

None of this is academic here. The dashboard lets a reader filter to one zone
and one hour. A zone on this map carries a few dozen trips between three and
five in the morning. That filter is one click away and it is exactly the case
somebody will screenshot.

The Wilson score interval inverts the score test rather than the Wald test. Its
centre is

    ( p + z²/(2n) ) / ( 1 + z²/n )

and its half width is

    ( z / (1 + z²/n) ) · sqrt( p(1-p)/n + z²/(4n²) )

Three properties follow from that algebra and they are the reasons it is here.
The centre is pulled towards one half by an amount that shrinks as `n` grows, so
a small sample is visibly shrunk rather than silently trusted. The `z²/(4n²)`
term under the root keeps the half width positive when `p(1-p)` is zero, so the
interval is defined and non degenerate at zero and at full success. And the
bounds cannot leave the unit interval algebraically; `wilson_interval` clamps
only against floating point drift where centre and spread are nearly equal.

One deliberate choice inside the implementation: **the reported point estimate is
the raw proportion, not the Wilson centre.** The centre is shrunk towards one
half, and reporting it as "the" tipped share would disagree with the number the
same query returns without an interval. A metric that changes value depending on
whether you asked for its uncertainty is worse than no uncertainty at all. The
interval is therefore asymmetric about the point, which
`test_interval_is_not_symmetric_about_the_point_estimate` asserts rather than
assumes, because the asymmetry is the method working.

A group with no trials returns `None` for all three values, and the array form
returns NaN. Returning zero there would be read as a measured zero by every
consumer downstream, and it would plot as a zero bar next to zones that were
actually measured.

Means get `mean_ci`, a Student t interval, because the sampling distribution of
a mean of a continuous quantity is governed by the spread of the per trip
values, not by `n` and `p`. A single observation returns a point with no bounds;
the alternative is an infinite interval, which is correct and useless.

## The tip rate and the tipped share are different quantities

This is the mistake the previous section exists to prevent, and it is worth its
own heading because the two numbers are both called "tip rate" in conversation.

**`tip_rate` is a ratio of sums.** Total tips over total fare, across the trips
whose payment channel records a tip. Each trip enters weighted by its fare, so a
forty dollar airport run counts for more than a six dollar hop. This is the
number that answers "what share of the money paid to drivers is tip".

**`tipped_share` is a proportion.** The count of observable trips that left
anything at all, over the count of observable trips. Each trip counts once. This
is the number that answers "how often do people tip".

They move independently. A zone where everybody tips a little and a zone where a
few people tip a lot can have the same `tip_rate` and very different
`tipped_share`. Publishing one and calling it the other is a category error, not
a rounding difference.

Only `tipped_share` takes a Wilson interval, and the reason is structural rather
than stylistic. Wilson is an interval for a binomial proportion: it needs a
numerator that counts successes and a denominator that counts Bernoulli trials,
and the whole derivation rests on that. `tip_rate` has neither. Its numerator is
a sum of dollars and its denominator is a sum of dollars, and the trips
contributing to them are not exchangeable, because each one carries a different
weight. Feeding those two sums to `wilson_interval` would produce a number, and
that number would mean nothing.

The honest interval for `tip_rate` is a delta method interval on a ratio of
sums, and it would be dominated by the covariance between the tip total and the
fare total, which the shipped components do not carry. Quoting an interval
without that term would imply a precision the layer cannot support. So
`tip_rate` carries no interval and an `interval_note` that says this and points
at `tipped_share` as the metric that answers the adjacent question properly.
`metrics.yml` refuses to let a proportion be defined without Wilson and refuses
to let any metric go without an interval unless it says why in writing.

## STL, and the gap problem

Daily trip volume is dominated by a weekly cycle. Anything this dashboard says
about a trend, a level shift or an anomaly has to be said about the series with
that cycle removed, or it is a statement about what day of the week it is.

`decompose` wraps `statsmodels`' STL and enforces two things the bare call does
not.

**The index.** STL works on position, not on time. Hand it a daily series with
three days missing and it will cheerfully decompose the rows it was given,
treating the row after the gap as the next day in the cycle. The seasonal phase
therefore slides by the width of every gap, and it stays slid for the rest of
the series. What comes out is a clean looking seasonal component that is simply
wrong, and nothing in the output says so: there is no warning, no diagnostic,
and the residual looks a little larger than it should in a way that nobody reads
as a bug.

So `decompose` infers the spacing from the most common gap in the index, not the
smallest, and reindexes onto a complete range. Using the smallest spacing would
turn one short interval in a year of daily data into a minute by minute grid of
almost entirely missing values. An index with timestamps that do not sit on the
inferred grid is rejected outright with a message saying to resample, because
that is a different problem and interpolation would hide it.

**What the interpolation actually does.** Missing points are filled by time
interpolation, and points that are present but null count as gaps too. This
preserves the phase, which is the whole objective: every real observation sits
back at its true position in the weekly cycle, and the seasonal estimate is
about the right days. `test_gap_filling_keeps_the_seasonal_phase` is the test
for precisely this, and its docstring names the failure it prevents.

It is not free, and the cost has a direction. An interpolated point lies on the
line between its neighbours, so it carries no residual of its own. That deflates
the residual variance slightly and therefore inflates
`strength_of_seasonality` and `strength_of_trend`, both of which are one minus a
ratio of residual variance to combined variance. It also cannot reconstruct
something it never saw: if the missing day was a blizzard or a holiday, the
interpolation smooths straight over the dip and the decomposition reports a
normal day. That is why `gaps_filled` is returned on the `Decomposition` rather
than being an internal detail, why `run_analysis` writes a note naming the
service and the count whenever it is non zero, and why the honest use of that
count is to decide whether to plot the decomposition at all.

**Robustness.** `robust=True` is the default here and not in `statsmodels`. A
single holiday, a blizzard or a data outage will otherwise bend the loess trend
around it for weeks either side. Downweighting those points instead keeps the
trend a statement about the trend.

The two strength measures are from Wang, Smith and Hyndman: the share of
variance each component removes from what is left after the other one is taken
out. They are the standard way to answer "is this series actually seasonal",
which is the question that decides whether a seasonally adjusted number should
be shown at all. Both are clipped to the unit interval, because the ratio can
exceed one when a component explains nothing and a negative strength is not a
quantity anyone has a use for.

## PELT, the pruning rule, and the noise scale

A changepoint here is a change in mean that persists: a surcharge landing, a
bridge closing, a neighbourhood's nightlife returning. It is not a trend and it
is not an outlier. Finding it means partitioning the series so that the total
within segment cost plus a penalty per segment is minimised.

Optimal partitioning solves that exactly in time quadratic in the series length.
PELT adds one observation and keeps the exactness.

**The pruning rule.** Let `F(t)` be the best cost up to `t` and `C(t, s)` the
cost of the segment from `t` to `s`. If

    F(t) + C(t, s) + K  >  F(s)

then `t` can never be the last changepoint before any point beyond `s`, and it
can be discarded from the candidate set permanently. `K` is a constant that
depends on the cost function; with the L2 cost it is zero, because splitting a
segment never increases its total cost. Under mild conditions the surviving
candidate set stays bounded, which is what turns the quadratic into something
close to linear.

The rule changes nothing about the answer. The segmentation is still the exact
minimiser, and `test_pruning_does_not_change_the_segmentation` proves that by
running an unpruned optimal partitioning over the same signals and penalties and
asserting the two agree, rather than by asserting that the pruned version
produces a plausible looking answer.

Two implementation notes that are load bearing rather than incidental. The L2
cost is evaluated in constant time from prefix sums; without that the cost
evaluation would carry the quadratic term the pruning just removed, and the
pruning would buy nothing. And a constant segment's cost is clamped at zero,
because prefix sums of a series far from the origin lose their low bits and can
return a small negative number, which would otherwise decide the pruning
comparisons.

**Why sigma comes from the MAD of first differences.** The BIC penalty is

    n_params · sigma² · log(n)

so the penalty is only as good as the noise scale it is built from. The obvious
estimator, the standard deviation of the signal, is the wrong one here, and it
fails in the direction that hurts most: it is inflated by the very level shifts
being searched for. A series with a large step in it has a large standard
deviation regardless of how quiet it is within each regime, the penalty built
from it comes out far too large, and the shifts go undetected. The detector then
reports a clean series, which is the most confident possible way to be wrong.

`estimate_sigma` differences the series first, which removes the level entirely
and leaves a series of noise plus a handful of entries that contain a shift. It
then takes the median absolute deviation of those differences, which ignores
that handful rather than being dragged by it. The result is rescaled by the
standard consistency constant that makes a MAD an estimator of the standard
deviation of a normal sample, and then divided by the square root of two to undo
the variance doubling that differencing introduced.
`test_estimate_sigma_recovers_the_noise_scale_despite_the_shifts` asserts the
claim directly, including that the plain standard deviation is useless on the
same input.

`bic_penalty` defaults to one free parameter, the bare Schwarz form, and
`analysis.py` passes two at the call site. Two is the usual choice for a change
in mean, because a changepoint introduces its own location as well as its new
level. That choice is at the call site rather than hidden in the default, so a
reader of the analysis code can see which penalty produced the annotations on
the chart. `CHANGEPOINT_MIN_SIZE` keeps a changepoint at least a fortnight from
its neighbours; below that the detector finds the edges of holidays, which are
already annotated from the date dimension.

## The search runs on the trend, not on the observed series

This is the change that made the changepoint panel worth showing, and the
reason for it is the same reason the STL section exists.

Run PELT on observed daily volume and it returns a segmentation of the weekly
cycle. The week has a larger amplitude than most of the level shifts anybody
cares about, so the cheapest way to reduce within segment cost is to cut the
series into runs of similar weekdays. The detector is not wrong; it is answering
the question it was asked, and the question was wrong. On this window that
produced dozens of changepoints, most of them a few days apart, which is not a
result a reader can do anything with.

So the signal is `result.trend`, the STL trend component, which is the series
with the weekly cycle already removed. "The level moved and stayed moved" is a
statement about the trend, and now it is computed on the trend.

That change moves the problem rather than removing it, and the second half of
the fix has to be stated as plainly as the first. A trend component is smooth by
construction, so its residual scale is small, so the BIC penalty derived from it
is small, so PELT will happily cut it at shifts of one percent. Two constants
handle that, and both are judgement calls rather than derivations:

- `CHANGEPOINT_PENALTY_SCALE` multiplies the BIC penalty, because the penalty
  that is right for a noisy series is far too permissive on a smoothed one.
- `CHANGEPOINT_MIN_EFFECT` is an effect floor: a candidate is annotated only
  when the level actually moved by at least
  {{ f0:changepoint_min_effect_pct }} percent, which is the smallest move a
  reader could plan around.

Detection and importance are different questions, and the floor is the place
that difference is made explicit. PELT answers "did the level change". The floor
answers "by enough that a reader should be told". Collapsing the two into a
penalty, which is the usual approach, hides a product decision inside a
statistical one and makes it impossible to report how much was discarded.

Which is the point of reporting it. On this build PELT found
{{ n:changepoints_found }} candidates, of which
{{ n:changepoints_below_effect_floor }} moved the level by less than the floor
and are not drawn, leaving {{ n:changepoints_annotated }} annotations on the
chart. Publishing all three counts is what lets a reader judge the floor
instead of trusting it: if the discarded share ever looks too large, the floor
is too high and the number is on the page that says so.

A detected changepoint is published with the mean of the four weeks before, the
mean of the four weeks after, the relative change and its direction. "A
changepoint was detected here" is not a finding. "Volume fell by this much and
stayed there" is one, and it is checkable.

**The one check with a known right answer.** Everything else in this document
is an argument. This is a measurement. The generator plants a single level shift
in for hire volume on {{ iso:planted_changepoint }}, a step of
{{ f1:planted_shift_pct.fhvhv }} percent. The detector placed a changepoint
{{ n:planted_changepoint_miss_days }} day away from that date, and measured the
step over the surrounding four weeks at
{{ f2:planted_changepoint_detected_change_pct }} percent.

Both halves of that result are worth reading. The location is essentially exact,
which is what an STL trend plus an L2 cost should give on a step of this size.
The magnitude is smaller than what was planted, and it should be: it is a
twenty eight day mean either side of a point on a smoothed trend that was
already drifting, so the window straddles the transition and the drift works
against the step. A detector that reported the planted figure back exactly would
be a detector that had been tuned until it did.

**And a caution about the rest of the annotations on generated data.** With the
exception of the planted shift, the annotated changepoints on this build cluster
within a few days of month boundaries. That is not a finding about anything. The
generator applies its monthly seasonal factor as a step at midnight on the first
of each month, so the trend genuinely does jump there, and a changepoint
detector is precisely the instrument that will notice. It is documented as a
known artefact in [data.md](data.md), it accounts for a large share of the
candidates that fall below the effect floor, and it is the clearest illustration
in this repository of why a generated fixture is good for testing a method and
useless for drawing a conclusion.

## Benjamini-Hochberg, and what the correction actually did

The zone comparison panel asks which zones tip differently from the city. That
is a multiple testing problem before it is anything else.

**The comparison count.** Comparing every pair of zones is the number of pairs
in the upper triangle, which for the full published zone set is tens of
thousands of tests; `test_pairwise_over_the_full_zone_set_is_the_number_the_interface_prints`
pins that count. At an uncorrected five percent threshold, one test in twenty
comes back significant on pure noise, which over a family that size is far more
marked pairs than any real effect would produce. The panel therefore compares
each zone against a
reference rate rather than against every other zone, which reduces the family to
one test per zone per service, and `analysis.py` also computes the all pairs
count and states it on screen so a reader knows how many comparisons the
correction is protecting against rather than assuming it is the number of zones.

Bonferroni would control the family wise error rate and cost almost all of the
power, because the tests are far from independent and the family is large.
Benjamini-Hochberg controls the expected share of false positives among the
comparisons the panel marks, which is the quantity a reader of that panel
actually cares about: of the differences shown here, about this fraction are
noise. It is valid under independence and under positive regression dependence,
which is the right assumption for comparisons drawn from a common pool of zones.

`benjamini_hochberg` returns standard adjusted p values: the running minimum of
`m/i · p(i)` taken from the largest p downwards, clipped at one. The running
minimum is what makes them monotone in the raw p value. That matters for a
reason that is entirely about the interface: the panel is a sortable table, and
a non monotone adjusted column lets a reader sort by raw p and see a smaller p
value marked not significant sitting directly above a larger one that is marked.

**NaN is not a p value of one.** A pair or a cell where one side had no trips in
the selected window is a test that was never run. Counting it as a non rejection
inflates `m`, which makes every real test harder to detect. Those entries come
back NaN, are not rejected, and are excluded from `n_comparisons`, so the number
the interface prints is the number of tests that happened.

**Why the comparison is made within a service.** Each zone is compared against
its own service's citywide tipped share, not against a single pooled rate across
all services. Pooling would confound the question completely. Yellow card trips
tip on most rides; app trips mostly do not. A zone's pooled tipped share is
therefore largely a measure of its service mix, and every Manhattan zone would
come back significantly different for a reason that has nothing whatever to do
with tipping. Comparing like with like is what makes a surviving result mean
something.

Every cell across every service is then corrected **together**, in one call,
because the multiplicity a reader faces is the number of comparisons on the
screen and not the number inside one facet. Correcting each service separately
and showing them side by side would control nothing.

Two further guards. A zone needs a minimum number of observable trips before it
is compared at all, applied as a `having` clause in the query. And the p value
itself uses a normal approximation to the two sided test against a fixed rate,
which is acceptable here and only here: this p value decides membership in a
corrected set rather than being displayed, and every zone that reaches it has
cleared that minimum. The number a reader actually sees is the Wilson interval,
which is the one that has to be right at small counts.

**What the correction actually did on this build.** {{ n:zone_comparisons }}
comparisons were run, of which {{ n:zone_comparisons_naive_significant }} cross
an uncorrected five percent threshold. On a family of that size, if no zone
differed at all, {{ f0:zone_comparisons_expected_by_chance }} would be expected
to cross it from noise alone. Surviving Benjamini-Hochberg:
{{ n:zone_comparisons_after_bh }}.

That is the correct answer, and it is worth sitting with rather than explaining
away. This build ran on the `{{ raw:backend }}` backend, where a trip's tip is
drawn from a distribution that depends on the payment channel and the fare and
on nothing else: it has no zone term at all. There is no zone level tipping
effect in this data, so there is nothing for the procedure to find, and it finds
nothing.

Notice that the uncorrected count comes in below the count expected by chance
rather than on top of it. That is what a family of true nulls looks like, with
one wrinkle worth naming: each zone is tested against a reference rate estimated
from the same data, including that zone's own trips, which makes the test
slightly conservative for the zones large enough to move their own reference.
The effect is small and it runs in the safe direction, but a reader comparing
the two counts should know it is there rather than reading the gap as evidence
of anything.

The panel showing an empty surviving set is the statistics working. A panel that
instead highlighted every uncorrected result would be handing a reader
{{ n:zone_comparisons_naive_significant }} coin flips and calling them
neighbourhoods.

## The palette validator

Colour is part of the statistics here, because a chart that encodes a value in a
hue two readers cannot tell apart has not communicated the value. `make palette`
runs `scripts/validate_palette.js` over eight scale and mode combinations, and
it fails the build. This is its real output on this build.

```
palette: 8 colours, mode dark, surface #0B0F14, pairs adjacent
  pass  contrast vs surface                    #0891B2               5.22 (floor 3) L 0.609 C 0.111
  pass  contrast vs surface                    #D97706               6.03 (floor 3) L 0.666 C 0.157
  pass  contrast vs surface                    #8B5CF6               4.54 (floor 3) L 0.606 C 0.219
  pass  contrast vs surface                    #059669                5.1 (floor 3) L 0.596 C 0.127
  pass  contrast vs surface                    #EF4444               5.11 (floor 3) L 0.637 C 0.208
  pass  contrast vs surface                    #2563EB               3.72 (floor 3) L 0.546 C 0.215
  pass  contrast vs surface                    #EC4899               5.45 (floor 3) L 0.656 C 0.212
  pass  contrast vs surface                    #65A30D               6.22 (floor 3) L 0.648 C 0.175
  pass  lightness or chroma separation         #0891B2 to #D97706   0.057 (floor 0.045) chroma delta 0.046
  pass  normal vision delta E                  #0891B2 to #D97706    27.2 (floor 12) 
  pass  worst colour vision deficiency delta E #0891B2 to #D97706    19.2 (floor 8) protanopia
  pass  lightness or chroma separation         #D97706 to #8B5CF6    0.06 (floor 0.045) chroma delta 0.061
  pass  normal vision delta E                  #D97706 to #8B5CF6    34.1 (floor 12) 
  pass  worst colour vision deficiency delta E #D97706 to #8B5CF6    26.5 (floor 8) tritanopia
  pass  lightness or chroma separation         #8B5CF6 to #059669    0.01 (floor 0.045) chroma delta 0.091
  pass  normal vision delta E                  #8B5CF6 to #059669    31.6 (floor 12) 
  pass  worst colour vision deficiency delta E #8B5CF6 to #059669    18.5 (floor 8) tritanopia
  pass  lightness or chroma separation         #059669 to #EF4444   0.041 (floor 0.045) chroma delta 0.080
  pass  normal vision delta E                  #059669 to #EF4444    31.7 (floor 12) 
  pass  worst colour vision deficiency delta E #059669 to #EF4444     9.8 (floor 8) deuteranopia
  pass  lightness or chroma separation         #EF4444 to #2563EB   0.091 (floor 0.045) chroma delta 0.007
  pass  normal vision delta E                  #EF4444 to #2563EB    38.2 (floor 12) 
  pass  worst colour vision deficiency delta E #EF4444 to #2563EB    26.4 (floor 8) protanopia
  pass  lightness or chroma separation         #2563EB to #EC4899    0.11 (floor 0.045) chroma delta 0.003
  pass  normal vision delta E                  #2563EB to #EC4899    32.5 (floor 12) 
  pass  worst colour vision deficiency delta E #2563EB to #EC4899    13.8 (floor 8) protanopia
  pass  lightness or chroma separation         #EC4899 to #65A30D   0.008 (floor 0.045) chroma delta 0.036
  pass  normal vision delta E                  #EC4899 to #65A30D    36.1 (floor 12) 
  pass  worst colour vision deficiency delta E #EC4899 to #65A30D    13.7 (floor 8) deuteranopia

All 29 checks pass.
palette: 8 colours, mode light, surface #FAFAFA, pairs adjacent
  pass  contrast vs surface                    #0891B2               3.53 (floor 3) L 0.609 C 0.111
  pass  contrast vs surface                    #B45309               4.81 (floor 3) L 0.555 C 0.146
  pass  contrast vs surface                    #7C3AED               5.46 (floor 3) L 0.541 C 0.247
  pass  contrast vs surface                    #047857               5.25 (floor 3) L 0.508 C 0.105
  pass  contrast vs surface                    #DC2626               4.63 (floor 3) L 0.577 C 0.215
  pass  contrast vs surface                    #1D4ED8               6.42 (floor 3) L 0.488 C 0.217
  pass  contrast vs surface                    #DB2777                4.4 (floor 3) L 0.592 C 0.218
  pass  contrast vs surface                    #4D7C0F               4.78 (floor 3) L 0.532 C 0.141
  pass  lightness or chroma separation         #0891B2 to #B45309   0.054 (floor 0.045) chroma delta 0.035
  pass  normal vision delta E                  #0891B2 to #B45309    26.1 (floor 12) 
  pass  worst colour vision deficiency delta E #0891B2 to #B45309    21.1 (floor 8) deuteranopia
  pass  lightness or chroma separation         #B45309 to #7C3AED   0.014 (floor 0.045) chroma delta 0.101
  pass  normal vision delta E                  #B45309 to #7C3AED    33.7 (floor 12) 
  pass  worst colour vision deficiency delta E #B45309 to #7C3AED    26.9 (floor 8) tritanopia
  pass  lightness or chroma separation         #7C3AED to #047857   0.033 (floor 0.045) chroma delta 0.142
  pass  normal vision delta E                  #7C3AED to #047857    32.3 (floor 12) 
  pass  worst colour vision deficiency delta E #7C3AED to #047857    19.3 (floor 8) tritanopia
  pass  lightness or chroma separation         #047857 to #DC2626   0.069 (floor 0.045) chroma delta 0.110
  pass  normal vision delta E                  #047857 to #DC2626    30.9 (floor 12) 
  pass  worst colour vision deficiency delta E #047857 to #DC2626     9.8 (floor 8) protanopia
  pass  lightness or chroma separation         #DC2626 to #1D4ED8   0.089 (floor 0.045) chroma delta 0.002
  pass  normal vision delta E                  #DC2626 to #1D4ED8      39 (floor 12) 
  pass  worst colour vision deficiency delta E #DC2626 to #1D4ED8    28.1 (floor 8) protanopia
  pass  lightness or chroma separation         #1D4ED8 to #DB2777   0.103 (floor 0.045) chroma delta 0.001
  pass  normal vision delta E                  #1D4ED8 to #DB2777      34 (floor 12) 
  pass  worst colour vision deficiency delta E #1D4ED8 to #DB2777    16.3 (floor 8) protanopia
  pass  lightness or chroma separation         #DB2777 to #4D7C0F   0.059 (floor 0.045) chroma delta 0.077
  pass  normal vision delta E                  #DB2777 to #4D7C0F    33.3 (floor 12) 
  pass  worst colour vision deficiency delta E #DB2777 to #4D7C0F    10.9 (floor 8) deuteranopia

All 29 checks pass.
palette: 3 colours, mode dark, surface #0B0F14, pairs all
  pass  contrast vs surface                    #0891B2               5.22 (floor 3) L 0.609 C 0.111
  pass  contrast vs surface                    #D97706               6.03 (floor 3) L 0.666 C 0.157
  pass  contrast vs surface                    #8B5CF6               4.54 (floor 3) L 0.606 C 0.219
  pass  lightness or chroma separation         #0891B2 to #D97706   0.057 (floor 0.045) chroma delta 0.046
  pass  normal vision delta E                  #0891B2 to #D97706    27.2 (floor 12) 
  pass  worst colour vision deficiency delta E #0891B2 to #D97706    19.2 (floor 8) protanopia
  pass  lightness or chroma separation         #0891B2 to #8B5CF6   0.003 (floor 0.045) chroma delta 0.108
  pass  normal vision delta E                  #0891B2 to #8B5CF6    21.1 (floor 12) 
  pass  worst colour vision deficiency delta E #0891B2 to #8B5CF6    11.7 (floor 8) deuteranopia
  pass  lightness or chroma separation         #D97706 to #8B5CF6    0.06 (floor 0.045) chroma delta 0.061
  pass  normal vision delta E                  #D97706 to #8B5CF6    34.1 (floor 12) 
  pass  worst colour vision deficiency delta E #D97706 to #8B5CF6    26.5 (floor 8) tritanopia

All 12 checks pass.
palette: 3 colours, mode light, surface #FAFAFA, pairs all
  pass  contrast vs surface                    #0891B2               3.53 (floor 3) L 0.609 C 0.111
  pass  contrast vs surface                    #B45309               4.81 (floor 3) L 0.555 C 0.146
  pass  contrast vs surface                    #7C3AED               5.46 (floor 3) L 0.541 C 0.247
  pass  lightness or chroma separation         #0891B2 to #B45309   0.054 (floor 0.045) chroma delta 0.035
  pass  normal vision delta E                  #0891B2 to #B45309    26.1 (floor 12) 
  pass  worst colour vision deficiency delta E #0891B2 to #B45309    21.1 (floor 8) deuteranopia
  pass  lightness or chroma separation         #0891B2 to #7C3AED   0.068 (floor 0.045) chroma delta 0.136
  pass  normal vision delta E                  #0891B2 to #7C3AED    24.5 (floor 12) 
  pass  worst colour vision deficiency delta E #0891B2 to #7C3AED    14.4 (floor 8) tritanopia
  pass  lightness or chroma separation         #B45309 to #7C3AED   0.014 (floor 0.045) chroma delta 0.101
  pass  normal vision delta E                  #B45309 to #7C3AED    33.7 (floor 12) 
  pass  worst colour vision deficiency delta E #B45309 to #7C3AED    26.9 (floor 8) tritanopia

All 12 checks pass.
palette: 6 colours, mode dark, surface #0B0F14, pairs adjacent
  pass  contrast vs surface (fill)             #155E75               2.64 (floor 1.5) L 0.450 C 0.077
  pass  contrast vs surface (fill)             #0E7490               3.59 (floor 1.5) L 0.520 C 0.094
  pass  contrast vs surface (fill)             #0891B2               5.22 (floor 1.5) L 0.609 C 0.111
  pass  contrast vs surface (fill)             #06B6D4               7.92 (floor 1.5) L 0.715 C 0.126
  pass  contrast vs surface (fill)             #22D3EE              10.63 (floor 1.5) L 0.797 C 0.134
  pass  contrast vs surface (fill)             #67E8F9              13.26 (floor 1.5) L 0.865 C 0.115
  pass  ordinal lightness step                 #155E75 to #0E7490    0.07 (floor 0.06) ascending
  pass  ordinal lightness step                 #0E7490 to #0891B2   0.089 (floor 0.06) ascending
  pass  ordinal lightness step                 #0891B2 to #06B6D4   0.106 (floor 0.06) ascending
  pass  ordinal lightness step                 #06B6D4 to #22D3EE   0.082 (floor 0.06) ascending
  pass  ordinal lightness step                 #22D3EE to #67E8F9   0.068 (floor 0.06) ascending

All 11 checks pass.
palette: 6 colours, mode light, surface #FAFAFA, pairs adjacent
  pass  contrast vs surface (fill)             #083344              12.84 (floor 1.5) L 0.302 C 0.054
  pass  contrast vs surface (fill)             #155E75               6.96 (floor 1.5) L 0.450 C 0.077
  pass  contrast vs surface (fill)             #0E7490               5.13 (floor 1.5) L 0.520 C 0.094
  pass  contrast vs surface (fill)             #0891B2               3.53 (floor 1.5) L 0.609 C 0.111
  pass  contrast vs surface (fill)             #06B6D4               2.33 (floor 1.5) L 0.715 C 0.126
  pass  contrast vs surface (fill)             #22D3EE               1.73 (floor 1.5) L 0.797 C 0.134
  pass  ordinal lightness step                 #083344 to #155E75   0.148 (floor 0.06) ascending
  pass  ordinal lightness step                 #155E75 to #0E7490    0.07 (floor 0.06) ascending
  pass  ordinal lightness step                 #0E7490 to #0891B2   0.089 (floor 0.06) ascending
  pass  ordinal lightness step                 #0891B2 to #06B6D4   0.106 (floor 0.06) ascending
  pass  ordinal lightness step                 #06B6D4 to #22D3EE   0.082 (floor 0.06) ascending

All 11 checks pass.
palette: 7 colours, mode dark, surface #0B0F14, pairs adjacent
  pass  diverging midpoint is neutral          #383835             0.0051 (floor 0.02) chroma, lower is more neutral
  pass  contrast vs surface (fill)             #67E8F9              13.26 (floor 1.5) L 0.865 C 0.115
  pass  contrast vs surface (fill)             #22D3EE              10.63 (floor 1.5) L 0.797 C 0.134
  pass  contrast vs surface (fill)             #0891B2               5.22 (floor 1.5) L 0.609 C 0.111
  pass  contrast vs surface (fill)             #D97706               6.03 (floor 1.5) L 0.666 C 0.157
  pass  contrast vs surface (fill)             #B45309               3.83 (floor 1.5) L 0.555 C 0.146
  pass  contrast vs surface (fill)             #92400E               2.71 (floor 1.5) L 0.473 C 0.125
  pass  cool arm lightness step                #383835 to #0891B2   0.269 (floor 0.06) measured from the midpoint outwards
  pass  cool arm lightness step                #0891B2 to #22D3EE   0.188 (floor 0.06) measured from the midpoint outwards
  pass  cool arm lightness step                #22D3EE to #67E8F9   0.068 (floor 0.06) measured from the midpoint outwards
  pass  warm arm lightness step                #383835 to #D97706   0.326 (floor 0.06) measured from the midpoint outwards
  pass  warm arm lightness step                #D97706 to #B45309   0.111 (floor 0.06) measured from the midpoint outwards
  pass  warm arm lightness step                #B45309 to #92400E   0.082 (floor 0.06) measured from the midpoint outwards

All 13 checks pass.
palette: 7 colours, mode light, surface #FAFAFA, pairs adjacent
  pass  diverging midpoint is neutral          #F0EFEC             0.0041 (floor 0.02) chroma, lower is more neutral
  pass  contrast vs surface (fill)             #155E75               6.96 (floor 1.5) L 0.450 C 0.077
  pass  contrast vs surface (fill)             #0E7490               5.13 (floor 1.5) L 0.520 C 0.094
  pass  contrast vs surface (fill)             #0891B2               3.53 (floor 1.5) L 0.609 C 0.111
  pass  contrast vs surface (fill)             #D97706               3.05 (floor 1.5) L 0.666 C 0.157
  pass  contrast vs surface (fill)             #B45309               4.81 (floor 1.5) L 0.555 C 0.146
  pass  contrast vs surface (fill)             #92400E               6.79 (floor 1.5) L 0.473 C 0.125
  pass  cool arm lightness step                #F0EFEC to #0891B2   0.343 (floor 0.06) measured from the midpoint outwards
  pass  cool arm lightness step                #0891B2 to #0E7490   0.089 (floor 0.06) measured from the midpoint outwards
  pass  cool arm lightness step                #0E7490 to #155E75    0.07 (floor 0.06) measured from the midpoint outwards
  pass  warm arm lightness step                #F0EFEC to #D97706   0.286 (floor 0.06) measured from the midpoint outwards
  pass  warm arm lightness step                #D97706 to #B45309   0.111 (floor 0.06) measured from the midpoint outwards
  pass  warm arm lightness step                #B45309 to #92400E   0.082 (floor 0.06) measured from the midpoint outwards

All 13 checks pass.
```

### The six checks

**1. Lightness separation.** Two series that differ only in hue are two series a
reader has to hold in working memory and match against a legend. A step in
lightness makes them distinguishable in a thumbnail, in greyscale, in a
photocopy and in peripheral vision. Reported as `lightness or chroma
separation`, because either one passing is enough to tell two slots apart.

**2. Chroma separation.** The other half of the same check. Two hues at the same
lightness and the same chroma read as the same colour under a projector, which
is where a surprising amount of this work is actually looked at.

**3. Contrast against the surface.** A two pixel line below a three to one
contrast ratio against its background disappears. Reported per colour against
the surface for that mode, so the dark and light themes are validated separately
rather than one being assumed from the other. Sequential and diverging scales
are held to the lower fill floor instead, and the reason is in the source: a
ramp is filled area rather than a thin line, and its darkest step sits
deliberately close to a dark surface so that the scale has somewhere to start.
Holding a fill to the line floor would fail a ramp for doing exactly the job it
was designed for.

**4. Normal vision separation, as OKLab delta E.** A perceptual distance rather
than an RGB one, because RGB distance does not correspond to how different two
colours look. The floor is deliberately low: this check is about adjacent slots
in the palette, which is where confusion actually happens, not about every pair
being maximally far apart.

**5. Colour vision deficiency separation.** The same delta E, computed after
simulating protanopia, deuteranopia and tritanopia, taking the worst of the
three. Roughly one man in twelve has one of these, and a red to green pair is
the classic failure that passes every other check on this list. The output names
which deficiency produced the worst case, so a failure tells you what to fix.

**6. Ordinal monotonicity.** For a sequential ramp, lightness has to move the
same direction at every step, or the ramp does not encode magnitude and a reader
cannot order two swatches without the legend. For a diverging scale this runs as
two arms measured outward from the midpoint, and it is accompanied by a check
that the midpoint is actually neutral: a diverging scale whose centre carries
chroma puts a colour at the value that is supposed to mean "no difference".
