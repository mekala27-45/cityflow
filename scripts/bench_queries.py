"""Measure every panel query: latency, bytes read, and row groups pruned.

Two measurements, taken two ways, because they answer different questions.

**Latency** is measured here, locally, against the same parquet the browser
reads. It is the lower bound: no network, no WebAssembly. If a query is slow
here it will be slow everywhere, and the cause is the query or the grain.

**Bytes pulled** cannot be measured here at all, because a local read is a file
read. It is measured in the browser by the smoke test, which counts the range
requests DuckDB-WASM issues, and this script folds that log in when it is
present. That column is the one that proves the range requests are working, and
it is the most interesting number in the repository: it is the difference
between "the file is 3.5MB" and "the query fetched 67KB of it".

**Row group pruning** is computed from the parquet footer rather than timed.
For each query's filter this reads the column statistics of every row group and
counts how many could be skipped outright. That number is a property of how the
file was sorted and written, and it is what makes the bytes column small.

Usage:
    python scripts/bench_queries.py [--repeats 25] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from cityflow_core import Paths, load_config

BROWSER_LOG = Path("web/tests/query-log.json")


@dataclass(frozen=True, slots=True)
class Benchmark:
    """One panel query, with the filter that decides what can be pruned."""

    panel: str
    name: str
    file: str
    sql: str
    prune_column: str | None = None
    prune_value: Any = None


@dataclass(slots=True)
class Result:
    benchmark: Benchmark
    rows: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    file_bytes: int
    row_groups: int
    row_groups_kept: int
    browser_bytes: int | None = None
    browser_requests: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def row_groups_skipped(self) -> int:
        return self.row_groups - self.row_groups_kept

    @property
    def prune_pct(self) -> float:
        return 100.0 * self.row_groups_skipped / self.row_groups if self.row_groups else 0.0


def benchmarks(paths: Paths) -> list[Benchmark]:
    zone_hour = str(paths.shipped / "agg_zone_hour.parquet")
    hour_of_week = str(paths.shipped / "agg_hour_of_week.parquet")
    daily = str(paths.shipped / "agg_daily.parquet")
    od = str(paths.shipped / "agg_od_flow.parquet")
    duration = str(paths.shipped / "agg_duration_dist.parquet")
    fare = str(paths.shipped / "agg_fare_distance.parquet")
    detail = next(iter(sorted(paths.shipped.glob("detail_*.parquet"))), None)

    out = [
        Benchmark(
            "pulse",
            "KPI tiles, whole window",
            "agg_zone_hour.parquet",
            f"select sum(trips), sum(total_amount_sum), sum(duration_s_sum), "
            f"sum(tip_obs_tip_sum), sum(tip_obs_fare_sum), sum(airport_trips) "
            f"from read_parquet('{zone_hour}')",
        ),
        Benchmark(
            "pulse",
            "Daily volume with trend",
            "agg_daily.parquet",
            f"select date_day, service, sum(trips) from read_parquet('{daily}') "
            f"group by 1, 2 order by 1",
        ),
        Benchmark(
            "pulse",
            "Hour of week grid",
            "agg_hour_of_week.parquet",
            f"select day_of_week, hour, sum(trips) from read_parquet('{hour_of_week}') "
            f"group by 1, 2",
        ),
        Benchmark(
            "pulse",
            "Hour of week, one service",
            "agg_hour_of_week.parquet",
            f"select day_of_week, hour, sum(trips) from read_parquet('{hour_of_week}') "
            f"where service = 'yellow' group by 1, 2",
            prune_column="service",
            prune_value="yellow",
        ),
        Benchmark(
            "geography",
            "Choropleth, trips by zone",
            "agg_zone_hour.parquet",
            f"select pu_zone_id, sum(trips) from read_parquet('{zone_hour}') group by 1",
        ),
        Benchmark(
            "geography",
            "One zone drilled, every hour",
            "agg_zone_hour.parquet",
            f"select hour, sum(trips), sum(tipped_trips), sum(tip_obs_trips) "
            f"from read_parquet('{zone_hour}') where pu_zone_id = 161 group by 1",
            prune_column="pu_zone_id",
            prune_value=161,
        ),
        Benchmark(
            "geography",
            "Top origin destination flows",
            "agg_od_flow.parquet",
            f"select pu_zone_id, do_zone_id, sum(trips) as t "
            f"from read_parquet('{od}') group by 1, 2 order by t desc limit 150",
        ),
        Benchmark(
            "geography",
            "Flows out of one zone",
            "agg_od_flow.parquet",
            f"select do_zone_id, sum(trips) from read_parquet('{od}') "
            f"where pu_zone_id = 161 group by 1",
            prune_column="pu_zone_id",
            prune_value=161,
        ),
        Benchmark(
            "behavior",
            "Duration ridgeline",
            "agg_duration_dist.parquet",
            f"select hour, minute_bucket, sum(trips) from read_parquet('{duration}') group by 1, 2",
        ),
        Benchmark(
            "behavior",
            "Fare against distance hexbin",
            "agg_fare_distance.parquet",
            f"select hex_q, hex_r, sum(trips), avg(mean_fare) "
            f"from read_parquet('{fare}') group by 1, 2",
        ),
        Benchmark(
            "mix",
            "Service share by month",
            "agg_zone_hour.parquet",
            f"select month, service, sum(trips) from read_parquet('{zone_hour}') group by 1, 2",
        ),
    ]
    if detail is not None:
        out.append(
            Benchmark(
                "geography",
                "Detail month, one zone at trip grain",
                detail.name,
                f"select * from read_parquet('{detail}') where pu_zone_id = 161 "
                f"order by pickup_ts limit 500",
                prune_column="pu_zone_id",
                prune_value=161,
            )
        )
    return out


def row_group_stats(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    column: str | None,
    value: Any,
) -> tuple[int, int]:
    """Total row groups, and how many a statistics based filter would keep.

    Read from the parquet footer rather than inferred from a timing. A row
    group is kept when the filter value falls inside its recorded minimum and
    maximum for that column, which is exactly the test the reader applies.
    """
    total_row = connection.execute(
        "select count(distinct row_group_id) from parquet_metadata(?)", [str(path)]
    ).fetchone()
    total = int(total_row[0]) if total_row else 0
    if column is None or total == 0:
        return total, total

    kept_row = connection.execute(
        """
        select count(*) from (
            select row_group_id, any_value(stats_min) as lo, any_value(stats_max) as hi
            from parquet_metadata(?)
            where path_in_schema = ?
            group by row_group_id
        )
        where lo is null or hi is null
           or (try_cast(? as varchar) >= lo and try_cast(? as varchar) <= hi)
        """,
        [str(path), column, str(value), str(value)],
    ).fetchone()
    return total, int(kept_row[0]) if kept_row else total


def time_query(
    connection: duckdb.DuckDBPyConnection, sql: str, repeats: int
) -> tuple[int, list[float]]:
    # One warm run first, so the measurement is of the query and not of the
    # first read of the parquet footer.
    rows = len(connection.execute(sql).fetchall())
    timings: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        connection.execute(sql).fetchall()
        timings.append((time.perf_counter() - start) * 1000.0)
    return rows, timings


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
    return ordered[index]


def browser_log(paths: Paths) -> dict[str, dict[str, int]]:
    """Bytes and request counts captured in the browser, when available."""
    path = paths.root / BROWSER_LOG
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict[str, int]] = {}
    for entry in raw if isinstance(raw, list) else raw.get("queries", []):
        label = str(entry.get("label") or entry.get("name") or "")
        if not label:
            continue
        out[label] = {
            "bytes": int(entry.get("bytes", 0)),
            "requests": int(entry.get("requests", 0)),
        }
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=25)
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    config = load_config()
    paths = Paths.resolve()
    connection = duckdb.connect()
    connection.execute("set threads = ?", [config.duckdb_threads])
    captured = browser_log(paths)

    results: list[Result] = []
    for benchmark in benchmarks(paths):
        path = paths.shipped / benchmark.file
        if not path.is_file():
            print(f"skipping {benchmark.name}: {path.name} is not published")
            continue
        rows, timings = time_query(connection, benchmark.sql, args.repeats)
        total, kept = row_group_stats(
            connection, path, benchmark.prune_column, benchmark.prune_value
        )
        entry = captured.get(benchmark.name, {})
        results.append(
            Result(
                benchmark=benchmark,
                rows=rows,
                p50_ms=round(statistics.median(timings), 2),
                p95_ms=round(percentile(timings, 0.95), 2),
                p99_ms=round(percentile(timings, 0.99), 2),
                file_bytes=path.stat().st_size,
                row_groups=total,
                row_groups_kept=kept,
                browser_bytes=entry.get("bytes"),
                browser_requests=entry.get("requests"),
            )
        )

    if not results:
        print("Nothing to benchmark: the shipped layer is not built.")
        return 2

    header = (
        f"{'panel':<10} {'query':<38} {'rows':>7} {'p50':>7} {'p95':>7} "
        f"{'p99':>7} {'file MB':>8} {'groups':>7} {'pruned':>7}"
    )
    print(header)
    print("-" * len(header))
    for result in results:
        print(
            f"{result.benchmark.panel:<10} {result.benchmark.name:<38} "
            f"{result.rows:>7,} {result.p50_ms:>7.2f} {result.p95_ms:>7.2f} "
            f"{result.p99_ms:>7.2f} {result.file_bytes / 1e6:>8.2f} "
            f"{result.row_groups:>7} {result.prune_pct:>6.0f}%"
        )

    if captured:
        print("\nMeasured in the browser (range requests, not local reads):")
        for result in results:
            if result.browser_bytes is None:
                continue
            print(
                f"  {result.benchmark.name:<40} "
                f"{result.browser_bytes / 1024:>8.1f} KB in "
                f"{result.browser_requests} requests, from a "
                f"{result.file_bytes / 1e6:.2f}MB file"
            )
    else:
        print(
            "\nNo browser query log found. Run 'make web-test' to capture "
            "bytes pulled; local reads cannot measure it."
        )

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                [
                    {
                        "panel": r.benchmark.panel,
                        "query": r.benchmark.name,
                        "file": r.benchmark.file,
                        "rows": r.rows,
                        "p50_ms": r.p50_ms,
                        "p95_ms": r.p95_ms,
                        "p99_ms": r.p99_ms,
                        "file_bytes": r.file_bytes,
                        "row_groups": r.row_groups,
                        "row_groups_skipped": r.row_groups_skipped,
                        "browser_bytes": r.browser_bytes,
                        "browser_requests": r.browser_requests,
                    }
                    for r in results
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
