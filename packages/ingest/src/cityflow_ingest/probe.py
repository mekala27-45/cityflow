"""Read every published file's schema, row count and size, and download none of them.

A build against the real files downloads tens of gigabytes and runs for hours,
and the schema check in the ingest stops it on the first month whose columns do
not match its vintage, after every earlier month has been fetched and landed.
Parquet keeps its schema, its row count and per column statistics in a footer,
so all of them can be read for every month of a window with a few range
requests and no download. That is what this module does, so a real build meets
an unexpected schema in a minute rather than an hour in, and so the size of a
window is known before a runner is committed to it.

The comparison is the ingest's own, vintage.compare_columns, so the probe and the
build cannot disagree about a file. It keeps a column whose name differs from
the vintage table only in case apart from a missing one: DuckDB binds names
without regard to case, so the normalizer reads such a column, and the two need
different fixes.

One footer read per file, and slowly. The TLC host sits behind a web
application firewall that starts answering 202 with a challenge page, then 403,
once a runner has made a few thousand requests in a few minutes; the second
real probe found that out at file two hundred and thirty. So the footer is read
once through parquet_metadata, which carries the schema, the row groups, the
null counts and the min and max of every column, the HTTP metadata cache is on,
files are spaced out, and a refusal that looks like throttling is retried after
a pause rather than recorded as a missing month.

A census is the one thing here that reads data: for one named column it reads
that column alone, through projection pushdown, and counts its non null and
non zero values. It exists to answer a question footers cannot, which is
whether a column the vintage table dates to a later month already carried a
fee.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import duckdb

from cityflow_core.config import SourceSpec
from cityflow_ingest.vintage import UnknownVintageError, compare_columns, resolve_vintage

# Status codes in a DuckDB HTTP error that mean the object is not there, and
# ones that mean the host is refusing this client for now. The firewall's
# challenge comes back as 202 Accepted, which is a success code to most
# clients, and a dropped connection reports itself as HTTP 0.
NOT_PUBLISHED = frozenset({403, 404})
THROTTLED = frozenset({0, 202, 429, 500, 502, 503, 504})
_URL = re.compile(r"""https?://[^\s'"]+""")
_STATUS = re.compile(r"(?:HTTP |: )(\d{1,3})\b")


def status_codes(message: str) -> set[int]:
    """The HTTP status codes an error message reports.

    URLs are removed first, since every file name carries a year: a message
    about yellow_tripdata_2024-01 would otherwise read as a 202. What is left
    is matched only where DuckDB writes a status, after "HTTP " or ": ".
    """
    return {int(code) for code in _STATUS.findall(_URL.sub("<url>", message))}


@dataclass(frozen=True, slots=True)
class Census:
    """One column of one file, read in full."""

    column: str
    non_null: int
    non_zero: int
    minimum: float | None
    maximum: float | None


@dataclass(frozen=True, slots=True)
class FileProbe:
    """Everything the footer says about one source file."""

    source: str
    service: str
    period: str
    location: str
    vintage: str | None
    published: bool
    # On disk for a local file. For a remote one, the compressed size of its
    # column chunks, which is the file less its footer: close enough to plan a
    # download by, and it costs no extra request.
    bytes: int | None = None
    rows: int | None = None
    row_groups: int | None = None
    # Name and physical type, in file order.
    columns: tuple[tuple[str, str], ...] = ()
    missing: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()
    case_only: tuple[tuple[str, str], ...] = ()
    # Columns the footer statistics say hold nothing but nulls. A column can be
    # present in a file long before it carries a value, and the vintage table
    # dates a column by the month it carries one.
    empty: tuple[str, ...] = ()
    # Rows carrying a value, per column, from the footer's null counts. None
    # where a row group's statistics leave the null count out.
    values: tuple[tuple[str, int | None], ...] = ()
    # The smallest and largest value the footer records for each unexpected
    # column, where the statistics carry them.
    ranges: tuple[tuple[str, float | None, float | None], ...] = ()
    census: Census | None = None
    attempts: int = 1
    error: str | None = None

    @property
    def readable(self) -> bool:
        """Published, read, and carrying every column its vintage expects."""
        return self.published and self.error is None and not self.missing


def _quoted(location: str) -> str:
    return "'" + location.replace("'", "''") + "'"


