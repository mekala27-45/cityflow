"""Ingest one month at a time, and keep the receipts.

The shape of this module is set by one requirement: every row that does not
reach the warehouse has to be accounted for by name. So the pipeline does not
filter, it labels. A single pass writes the rule that caught each row, and then
two inserts split the labelled set into the clean table and the quarantine log.
The arithmetic that has to hold, and that a test asserts after every run, is

    source rows = clean rows + sum(quarantine counts by rule)

with no rule overlapping another, because the rules are evaluated first match
wins. If that identity fails, the build fails. A pipeline that cannot say where
four percent of its input went does not get to describe itself as clean.

Memory, and why the work is per month: a year of for hire records is two
hundred million rows. DuckDB will stream that happily, but the raw parquet for
it will not fit next to the warehouse on a small disk. Each month is resolved,
ingested and then released, so peak disk is one month of source plus the
warehouse rather than a year of both.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from cityflow_core.config import CityflowConfig, SourceSpec
from cityflow_core.paths import Paths
from cityflow_ingest.normalize import (
    CANONICAL_COLUMNS,
    normalize_select,
    trip_id_offset,
)
from cityflow_ingest.quarantine import (
    DUPLICATE_RULE,
    duplicate_flag_sql,
    quarantine_case,
    rules_for,
)
from cityflow_ingest.registry import ResolvedSource, SourceRegistry

# How many rejected rows to keep per rule per month for the data health panel.
# Enough to show a reader what a caught row looks like, small enough that the
# sample table stays under a megabyte over a full year.
QUARANTINE_SAMPLE_PER_RULE = 25

STAGING_TABLE = "stg_trip"
QUARANTINE_LOG = "quarantine_log"
QUARANTINE_SAMPLE = "quarantine_sample"
SOURCE_AUDIT = "source_audit"


@dataclass(slots=True)
class MonthReport:
    """What one month cost and what it contained."""

    spec: SourceSpec
    vintage: str
    backend: str
    source_rows: int
    clean_rows: int
    quarantined: dict[str, int] = field(default_factory=dict)
    source_bytes: int = 0
    seconds: float = 0.0
    unexpected_columns: tuple[str, ...] = ()

    @property
    def quarantined_total(self) -> int:
        return sum(self.quarantined.values())

    def check_balance(self) -> None:
        total = self.clean_rows + self.quarantined_total
        if total != self.source_rows:
            raise RuntimeError(
                f"{self.spec}: {self.source_rows} source rows do not balance. "
                f"{self.clean_rows} clean plus {self.quarantined_total} "
                f"quarantined is {total}. A row went missing without a rule "
                "claiming it, which means the rule set and the filter disagree."
            )


@dataclass(slots=True)
class IngestReport:
    """The whole run."""

    months: list[MonthReport] = field(default_factory=list)
    started: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))

    @property
    def source_rows(self) -> int:
        return sum(m.source_rows for m in self.months)

    @property
    def clean_rows(self) -> int:
        return sum(m.clean_rows for m in self.months)

    @property
    def quarantined_total(self) -> int:
        return sum(m.quarantined_total for m in self.months)

    def by_rule(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for month in self.months:
            for rule, count in month.quarantined.items():
                out[rule] = out.get(rule, 0) + count
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    @property
    def quarantine_share(self) -> float:
        return self.quarantined_total / self.source_rows if self.source_rows else 0.0


def create_tables(connection: duckdb.DuckDBPyConnection) -> None:
    """The four tables the ingest owns. dbt owns everything downstream."""
    connection.execute(
        f"""
        create table if not exists {STAGING_TABLE} (
            trip_id bigint,
            service varchar,
            vintage varchar,
            source_period date,
            operator varchar,
            pickup_ts timestamp,
            dropoff_ts timestamp,
            pu_zone_id smallint,
            do_zone_id smallint,
            passenger_count smallint,
            trip_distance double,
            ratecode_id smallint,
            payment_type varchar,
            tip_is_observable boolean,
            fare_amount double,
            extra double,
            mta_tax double,
            improvement_surcharge double,
            congestion_surcharge double,
            airport_fee double,
            cbd_congestion_fee double,
            tip_amount double,
            tolls_amount double,
            total_amount double
        )
        """
    )
    connection.execute(
        f"""
        create table if not exists {QUARANTINE_LOG} (
            service varchar,
            source_period date,
            vintage varchar,
            rule varchar,
            rows bigint,
            source_rows bigint
        )
        """
    )
    connection.execute(
        f"""
        create table if not exists {QUARANTINE_SAMPLE} (
            service varchar,
            source_period date,
            rule varchar,
            pickup_ts timestamp,
            dropoff_ts timestamp,
            pu_zone_id smallint,
            do_zone_id smallint,
            trip_distance double,
            fare_amount double,
            passenger_count smallint,
            implied_speed_mph double
        )
        """
    )
    connection.execute(
        f"""
        create table if not exists {SOURCE_AUDIT} (
            service varchar,
            source_period date,
            vintage varchar,
            backend varchar,
            source_rows bigint,
            clean_rows bigint,
            quarantined_rows bigint,
            source_bytes bigint,
            seconds double,
            ingested_at timestamp
        )
        """
    )


def ingest_month(
    connection: duckdb.DuckDBPyConnection,
    config: CityflowConfig,
    resolved: ResolvedSource,
    *,
    unexpected_columns: tuple[str, ...] = (),
) -> MonthReport:
    """Label, split and land one month. Returns the accounting."""
    started = time.perf_counter()
    spec = resolved.spec
    rules = rules_for(config, spec)
    columns = ", ".join(CANONICAL_COLUMNS)

    # One pass: normalize, then label. The duplicate check runs after the rule
    # case so a row that is both a duplicate and out of period is counted once,
    # under the rule that is more informative about the source.
    connection.execute("drop table if exists labelled_month")
    connection.execute(
        f"""
        create temporary table labelled_month as
        with normalized as (
            {normalize_select(spec, resolved.vintage, resolved.path,
                              trip_id_offset=trip_id_offset(spec))}
        ),
        ruled as (
            select *, {quarantine_case(rules)} as rule_hit
            from normalized
        )
        select
            * exclude (rule_hit),
            coalesce(
                rule_hit,
                case when {duplicate_flag_sql()} then '{DUPLICATE_RULE.name}' end
            ) as quarantine_rule
        from ruled
        """
    )

    source_rows = _scalar(connection, "select count(*) from labelled_month")

    connection.execute(
        f"insert into {STAGING_TABLE} ({columns}) "
        f"select {columns} from labelled_month where quarantine_rule is null"
    )
    clean_rows = _scalar(
        connection, "select count(*) from labelled_month where quarantine_rule is null"
    )

    connection.execute(
        f"""
        insert into {QUARANTINE_LOG}
        select
            '{spec.service}', date '{spec.period:%Y-%m-%d}', '{resolved.vintage.name}',
            quarantine_rule, count(*), {source_rows}
        from labelled_month
        where quarantine_rule is not null
        group by quarantine_rule
        """
    )

    connection.execute(
        f"""
        insert into {QUARANTINE_SAMPLE}
        select service, source_period, quarantine_rule, pickup_ts, dropoff_ts,
               pu_zone_id, do_zone_id, trip_distance, fare_amount, passenger_count,
               trip_distance
                   / nullif(date_diff('second', pickup_ts, dropoff_ts), 0) * 3600.0
        from (
            select *, row_number() over (
                partition by quarantine_rule order by trip_id
            ) as rn
            from labelled_month
            where quarantine_rule is not null
        )
        where rn <= {QUARANTINE_SAMPLE_PER_RULE}
        """
    )

    rows = connection.execute(
        "select quarantine_rule, count(*) from labelled_month "
        "where quarantine_rule is not null group by 1"
    ).fetchall()
    connection.execute("drop table if exists labelled_month")

    report = MonthReport(
        spec=spec,
        vintage=resolved.vintage.name,
        backend=resolved.backend,
        source_rows=source_rows,
        clean_rows=clean_rows,
        quarantined={str(rule): int(count) for rule, count in rows},
        source_bytes=resolved.bytes_on_disk,
        seconds=time.perf_counter() - started,
        unexpected_columns=unexpected_columns,
    )
    report.check_balance()

    connection.execute(
        f"""
        insert into {SOURCE_AUDIT} values
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            spec.service,
            spec.period,
            resolved.vintage.name,
            resolved.backend,
            report.source_rows,
            report.clean_rows,
            report.quarantined_total,
            report.source_bytes,
            report.seconds,
            dt.datetime.now(dt.UTC).replace(tzinfo=None),
        ],
    )
    return report


