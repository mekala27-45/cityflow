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
import time
from collections.abc import Callable
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
from cityflow_ingest.vintage import Vintage, compare_columns, resolve_vintage


class SourceUnavailableError(RuntimeError):
    """The file could not be fetched, with the reason preserved."""


# How the download treats a refusal that looks like throttling: six attempts,
# the pauses between them doubling from a minute to a ceiling of five, which
# outlasts the window a rate rule counts requests over.
DOWNLOAD_ATTEMPTS = 6
DOWNLOAD_PAUSE_SECONDS = 60.0
DOWNLOAD_PAUSE_CEILING = 300.0
PARQUET_MAGIC = b"PAR1"
# What the storage behind the distribution says about an object that is not
# there, as opposed to what the firewall in front of it says about a client.
MISSING_OBJECT_MARKERS = ("AccessDenied", "NoSuchKey")


# curl exits that mean the host was never reached: a proxy, a resolver or a
# connection refusing outright. curl's own --retry has already tried those, so
# minutes more of waiting would only delay the message a person needs.
UNREACHABLE_EXITS = frozenset({5, 6, 7})


def classify_refusal(status: str, body: str, curl_exit: int, stderr: str = "") -> str:
    """What a failed fetch was: missing, refused, firewall, transfer or server.

    The first two are final and the rest are retried. A 403 is missing only when
    its body is the storage's own AccessDenied document; a 403 with any other
    body is the firewall, which also answers 202 Accepted with a challenge page.
    A host that was never reached, including an egress policy refusing the
    tunnel, is refused rather than retried.
    """
    if status in ("404", "410") or (
        status == "403" and any(marker in body for marker in MISSING_OBJECT_MARKERS)
    ):
        return "missing"
    if status in ("202", "403", "429"):
        return "firewall"
    if status == "000" and (curl_exit in UNREACHABLE_EXITS or "CONNECT tunnel failed" in stderr):
        return "refused"
    if status == "000" or (curl_exit != 0 and status in ("200", "206")):
        return "transfer"
    if status.startswith("5") or status == "416":
        return "server"
    return "refused"


def _peek(path: Path, size: int = 2048) -> str:
    """The start of whatever arrived, as text, for a body that is not parquet."""
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        head = handle.read(size)
    if head.startswith(PARQUET_MAGIC):
        return ""
    return head.decode("utf-8", errors="replace")


def _is_parquet(path: Path) -> bool:
    """Starts and ends with the parquet magic number, the cheapest proof that a
    download is the file rather than a page about it."""
    if not path.is_file() or path.stat().st_size < 2 * len(PARQUET_MAGIC):
        return False
    with path.open("rb") as handle:
        head = handle.read(len(PARQUET_MAGIC))
        handle.seek(-len(PARQUET_MAGIC), 2)
        tail = handle.read(len(PARQUET_MAGIC))
    return head == PARQUET_MAGIC and tail == PARQUET_MAGIC


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
        self.sleep: Callable[[float], None] = time.sleep
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

        Success means a 200 and a file that starts and ends with the parquet
        magic number. Anything else is read for what it is, because the status
        alone cannot say. The TLC host sits behind a firewall, and a client it
        thinks is busy gets 202 Accepted with a challenge page, which curl
        counts as a success, or a 403 that looks exactly like the 403 the
        storage behind it returns for a month that was never published. What
        tells them apart is the body: storage answers a missing object with an
        AccessDenied document, the firewall with CloudFront's own error page.
        A missing object fails at once. The firewall, a 429, a 5xx or a dropped
        connection is retried after a pause that doubles, up to five minutes,
        which is longer than the window the firewall counts requests over.
        """
        if shutil.which("curl") is None:
            raise SourceUnavailableError(
                "curl is not on PATH and the tlc backend needs it to fetch "
                f"{spec.url}. Install curl, or run with backend: synthetic."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".parquet.partial")
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            result = subprocess.run(
                [
                    "curl",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--retry",
                    "3",
                    "--retry-delay",
                    "2",
                    "--continue-at",
                    "-",
                    "--write-out",
                    "%{http_code}",
                    "--output",
                    str(partial),
                    spec.url,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            status = result.stdout.strip()[-3:] or "000"
            if result.returncode == 0 and status in ("200", "206") and _is_parquet(partial):
                partial.replace(target)
                return

            body = _peek(partial)
            refusal = classify_refusal(status, body, result.returncode, result.stderr)
            if refusal != "transfer":
                # Whatever arrived is not the file: a challenge page, an error
                # document. Resuming onto it would corrupt the next attempt. A
                # dropped transfer keeps its bytes, which --continue-at resumes.
                partial.unlink(missing_ok=True)
            if refusal in ("missing", "refused") or attempt == DOWNLOAD_ATTEMPTS:
                partial.unlink(missing_ok=True)
                detail = result.stderr.strip() or f"HTTP {status}"
                excerpt = " ".join(body.split())[:200]
                raise SourceUnavailableError(
                    f"Could not fetch {spec.url}\n"
                    f"curl exit {result.returncode}, HTTP {status}: {detail}\n"
                    + (f"The host answered: {excerpt}\n" if excerpt else "")
                    + {
                        "missing": "The object is not there: this month is not published.\n",
                        "refused": "The host refused the request outright.\n",
                    }.get(
                        refusal,
                        f"The host refused {attempt} attempts in a row, which is its "
                        "firewall throttling this client. Wait, or run from another network.\n",
                    )
                    + "If this is an egress policy denial rather than a network "
                    "failure, run with backend: synthetic, which needs no network."
                )
            self.sleep(min(DOWNLOAD_PAUSE_SECONDS * 2 ** (attempt - 1), DOWNLOAD_PAUSE_CEILING))

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
        Names compare without regard to case, as DuckDB binds them; see
        vintage.compare_names for the month that made this matter.
        """
        described = self.connection.execute(
            f"describe select * from read_parquet('{resolved.path}')"
        ).fetchall()
        check = compare_columns((str(row[0]) for row in described), resolved.vintage)
        return set(check.missing), set(check.unexpected)
