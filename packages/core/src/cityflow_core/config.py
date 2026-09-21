"""Configuration for the pipeline, loaded from config/cityflow.yml.

Two things live here that are usually scattered and should not be: the exact
threshold every quarantine rule uses, and the source registry. Both get
published in docs/data.md, and both are read from this one object so the
document and the code cannot drift.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from cityflow_core.paths import repo_root

Service = Literal["yellow", "green", "fhvhv"]
Backend = Literal["tlc", "synthetic"]

# The public TLC distribution. Kept as a template rather than a list of URLs so
# a new month needs a config change, not a code change.
TLC_URL_TEMPLATE = "https://d37ci6vzurychx.cloudfront.net/trip-data/{service}_tripdata_{year:04d}-{month:02d}.parquet"


class StrictModel(BaseModel):
    """Reject unknown keys.

    A typo in a config file that silently takes the default is the failure mode
    this prevents. Ported from trajectory, where it caught two.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class QuarantineThresholds(StrictModel):
    """Every number a quarantine rule compares against, in one place.

    Each of these is published in docs/data.md next to the count of rows it
    removed. Changing one here changes the document, because the document reads
    this object.
    """

    period_tolerance_days: int = Field(
        default=2,
        ge=0,
        description=(
            "A trip may fall this far outside its file's own month before it is "
            "quarantined. Two days absorbs a genuine trip that starts on the last "
            "night of a month and is recorded in the next file, without admitting "
            "the 2001 and 2098 timestamps the files actually contain."
        ),
    )
    max_implied_speed_mph: float = Field(
        default=80.0,
        gt=0,
        description=(
            "Implied speed above this is a data error, not a fast driver. 80 mph "
            "is above any sustained speed achievable on NYC streets or the bridges "
            "and parkways in the service area, so it quarantines record errors "
            "without touching legitimate long airport runs at highway speed."
        ),
    )
    max_trip_hours: float = Field(
        default=12.0,
        gt=0,
        description="A trip longer than this is a meter left running, not a trip.",
    )
    max_trip_miles: float = Field(
        default=200.0,
        gt=0,
        description="Beyond the furthest plausible destination in the service area.",
    )
    max_fare_amount: float = Field(
        default=2000.0,
        gt=0,
        description="Above this the fare column is a data entry error.",
    )
    max_passenger_count: int = Field(
        default=9,
        gt=0,
        description="Larger than the largest licensed vehicle in the fleet.",
    )
    min_trip_seconds: int = Field(
        default=10,
        ge=0,
        description="Shorter than this is a meter toggled on and off, not a trip.",
    )


class SourceSpec(StrictModel):
    """One source file: a service and a month."""

    service: Service
    year: int = Field(ge=2009, le=2100)
    month: int = Field(ge=1, le=12)

    @property
    def period(self) -> dt.date:
        return dt.date(self.year, self.month, 1)

    @property
    def period_key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def url(self) -> str:
        return TLC_URL_TEMPLATE.format(service=self.service, year=self.year, month=self.month)

    @property
    def filename(self) -> str:
        return f"{self.service}_tripdata_{self.period_key}.parquet"

    def __str__(self) -> str:
        return f"{self.service}:{self.period_key}"


class SyntheticSpec(StrictModel):
    """Controls for the seeded generator.

    The generator exists because CI cannot download eight gigabytes and because
    every quarantine rule needs a fixture that actually contains the defect it
    catches. It reproduces the TLC schema and its defects, not its values.
    """

    seed: int = Field(default=20260916, description="Fixed, so a build is reproducible.")
    trips_per_month: dict[str, int] = Field(
        default_factory=lambda: {"yellow": 3_000_000, "green": 55_000, "fhvhv": 19_000_000},
        description="Approximate monthly volume per service, matched to the real files.",
    )
    defect_rates: dict[str, float] = Field(
        default_factory=lambda: {
            "timestamp_out_of_period": 0.00012,
            "dropoff_before_pickup": 0.00008,
            "non_positive_distance": 0.0031,
            "non_positive_fare": 0.00055,
            "implausible_speed": 0.00042,
            "unknown_zone": 0.0146,
            "exact_duplicate": 0.00004,
            "passenger_count_zero": 0.0021,
        },
        description=(
            "Injection rates, chosen to sit in the same order of magnitude as the "
            "published rates in the real files so the quarantine panel is not a "
            "cartoon. The exact values are stated in docs/data.md."
        ),
    )


class CityflowConfig(StrictModel):
    """The whole build, described."""

    backend: Backend = Field(
        default="tlc",
        description="tlc reads the real public parquet. synthetic runs the generator.",
    )
    services: tuple[Service, ...] = Field(default=("yellow", "green", "fhvhv"))
    start: dt.date = Field(default=dt.date(2024, 1, 1))
    end: dt.date = Field(default=dt.date(2024, 12, 1))
    detail_month: dt.date = Field(
        default=dt.date(2024, 6, 1),
        description=(
            "The one month published at trip grain so browser drill-down reaches "
            "an actual row rather than stopping at an aggregate."
        ),
    )
    quarantine: QuarantineThresholds = Field(default_factory=QuarantineThresholds)
    synthetic: SyntheticSpec = Field(default_factory=SyntheticSpec)
    duckdb_memory_limit: str = Field(default="12GB")
    duckdb_threads: int = Field(default=4, gt=0)
    shipped_file_budget_mb: float = Field(
        default=95.0,
        gt=0,
        description="GitHub refuses a file over 100MB. 95 leaves room to be wrong.",
    )
    shipped_total_budget_mb: float = Field(default=400.0, gt=0)

    @model_validator(mode="after")
    def check_window(self) -> Self:
        if self.end < self.start:
            raise ValueError("end must not be before start")
        if not (self.start <= self.detail_month <= self.end):
            raise ValueError("detail_month must fall inside the build window")
        if self.detail_month.day != 1:
            raise ValueError("detail_month must be the first of a month")
        return self

    def months(self) -> list[dt.date]:
        """Every month in the window, inclusive at both ends."""
        out: list[dt.date] = []
        cursor = self.start.replace(day=1)
        last = self.end.replace(day=1)
        while cursor <= last:
            out.append(cursor)
            year, month = cursor.year, cursor.month
            cursor = dt.date(year + month // 12, month % 12 + 1, 1)
        return out

    def sources(self) -> list[SourceSpec]:
        """The full cross of services and months, in a stable order."""
        return [
            SourceSpec(service=service, year=month.year, month=month.month)
            for month in self.months()
            for service in self.services
        ]


def load_config(path: Path | None = None) -> CityflowConfig:
    """Read config/cityflow.yml, or the defaults when it is absent.

    CITYFLOW_BACKEND overrides the backend after the file is read, so the same
    committed configuration can build either warehouse without an edit. That is
    the only field with an override: everything else is a property of the build
    and belongs in version control where a reviewer can see it.
    """
    target = path or (repo_root() / "config" / "cityflow.yml")
    raw: dict[str, Any] = {}
    if target.is_file():
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}

    override = os.environ.get("CITYFLOW_BACKEND")
    if override:
        if override not in ("tlc", "synthetic"):
            raise ValueError(f"CITYFLOW_BACKEND is {override!r}, expected 'tlc' or 'synthetic'.")
        raw["backend"] = override
    return CityflowConfig.model_validate(raw)
