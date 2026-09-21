"""The cleaning rules, each one named, counted and published.

Two decisions run through this module.

**Quarantine, never delete.** A rejected row is written to a log with the rule
that caught it. The counts go on the dashboard. A pipeline that silently drops
four percent of its input and reports a clean build is worse than one that
reports the four percent, because the four percent is usually where the
interesting problem is.

**Unknown zones are not a quarantine rule.** Zone ids 264 and 265 mean the TLC
could not determine the location. Those trips are real, they carry real fare,
and throwing them away biases every total downwards. They are kept, flagged,
and excluded from geographic aggregates only, by an explicit filter that a test
enforces. Deleting them would be the easy version of this and it would be wrong.

Rules are evaluated in the order listed and the first match wins, so a row
carries exactly one label and the per rule counts sum to the quarantine total
without double counting. The order is worst-first: a row whose timestamps are
nonsense gets labelled for that rather than for the implausible speed the
nonsense timestamps imply.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from cityflow_core.config import CityflowConfig, SourceSpec


@dataclass(frozen=True, slots=True)
class Rule:
    """One quarantine rule: a name, a reason, and the predicate that catches it."""

    name: str
    reason: str
    predicate: str

    def __str__(self) -> str:
        return self.name


def rules_for(config: CityflowConfig, source: SourceSpec) -> tuple[Rule, ...]:
    """The rule list, with this build's thresholds substituted in.

    The thresholds come from config rather than being written here, so
    docs/data.md can print the same numbers the pipeline used instead of a
    copy of them that drifts.
    """
    q = config.quarantine
    period_start = source.period
    period_end = _month_end(source.period)
    low = period_start - dt.timedelta(days=q.period_tolerance_days)
    high = period_end + dt.timedelta(days=q.period_tolerance_days + 1)

    return (
        Rule(
            name="null_timestamp",
            reason="Pickup or dropoff timestamp is null, so the trip has no duration.",
            predicate="pickup_ts is null or dropoff_ts is null",
        ),
        Rule(
            name="timestamp_out_of_period",
            reason=(
                f"Pickup falls outside {source.period_key} plus or minus "
                f"{q.period_tolerance_days} days. The published files contain trips "
                "dated years outside their own month, including 2001 and 2098."
            ),
            predicate=(
                f"pickup_ts < timestamp '{low:%Y-%m-%d} 00:00:00' "
                f"or pickup_ts >= timestamp '{high:%Y-%m-%d} 00:00:00'"
            ),
        ),
        Rule(
            name="dropoff_before_pickup",
            reason="Dropoff is at or before pickup, giving a zero or negative duration.",
            predicate="dropoff_ts <= pickup_ts",
        ),
        Rule(
            name="duration_too_short",
            reason=(
                f"Shorter than {q.min_trip_seconds} seconds, which is a meter toggled "
                "on and off rather than a trip."
            ),
            predicate=(f"date_diff('second', pickup_ts, dropoff_ts) < {q.min_trip_seconds}"),
        ),
        Rule(
            name="duration_too_long",
            reason=(f"Longer than {q.max_trip_hours} hours, which is a meter left running."),
            predicate=(
                f"date_diff('second', pickup_ts, dropoff_ts) > {int(q.max_trip_hours * 3600)}"
            ),
        ),
        Rule(
            name="non_positive_distance",
            reason="Distance is null, zero or negative.",
            predicate="trip_distance is null or trip_distance <= 0",
        ),
        Rule(
            name="distance_too_long",
            reason=(
                f"Further than {q.max_trip_miles} miles, beyond any plausible "
                "destination for a metered trip originating in the service area."
            ),
            predicate=f"trip_distance > {q.max_trip_miles}",
        ),
        Rule(
            name="non_positive_fare",
            reason="Fare is null, zero or negative.",
            predicate="fare_amount is null or fare_amount <= 0",
        ),
        Rule(
            name="fare_too_large",
            reason=f"Fare above {q.max_fare_amount:.0f} dollars is a data entry error.",
            predicate=f"fare_amount > {q.max_fare_amount}",
        ),
        Rule(
            name="implausible_speed",
            reason=(
                f"Implied speed above {q.max_implied_speed_mph:.0f} mph. Above this is "
                "a record error, not a fast driver: it exceeds any sustained speed "
                "achievable on the streets, bridges and parkways in the service area."
            ),
            predicate=(
                "trip_distance / nullif(date_diff('second', pickup_ts, dropoff_ts), 0) "
                f"* 3600.0 > {q.max_implied_speed_mph}"
            ),
        ),
        Rule(
            name="passenger_count_invalid",
            reason=(
                "Passenger count is zero, negative, or larger than "
                f"{q.max_passenger_count}, the largest licensed vehicle in the fleet. "
                "Null is left alone: the for hire files do not record it at all, which "
                "is not the same as recording nonsense."
            ),
            predicate=(
                "passenger_count is not null and "
                f"(passenger_count <= 0 or passenger_count > {q.max_passenger_count})"
            ),
        ),
        Rule(
            name="negative_component",
            reason=(
                "A tip, toll or surcharge is negative. These appear in the published "
                "files as reversal rows that were never matched to their original."
            ),
            predicate=(
                "tip_amount < 0 or tolls_amount < 0 "
                "or coalesce(congestion_surcharge, 0) < 0 "
                "or coalesce(airport_fee, 0) < 0"
            ),
        ),
    )


# Duplicates are found by a window rather than a predicate, so they are not in
# the rule list above and get their own name here. The business key deliberately
# excludes trip_id, which is assigned by us and is unique by construction.
DUPLICATE_RULE = Rule(
    name="exact_duplicate",
    reason=(
        "A byte for byte repeat of an earlier row in the same file. The published "
        "files contain them, usually from a re-submitted batch. The first "
        "occurrence is kept."
    ),
    predicate="",
)

BUSINESS_KEY: tuple[str, ...] = (
    "service",
    "operator",
    "pickup_ts",
    "dropoff_ts",
    "pu_zone_id",
    "do_zone_id",
    "passenger_count",
    "trip_distance",
    "payment_type",
    "fare_amount",
    "tip_amount",
    "tolls_amount",
    "total_amount",
)


def quarantine_case(rules: tuple[Rule, ...]) -> str:
    """A CASE expression returning the first matching rule name, or NULL."""
    branches = "\n        ".join(f"when {rule.predicate} then '{rule.name}'" for rule in rules)
    return f"""case
        {branches}
        else null
    end"""


def duplicate_flag_sql() -> str:
    """Marks every occurrence after the first within one source file."""
    partition = ", ".join(BUSINESS_KEY)
    return f"row_number() over (partition by {partition} order by trip_id) > 1"


def _month_end(period: dt.date) -> dt.date:
    """Last day of the month that period falls in."""
    if period.month == 12:
        return dt.date(period.year, 12, 31)
    return dt.date(period.year, period.month + 1, 1) - dt.timedelta(days=1)


def all_rule_names(config: CityflowConfig, source: SourceSpec) -> tuple[str, ...]:
    """Every label a row can carry, duplicates included, in evaluation order."""
    return (*(rule.name for rule in rules_for(config, source)), DUPLICATE_RULE.name)