def run_ingest(
    connection: duckdb.DuckDBPyConnection,
    config: CityflowConfig,
    *,
    paths: Paths | None = None,
    scale: float = 1.0,
    sources: list[SourceSpec] | None = None,
    release_source: bool = True,
    on_progress: object = None,
) -> IngestReport:
    """Resolve, ingest and release every month in the window.

    release_source deletes each source file once its rows are in the warehouse.
    On by default because a year of for hire parquet and the warehouse it
    produces do not fit together on a small disk, and the file can always be
    fetched or regenerated. Turn it off when iterating on the normalizer, where
    re-resolving every month is the slow part.
    """
    paths = paths or Paths.resolve()
    paths.ensure()
    create_tables(connection)
    registry = SourceRegistry(config, connection, paths=paths, scale=scale)
    report = IngestReport()

    for spec in sources if sources is not None else config.sources():
        resolved = registry.resolve(spec)
        missing, unexpected = registry.check_schema(resolved)
        if missing:
            raise RuntimeError(
                f"{spec} is missing columns the {resolved.vintage.name} mapping "
                f"expects: {sorted(missing)}. The vintage table is wrong about "
                "this month, or the file is not what it claims to be."
            )
        month = ingest_month(
            connection, config, resolved, unexpected_columns=tuple(sorted(unexpected))
        )
        report.months.append(month)
        if callable(on_progress):
            on_progress(month)
        if release_source:
            Path(resolved.path).unlink(missing_ok=True)

    return report


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    return int(row[0]) if row else 0
