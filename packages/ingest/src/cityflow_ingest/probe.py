"""Read every published file's schema, row count and size, and download none of them.

A build against the real files downloads tens of gigabytes and runs for hours,
and the schema check in the ingest stops it on the first month whose columns do
not match its vintage, after every earlier month has been fetched and landed.
Parquet keeps its schema and its row count in a footer, so both can be read for
every month of a window with a few range requests and no download. That is what
this module does, so a real build meets an unexpected schema in a minute rather
than an hour in, and so the size of a window is known before a runner is
committed to it.

The comparison is the ingest's own, expected_columns against the file, with one
distinction kept apart: a column whose name differs from the vintage table only
in case. DuckDB binds column names without regard to case, so the normalizer
reads such a column, while an exact comparison calls it missing. The two need
different fixes, so they are reported separately.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import duckdb

from cityflow_core.config import SourceSpec
from cityflow_ingest.vintage import UnknownVintageError, Vintage, expected_columns, resolve_vintage


@dataclass(frozen=True, slots=True)
class Head:
    """What a HEAD request, or a stat of a local file, says about one location."""

    published: bool
    bytes: int | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ColumnCheck:
    """One file's columns against its vintage's expected set."""

    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    # (the name the vintage table uses, the name the file uses)
    case_only: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class FileProbe:
    """Everything the footer says about one source file."""

    source: str
    service: str
    period: str
    location: str
    vintage: str | None
    published: bool
    bytes: int | None = None
    rows: int | None = None
    row_groups: int | None = None
    columns: tuple[tuple[str, str], ...] = ()
    missing: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()
    case_only: tuple[tuple[str, str], ...] = ()
    error: str | None = None

    @property
    def readable(self) -> bool:
        """Published, read, and carrying every column its vintage expects."""
        return self.published and self.error is None and not self.missing


def compare_columns(actual: Iterable[str], vintage: Vintage) -> ColumnCheck:
    """Missing, unexpected, and different only in case, as three separate sets."""
    actual_set = set(actual)
    expected = expected_columns(vintage)
    ignored = {name.lower() for name in vintage.ignored}
    by_lower = {name.lower(): name for name in actual_set}

    missing: list[str] = []
    case_only: list[tuple[str, str]] = []
    for name in sorted(expected - actual_set):
        found = by_lower.get(name.lower())
        if found is None:
            missing.append(name)
        else:
            case_only.append((name, found))

    claimed = {found for _, found in case_only}
    expected_lower = {name.lower() for name in expected}
    unexpected = sorted(
        name
        for name in actual_set - expected - claimed
        if name.lower() not in ignored and name.lower() not in expected_lower
    )
    return ColumnCheck(tuple(missing), tuple(unexpected), tuple(case_only))


def head_url(url: str) -> Head:
    """HEAD the published file. A missing object on this host answers 403 or 404."""
    if shutil.which("curl") is None:
        return Head(published=False, detail="curl is not on PATH")
    result = subprocess.run(
        ["curl", "--silent", "--show-error", "--head", "--location", "--max-time", "60", url],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return Head(
            published=False, detail=f"curl exit {result.returncode}: {result.stderr.strip()}"
        )
    status: int | None = None
    length: int | None = None
    for line in result.stdout.splitlines():
        lowered = line.strip().lower()
        if lowered.startswith("http/"):
            parts = lowered.split()
            status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            length = None
        elif lowered.startswith("content-length:"):
            value = lowered.split(":", 1)[1].strip()
            length = int(value) if value.isdigit() else None
    if status == 200:
        return Head(published=True, bytes=length)
    return Head(published=False, detail=f"HTTP {status}")


def head_path(path: str) -> Head:
    """The local equivalent, which is how the tests run the probe."""
    target = Path(path)
    if not target.is_file():
        return Head(published=False, detail="no such file")
    return Head(published=True, bytes=target.stat().st_size)


def _quoted(location: str) -> str:
    return "'" + location.replace("'", "''") + "'"


def probe_source(
    connection: duckdb.DuckDBPyConnection,
    spec: SourceSpec,
    location: str,
    head: Callable[[str], Head],
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

    answer = head(location)
    if not answer.published:
        return FileProbe(**base, vintage=vintage.name, published=False, error=answer.detail or None)

    quoted = _quoted(location)
    try:
        described = connection.execute(f"describe select * from read_parquet({quoted})").fetchall()
        meta = connection.execute(
            f"select num_rows, num_row_groups from parquet_file_metadata({quoted})"
        ).fetchone()
    except duckdb.Error as error:
        return FileProbe(
            **base, vintage=vintage.name, published=True, bytes=answer.bytes, error=str(error)
        )

    columns = tuple((str(row[0]), str(row[1])) for row in described)
    check = compare_columns((name for name, _ in columns), vintage)
    return FileProbe(
        **base,
        vintage=vintage.name,
        published=True,
        bytes=answer.bytes,
        rows=int(meta[0]) if meta else None,
        row_groups=int(meta[1]) if meta else None,
        columns=columns,
        missing=check.missing,
        unexpected=check.unexpected,
        case_only=check.case_only,
    )


def probe_window(
    connection: duckdb.DuckDBPyConnection,
    sources: Sequence[SourceSpec],
    *,
    local_dir: Path | None = None,
) -> list[FileProbe]:
    """Probe every source, from the TLC host or from a local directory."""
    if local_dir is None:
        connection.execute("install httpfs")
        connection.execute("load httpfs")
        return [probe_source(connection, spec, spec.url, head_url) for spec in sources]
    return [
        probe_source(connection, spec, str(local_dir / spec.filename), head_path)
        for spec in sources
    ]


def schema_groups(probes: Sequence[FileProbe]) -> list[dict[str, Any]]:
    """Consecutive published months of one service that share a vintage and an
    identical schema, with their rows and bytes. A change of column name, type or
    order starts a new group, which is how a drift inside a vintage shows up."""
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
            }
        )
    return groups


def write_report(probes: Sequence[FileProbe], path: Path) -> None:
    """Every file's probe and the schema groups, as one JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "files": [asdict(probe) for probe in probes],
        "groups": schema_groups(probes),
    }
    path.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
