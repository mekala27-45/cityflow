"""Where a source file comes from, and what to do when it is not there.

Two backends sit behind one interface.

`tlc` reads the published parquet. This is the real thing and it is the
default. It needs network access to the TLC distribution host.

`synthetic` runs the seeded generator. It exists because continuous
integration cannot download eight gigabytes on every push, because every
quarantine rule needs a fixture that actually contains the defect it catches at
a realistic rate and scale, and because the public demo has to work on first
click.

The interface is identical on purpose: the same vintage mapping, the same
normalizer and the same quarantine rules run over both, so the synthetic path
exercises the production path rather than bypassing it. The one difference is
recorded on every row of the audit table and printed on every report, because a
number measured on generated data has to be labelled everywhere it appears.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import duckdb

from cityflow_core.config import CityflowConfig, SourceSpec
from cityflow_core.paths import Paths
from cityflow_ingest.synthetic import (
    ZoneRow,
    generate_month,
    install_zone_tables,
    load_zones,
)
from cityflow_ingest.vintage import Vintage, expected_columns, resolve_vintage


class SourceUnavailableError(RuntimeError):
    """The file could not be fetched, with the reason preserved."""


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """A source file that now exists on disk, and how it got there."""

    spec: SourceSpec
    vintage: Vintage
    path: Path
    backend: str
    bytes_on_disk: int
    row_count: int


class SourceRegistry:
    """Resolves every source in the build window to a local parquet file."""

    def __init__(
        self,
        config: CityflowConfig,
        connection: duckdb.DuckDBPyConnection,
        *,
        paths: Paths | None = None,
        scale: float = 1.0,
    ) -> None:
        self.config = config
        self.connection = connection
        self.paths = paths or Paths.resolve()
        self.scale = scale
        self._zones: list[ZoneRow] | None = None
        self._zone_tables_installed: set[str] = set()

    @property
    def zones(self) -> list[ZoneRow]:
        if self._zones is None:
            reference = self.paths.reference / "dim_zone.csv"
            if not reference.is_file():
                raise SourceUnavailableError(
                    f"{reference} is missing. Run 'cityflow zones' first: the "
                    "generator needs the real zone list to draw from, and the "
                    "warehouse needs it to join against."
                )
            self._zones = load_zones(reference)
        return self._zones

    def resolve(self, spec: SourceSpec) -> ResolvedSource:
        vintage = resolve_vintage(spec.service, spec.period)
        if vintage.is_coordinate_era:
            raise SourceUnavailableError(
                f"{spec} falls in {vintage.name}, which carries raw coordinates "
                "rather than zone ids. Restrict the window to July 2016 onwards."
            )

        target = self.paths.raw / spec.filename
        if target.is_file() and target.stat().st_size > 0:
            return self._describe(spec, vintage, target)

        if self.config.backend == "tlc":
            self._download(spec, target)
        else:
            self._generate(spec, vintage, target)
        return self._describe(spec, vintage, target)

    def _describe(self, spec: SourceSpec, vintage: Vintage, path: Path) -> ResolvedSource:
        row = self.connection.execute(f"select count(*) from read_parquet('{path}')").fetchone()
        return ResolvedSource(
            spec=spec,
            vintage=vintage,
            path=path,
            backend=self.config.backend,
            bytes_on_disk=path.stat().st_size,
            row_count=int(row[0]) if row else 0,
        )

    def _download(self, spec: SourceSpec, target: Path) -> None:
        """Fetch the published parquet.

        curl rather than a Python HTTP client on purpose: these files run to
        several hundred megabytes, curl resumes a partial transfer, and the
        download is written to a temporary name and moved into place only on
        success, so an interrupted run does not leave a truncated parquet that
        reads as a valid file with fewer rows.
        """
        if shutil.which("curl") is None:
            raise SourceUnavailableError(
                "curl is not on PATH and the tlc backend needs it to fetch "
                f"{spec.url}. Install curl, or run with backend: synthetic."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".parquet.partial")
        result = subprocess.run(
            [
                "curl",
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--retry",
                "3",
                "--retry-delay",
                "2",
                "--continue-at",
                "-",
                "--output",
                str(partial),
                spec.url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            partial.unlink(missing_ok=True)
            raise SourceUnavailableError(
                f"Could not fetch {spec.url}\n"
                f"curl exit {result.returncode}: {result.stderr.strip()}\n"
                "If this is an egress policy denial rather than a network "
                "failure, run with backend: synthetic, which needs no network."
            )
        partial.replace(target)

    def _generate(self, spec: SourceSpec, vintage: Vintage, target: Path) -> None:
        if spec.service not in self._zone_tables_installed:
            install_zone_tables(self.connection, self.zones, spec.service)
            self._zone_tables_installed.add(spec.service)
        generate_month(self.connection, self.config, spec, vintage, target, scale=self.scale)

    def check_schema(self, resolved: ResolvedSource) -> tuple[set[str], set[str]]:
        """What the file is missing, and what it carries that we did not expect.

        Two different failures. Missing means the vintage table is wrong about
        this month and the build must stop. Unexpected means the TLC added a
        column, which is information worth printing and not a reason to fail.
        """
        described = self.connection.execute(
            f"describe select * from read_parquet('{resolved.path}')"
        ).fetchall()
        actual = {row[0] for row in described}
        expected = expected_columns(resolved.vintage)
        missing = expected - actual
        unexpected = actual - expected - set(resolved.vintage.ignored)
        return missing, unexpected
