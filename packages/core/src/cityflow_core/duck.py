"""The one place a DuckDB connection is opened.

Every session gets the same memory limit, thread count and temp directory, so a
query that works in a test works in the pipeline. Opening connections ad hoc is
how a build ends up with one process holding a 12GB limit and another holding
the default.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from cityflow_core.config import CityflowConfig
from cityflow_core.paths import Paths


def connect(
    config: CityflowConfig,
    database: Path | None = None,
    *,
    read_only: bool = False,
) -> duckdb.DuckDBPyConnection:
    """Open a configured connection. The caller closes it.

    Args:
        config: supplies the memory limit and thread count.
        database: the warehouse file, or None for an in-memory database.
        read_only: open without taking the write lock, so the dashboard
            benchmark can read a warehouse a dbt run is still writing.
    """
    paths = Paths.resolve()
    target = ":memory:" if database is None else str(database)
    if database is not None:
        database.parent.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(target, read_only=read_only)
    temp_directory = paths.warehouse_dir / "tmp"
    temp_directory.mkdir(parents=True, exist_ok=True)

    settings: dict[str, Any] = {
        "memory_limit": config.duckdb_memory_limit,
        "threads": config.duckdb_threads,
        "temp_directory": str(temp_directory),
        "preserve_insertion_order": False,
    }
    for key, value in settings.items():
        if read_only and key == "temp_directory":
            continue
        connection.execute(f"set {key} = ?", [value])
    return connection


@contextmanager
def session(
    config: CityflowConfig,
    database: Path | None = None,
    *,
    read_only: bool = False,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """A connection that closes itself, including when the body raises."""
    connection = connect(config, database, read_only=read_only)
    try:
        yield connection
    finally:
        connection.close()