def _number(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


FOOTER_SQL = """
select row_group_id, row_group_num_rows, column_id, path_in_schema, type,
       stats_null_count, stats_min_value, stats_max_value, total_compressed_size
from parquet_metadata({location})
order by row_group_id, column_id
"""


def read_footer(
    connection: duckdb.DuckDBPyConnection,
    location: str,
    *,
    retries: int = 3,
    pause: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[tuple[Any, ...]], int]:
    """The footer's rows, and how many attempts it took. Raises the last error.

    A throttling refusal is retried after a pause that doubles each time. A
    refusal that means the file is not there is raised at once.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            rows = connection.execute(FOOTER_SQL.format(location=_quoted(location))).fetchall()
            return rows, attempt
        except duckdb.Error as error:
            throttled = bool(status_codes(str(error)) & THROTTLED)
            if not throttled or attempt > retries:
                raise
            sleep(pause * 2 ** (attempt - 1))


def take_census(connection: duckdb.DuckDBPyConnection, location: str, column: str) -> Census:
    """Read one column in full and count what is in it."""
    quoted_column = '"' + column.replace('"', '""') + '"'
    row = connection.execute(
        f"select count({quoted_column}), "
        f"count(*) filter (where {quoted_column} <> 0), "
        f"min({quoted_column}), max({quoted_column}) "
        f"from read_parquet({_quoted(location)})"
    ).fetchone()
    assert row is not None
    return Census(
        column=column,
        non_null=int(row[0]),
        non_zero=int(row[1]),
        minimum=_number(row[2]),
        maximum=_number(row[3]),
    )


def probe_source(
    connection: duckdb.DuckDBPyConnection,
    spec: SourceSpec,
    location: str,
    *,
    local: bool,
    census: str | None = None,
    retries: int = 3,
    pause: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> FileProbe:
    """Read one file's footer and compare its columns with its vintage."""
    base: dict[str, Any] = {
        "source": str(spec),
        "service": spec.service,
        "period": spec.period_key,
        "location": location,
    }
    try:
        vintage = resolve_vintage(spec.service, spec.period)
    except UnknownVintageError as error:
        return FileProbe(**base, vintage=None, published=False, error=str(error))

    if local and not Path(location).is_file():
        return FileProbe(**base, vintage=vintage.name, published=False, error="no such file")

    try:
        footer, attempts = read_footer(
            connection, location, retries=0 if local else retries, pause=pause, sleep=sleep
        )
    except duckdb.Error as error:
        message = str(error).strip().splitlines()[0]
        if not local and status_codes(message) & NOT_PUBLISHED:
            return FileProbe(**base, vintage=vintage.name, published=False, error=message)
        return FileProbe(**base, vintage=vintage.name, published=True, error=message)

    columns: dict[int, tuple[str, str]] = {}
    group_rows: dict[int, int] = {}
    nulls: dict[str, list[int | None]] = {}
    minimums: dict[str, list[float | None]] = {}
    maximums: dict[str, list[float | None]] = {}
    compressed = 0
    for group, group_count, column_id, name, kind, null_count, low, high, size in footer:
        columns.setdefault(int(column_id), (str(name), str(kind)))
        group_rows[int(group)] = int(group_count)
        nulls.setdefault(str(name), []).append(None if null_count is None else int(null_count))
        minimums.setdefault(str(name), []).append(_number(low))
        maximums.setdefault(str(name), []).append(_number(high))
        compressed += int(size or 0)

    ordered = tuple(columns[key] for key in sorted(columns))
    rows = sum(group_rows.values())
    check = compare_columns((name for name, _ in ordered), vintage)
    values = tuple(
        (name, None if any(n is None for n in counts) else rows - sum(n or 0 for n in counts))
        for name, counts in sorted(nulls.items())
    )
    empty = tuple(name for name, count in values if rows > 0 and count == 0)
    ranges = tuple(
        (
            name,
            min((v for v in minimums[name] if v is not None), default=None),
            max((v for v in maximums[name] if v is not None), default=None),
        )
        for name in check.unexpected
    )

    counted: Census | None = None
    if census is not None:
        present = {name.lower(): name for name, _ in ordered}
        if census.lower() in present:
            counted = take_census(connection, location, present[census.lower()])

    return FileProbe(
        **base,
        vintage=vintage.name,
        published=True,
        bytes=Path(location).stat().st_size if local else compressed,
        rows=rows,
        row_groups=len(group_rows),
        columns=ordered,
        missing=check.missing,
        unexpected=check.unexpected,
        case_only=check.case_only,
        empty=empty,
        values=values,
        ranges=ranges,
        census=counted,
        attempts=attempts,
    )


def probe_window(
    connection: duckdb.DuckDBPyConnection,
    sources: Sequence[SourceSpec],
    *,
    local_dir: Path | None = None,
    census: str | None = None,
    spacing: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> list[FileProbe]:
    """Probe every source, from the TLC host or from a local directory."""
    if local_dir is not None:
        return [
            probe_source(
                connection, spec, str(local_dir / spec.filename), local=True, census=census
            )
            for spec in sources
        ]

    connection.execute("install httpfs")
    connection.execute("load httpfs")
    connection.execute("set enable_http_metadata_cache = true")
    probes: list[FileProbe] = []
    for index, spec in enumerate(sources):
        if index:
            sleep(spacing)
        probes.append(probe_source(connection, spec, spec.url, local=False, census=census))
    return probes


def schema_groups(probes: Sequence[FileProbe]) -> list[dict[str, Any]]:
    """Consecutive published months of one service that share a vintage and an
    identical schema, with their rows and bytes. A change of column name, type or
    order starts a new group, which is how a drift inside a vintage shows up.
    A group's empty columns are the ones empty in every one of its months, and
    for each unexpected column it counts the rows that carry a value, which is
    what says whether a column the vintage table does not expect yet is a
    placeholder or a fee already being collected."""
    groups: list[dict[str, Any]] = []
    for probe in sorted(probes, key=lambda p: (p.service, p.period)):
        if not probe.published or probe.error:
            continue
        signature = [list(column) for column in probe.columns]
        last = groups[-1] if groups else None
        if (
            last is not None
            and last["service"] == probe.service
            and last["vintage"] == probe.vintage
            and last["columns"] == signature
        ):
            last["last"] = probe.period
            last["months"] += 1
            last["rows"] += probe.rows or 0
            last["bytes"] += probe.bytes or 0
            last["empty"] = [name for name in last["empty"] if name in probe.empty]
            _add_values(last["unexpected_values"], probe)
            _widen_ranges(last["unexpected_ranges"], probe)
            continue
        groups.append(
            {
                "service": probe.service,
                "vintage": probe.vintage,
                "first": probe.period,
                "last": probe.period,
                "months": 1,
                "rows": probe.rows or 0,
                "bytes": probe.bytes or 0,
                "columns": signature,
                "missing": list(probe.missing),
                "unexpected": list(probe.unexpected),
                "case_only": [list(pair) for pair in probe.case_only],
                "empty": list(probe.empty),
                "unexpected_values": _add_values(dict.fromkeys(probe.unexpected, 0), probe),
                "unexpected_ranges": _widen_ranges({}, probe),
            }
        )
    return groups


def _add_values(totals: dict[str, int | None], probe: FileProbe) -> dict[str, int | None]:
    """Add one month's value counts for the unexpected columns into a group's
    running totals. A month whose footer leaves a null count out makes the
    total unknown rather than quietly smaller."""
    counts = dict(probe.values)
    for name, running in totals.items():
        count = counts.get(name)
        totals[name] = None if running is None or count is None else running + count
    return totals


def _widen_ranges(
    ranges: dict[str, list[float | None]], probe: FileProbe
) -> dict[str, list[float | None]]:
    for name, low, high in probe.ranges:
        current = ranges.setdefault(name, [None, None])
        if low is not None:
            current[0] = low if current[0] is None else min(current[0], low)
        if high is not None:
            current[1] = high if current[1] is None else max(current[1], high)
    return ranges


def write_report(probes: Sequence[FileProbe], path: Path) -> None:
    """Every file's probe and the schema groups, as one JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "files": [asdict(probe) for probe in probes],
        "groups": schema_groups(probes),
    }
    path.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")


def _amount(value: float | None) -> str:
    return "unknown" if value is None else f"{value:g}"


def group_notes(group: dict[str, Any]) -> list[str]:
    """What is worth saying about one schema group, in words."""
    notes: list[str] = []
    if group["missing"]:
        notes.append("missing " + ", ".join(group["missing"]))
    if group["case_only"]:
        notes.append(
            "case only " + ", ".join(f"{want} is {have}" for want, have in group["case_only"])
        )
    for name in group["unexpected"]:
        count = group["unexpected_values"].get(name)
        if count is None:
            detail = "no null count in the footer"
        elif count == 0:
            detail = "all null"
        else:
            detail = f"values in {count:,} rows"
            low, high = group.get("unexpected_ranges", {}).get(name, [None, None])
            if low is not None or high is not None:
                detail += f", {_amount(low)} to {_amount(high)}"
        notes.append(f"unexpected {name} ({detail})")
    expected_empty = [name for name in group["empty"] if name not in group["unexpected"]]
    if expected_empty:
        notes.append("all null " + ", ".join(expected_empty))
    return notes


def census_lines(probes: Sequence[FileProbe]) -> list[str]:
    """One line per file that was censused, in source order."""
    lines: list[str] = []
    for probe in sorted(probes, key=lambda p: (p.service, p.period)):
        if probe.census is None:
            continue
        c = probe.census
        lines.append(
            f"{probe.source} {c.column}: {c.non_null:,} of {probe.rows or 0:,} rows carry a "
            f"value, {c.non_zero:,} non zero, {_amount(c.minimum)} to {_amount(c.maximum)}"
        )
    return lines
