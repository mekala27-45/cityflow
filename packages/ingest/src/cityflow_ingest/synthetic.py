"""A seeded generator that reproduces the TLC files, defects included.

This exists for three reasons, in order of how much they matter.

1. Every quarantine rule needs a fixture that actually contains the defect it
   claims to catch, at a realistic rate, at a realistic scale. Asserting that a
   rule fires on a hand written three row table proves the SQL parses. Asserting
   that it catches 2,284 rows out of nineteen million, and that the eleven rules
   partition the rejected set without overlap, proves the rule.

2. Continuous integration cannot download eight gigabytes of parquet on every
   push, and a pipeline whose tests only run against data CI cannot fetch is a
   pipeline with no tests.

3. The public demo has to work on first click with no key and no setup.

What it reproduces is the *schema and its defects*, not the values. It writes
files carrying the exact column names of the vintage it is asked for, so the
per vintage mapping is exercised by the generator rather than bypassed by it,
and every defect rate it injects is published in docs/data.md next to the count
the pipeline caught.

What it does not reproduce, stated plainly so nobody assumes otherwise: real
trip volumes, real fares, real geography of demand beyond a borough level
gradient, or any finding about New York City. A number measured on generated
data is labelled as such everywhere it appears.
"""

from __future__ import annotations

import csv
import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import duckdb

from cityflow_core.config import CityflowConfig, Service, SourceSpec
from cityflow_core.paths import repo_root
from cityflow_ingest.vintage import Vintage

# Hour of day weights. Weekday and weekend are genuinely different shapes and
# a generator that uses one profile for both produces a hour of week heatmap
# with no diagonal structure, which is the one chart people look at.
WEEKDAY_HOURS: tuple[float, ...] = (
    0.38,
    0.24,
    0.16,
    0.12,
    0.11,
    0.18,
    0.42,
    0.78,
    1.06,
    1.00,
    0.86,
    0.88,
    0.95,
    0.96,
    1.02,
    1.12,
    1.24,
    1.48,
    1.52,
    1.34,
    1.14,
    0.98,
    0.80,
    0.56,
)
WEEKEND_HOURS: tuple[float, ...] = (
    1.18,
    1.04,
    0.86,
    0.62,
    0.40,
    0.26,
    0.22,
    0.28,
    0.40,
    0.58,
    0.76,
    0.92,
    1.02,
    1.06,
    1.08,
    1.10,
    1.12,
    1.18,
    1.20,
    1.16,
    1.10,
    1.08,
    1.12,
    1.20,
)

# Day of week multipliers, Monday first. Friday and Saturday carry the volume.
DOW_WEIGHT: tuple[float, ...] = (0.88, 0.92, 0.97, 1.04, 1.18, 1.16, 0.85)

# Average speed by hour, miles per hour. Manhattan traffic is the reason the
# midday figure is lower than the small hours figure by a factor of two, and it
# is what makes the duration ridgeline by hour worth drawing.
SPEED_MPH: tuple[float, ...] = (
    19.5,
    20.8,
    21.6,
    22.0,
    22.2,
    21.0,
    17.4,
    13.2,
    10.6,
    10.1,
    10.4,
    10.8,
    10.9,
    10.7,
    10.2,
    9.6,
    9.1,
    8.8,
    9.4,
    10.8,
    12.6,
    14.8,
    16.9,
    18.4,
)


# The holiday calendar is the dbt seed, read rather than restated. Two copies
# of a holiday list is one copy too many: the generator would dip volume on a
# day the warehouse did not think was a holiday, and the resulting bump in the
# STL remainder would look like a finding.
@cache
def holidays(seed_path: Path | None = None) -> frozenset[dt.date]:
    path = seed_path or (repo_root() / "transform" / "seeds" / "holiday.csv")
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} is missing. The generator and dim_date share one holiday "
            "list and neither has a fallback, because a fallback is how they "
            "come to disagree."
        )
    with path.open(encoding="utf-8", newline="") as handle:
        return frozenset(
            dt.date.fromisoformat(row["holiday_date"]) for row in csv.DictReader(handle)
        )


# One planted level shift, so the changepoint detector has something real to
# find and the annotation on the chart can be checked against a known date.
# Published in docs/data.md as planted, because a reader who finds it and
# assumes it is a real event has been misled by us, not by the data.
PLANTED_CHANGEPOINT = dt.date(2024, 8, 12)
PLANTED_SHIFT: dict[str, float] = {"yellow": 0.97, "green": 0.99, "fhvhv": 0.88}

# Monthly seasonal factors, January first.
MONTH_FACTOR: tuple[float, ...] = (
    0.88,
    0.90,
    1.02,
    1.03,
    1.06,
    1.02,
    0.94,
    0.90,
    1.04,
    1.10,
    1.03,
    1.08,
)

# Share of trips whose destination is drawn from the origin's own borough.
LOCAL_TRIP_SHARE: dict[Service, float] = {"yellow": 0.62, "green": 0.71, "fhvhv": 0.58}

# Trip distance, lognormal. Median is exp(mu) miles.
DISTANCE_LOGNORMAL: dict[Service, tuple[float, float]] = {
    "yellow": (0.62, 0.74),  # median 1.86 miles
    "green": (0.83, 0.78),  # median 2.29 miles
    "fhvhv": (1.16, 0.82),  # median 3.19 miles
}

# Weight of a zone group in a service's pickup distribution. Yellow cabs live
# in Manhattan, green cabs were licensed precisely so they would not, and the
# for hire fleet is everywhere.
SERVICE_ZONE_WEIGHT: dict[Service, dict[str, float]] = {
    "yellow": {
        "Yellow Zone": 1.0,
        "Airports": 0.30,
        "Boro Zone": 0.022,
        "EWR": 0.0015,
        "N/A": 0.010,
    },
    "green": {
        "Yellow Zone": 0.09,
        "Boro Zone": 1.0,
        "Airports": 0.06,
        "EWR": 0.0004,
        "N/A": 0.008,
    },
    "fhvhv": {
        "Yellow Zone": 0.52,
        "Boro Zone": 1.0,
        "Airports": 0.45,
        "EWR": 0.003,
        "N/A": 0.013,
    },
}

# Within a group, weight falls off as 1 / rank^ZIPF. Without this every zone in
# a borough gets the same volume, the Pareto panel is a straight line, and the
# concentration question the panel asks has no answer.
ZIPF_EXPONENT = 0.85

# Which zones get the top ranks. A hash would be defensible and reproducible,
# and it produced a map on which Governor's Island was the fourth busiest place
# in Manhattan. Anyone who has stood on a corner in this city would see that in
# a second, and once they see one thing that is obviously wrong they stop
# trusting the rest of the chart. So the busy zones are named. The volumes are
# still generated, the ordering is judgement, and docs/data.md says so.
PRIORITY_ZONES: dict[str, tuple[str, ...]] = {
    "Yellow Zone": (
        "Midtown Center",
        "Upper East Side South",
        "Upper East Side North",
        "Penn Station/Madison Sq West",
        "Times Sq/Theatre District",
        "Midtown East",
        "Lincoln Square East",
        "Murray Hill",
        "Union Sq",
        "Clinton East",
        "East Village",
        "Lenox Hill West",
        "Garment District",
        "Gramercy",
        "Upper West Side South",
        "Midtown North",
        "West Village",
        "East Chelsea",
        "Yorkville West",
        "Sutton Place/Turtle Bay North",
        "Clinton West",
        "Lincoln Square West",
        "Upper West Side North",
        "Flatiron",
        "Little Italy/NoLiTa",
    ),
    "Boro Zone": (
        "East Williamsburg",
        "Williamsburg (North Side)",
        "Astoria",
        "Long Island City/Queens Plaza",
        "Downtown Brooklyn/MetroTech",
        "Central Harlem",
        "Park Slope",
        "Jackson Heights",
        "Bedford",
        "Fort Greene",
        "Crown Heights North",
        "Bushwick South",
        "Sunnyside",
        "Greenpoint",
        "Elmhurst",
        "Flushing",
        "Central Harlem North",
        "Mott Haven/Port Morris",
        "Forest Hills",
        "Prospect Heights",
        "Boerum Hill",
        "Carroll Gardens",
        "Jamaica",
        "Ridgewood",
        "East Harlem South",
    ),
}

# Places with a zone polygon and almost no trips. Left in the data, because
# they are in the real lookup and a zone with a handful of trips is exactly
# what the Wilson intervals on the zone ranking are there to handle, but not
# allowed to rank near the top by an accident of hashing.
LOW_TRAFFIC_ZONES: frozenset[str] = frozenset(
    {
        "Governor's Island/Ellis Island/Liberty Island",
        "Jamaica Bay",
        "Rikers Island",
        "Freshkills Park",
        "Green-Wood Cemetery",
        "Great Kills Park",
        "Crotona Park",
        "Van Cortlandt Park",
        "Pelham Bay Park",
        "Forest Park",
        "Marine Park/Floyd Bennett Field",
        "Breezy Point/Fort Tilden/Riis Beach",
        "Broad Channel",
        "Highbridge Park",
        "Inwood Hill Park",
        "Randalls Island",
        "Central Park",
        "Prospect Park",
        "Bronx Park",
    }
)
LOW_TRAFFIC_MULTIPLIER = 0.04

AIRPORT_ZONE_IDS: tuple[int, ...] = (1, 132, 138)
UNKNOWN_ZONE_IDS: tuple[int, ...] = (264, 265)


@dataclass(frozen=True, slots=True)
class ZoneRow:
    """The columns of dim_zone the generator needs."""

    zone_id: int
    name: str
    borough: str
    service_zone: str
    is_unknown: bool

    @property
    def is_airport(self) -> bool:
        return self.zone_id in AIRPORT_ZONE_IDS


def load_zones(reference_csv: Path) -> list[ZoneRow]:
    with reference_csv.open(encoding="utf-8", newline="") as handle:
        return [
            ZoneRow(
                zone_id=int(row["zone_id"]),
                name=row["zone"],
                borough=row["borough"],
                service_zone=row["service_zone"],
                is_unknown=row["is_unknown"] == "1",
            )
            for row in csv.DictReader(handle)
        ]


def _zone_weights(zones: Sequence[ZoneRow], service: Service) -> list[tuple[ZoneRow, float]]:
    """Weight every zone for one service, with a Zipf fall off inside each group."""
    group_weight = SERVICE_ZONE_WEIGHT[service]
    ranked: dict[str, int] = {}
    out: list[tuple[ZoneRow, float]] = []

    def rank_key(zone: ZoneRow) -> tuple[int, int]:
        """Named zones first, in the order named, then everything else.

        The tail is ordered by a fixed hash rather than by zone id, because the
        TLC numbered its zones alphabetically and ranking by id turns the
        choropleth into a gradient running from Alphabet City to Yorkville:
        an artefact of the numbering that looks exactly like one.
        """
        priority = PRIORITY_ZONES.get(zone.service_zone, ())
        if zone.name in priority:
            return (0, priority.index(zone.name))
        return (1, (zone.zone_id * 2654435761) % 4294967296)

    for zone in sorted(zones, key=rank_key):
        base = group_weight.get(zone.service_zone, 0.01)
        rank = ranked.get(zone.service_zone, 0) + 1
        ranked[zone.service_zone] = rank
        weight = base / rank**ZIPF_EXPONENT
        if zone.name in LOW_TRAFFIC_ZONES:
            weight *= LOW_TRAFFIC_MULTIPLIER
        out.append((zone, weight))
    return out


def _cumulative(
    rows: Sequence[tuple[ZoneRow, float]],
) -> list[tuple[ZoneRow, float, float]]:
    total = sum(weight for _, weight in rows)
    running = 0.0
    out: list[tuple[ZoneRow, float, float]] = []
    for zone, weight in rows:
        low = running / total
        running += weight
        out.append((zone, low, running / total))
    return out


def install_zone_tables(
    connection: duckdb.DuckDBPyConnection,
    zones: Sequence[ZoneRow],
    service: Service,
) -> None:
    """Two cumulative lookup tables per service: citywide, and per borough.

    Sampling by inverse CDF through an ASOF JOIN keeps the whole draw inside
    DuckDB. The alternative, sampling in Python and joining, moves nineteen
    million rows across a process boundary for no reason.
    """
    weighted = _cumulative(_zone_weights(zones, service))
    connection.execute(f"drop table if exists gen_zone_{service}")
    connection.execute(
        f"""create table gen_zone_{service} (
            zone_id smallint, borough varchar, service_zone varchar,
            is_unknown boolean, is_airport boolean, is_yellow_zone boolean,
            lo double, hi double
        )"""
    )
    connection.executemany(
        f"insert into gen_zone_{service} values (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                z.zone_id,
                z.borough,
                z.service_zone,
                z.is_unknown,
                z.is_airport,
                z.service_zone == "Yellow Zone",
                lo,
                hi,
            )
            for z, lo, hi in weighted
        ],
    )

    per_borough: list[tuple[int, str, float, float]] = []
    for borough in sorted({z.borough for z in zones}):
        subset = [(z, w) for z, w in _zone_weights(zones, service) if z.borough == borough]
        for zone, lo, hi in _cumulative(subset):
            per_borough.append((zone.zone_id, borough, lo, hi))
    connection.execute(f"drop table if exists gen_zone_boro_{service}")
    connection.execute(
        f"""create table gen_zone_boro_{service} (
            zone_id smallint, borough varchar, lo double, hi double
        )"""
    )
    connection.executemany(f"insert into gen_zone_boro_{service} values (?, ?, ?, ?)", per_borough)


def _daily_weight(day: dt.date, service: Service, start: dt.date) -> float:
    weight = DOW_WEIGHT[day.weekday()] * MONTH_FACTOR[day.month - 1]
    if day in holidays():
        weight *= 0.62
    # A slow secular drift, so the STL trend component is not flat.
    weight *= 1.0 + 0.00035 * (day - start).days
    if day >= PLANTED_CHANGEPOINT:
        weight *= PLANTED_SHIFT[service]
    return weight


def install_slot_table(
    connection: duckdb.DuckDBPyConnection,
    source: SourceSpec,
    config: CityflowConfig,
) -> None:
    """Cumulative weights over every (day, hour) slot in the month."""
    first = source.period
    last = dt.date(first.year + first.month // 12, first.month % 12 + 1, 1) - dt.timedelta(days=1)
    rows: list[tuple[dt.date, int, float]] = []
    day = first
    while day <= last:
        profile = WEEKEND_HOURS if day.weekday() >= 5 else WEEKDAY_HOURS
        daily = _daily_weight(day, source.service, config.start)
        for hour in range(24):
            rows.append((day, hour, daily * profile[hour]))
        day += dt.timedelta(days=1)

    total = sum(weight for _, _, weight in rows)
    running = 0.0
    cumulative: list[tuple[dt.date, int, float, float]] = []
    for day_value, hour, weight in rows:
        low = running / total
        running += weight
        cumulative.append((day_value, hour, low, running / total))

    connection.execute("drop table if exists gen_slot")
    connection.execute("create table gen_slot (day_date date, hour integer, lo double, hi double)")
    connection.executemany("insert into gen_slot values (?, ?, ?, ?)", cumulative)


def _speed_case() -> str:
    branches = " ".join(f"when {hour} then {speed}" for hour, speed in enumerate(SPEED_MPH))
    return f"case hour {branches} else 12.0 end"


def _defect_bands(config: CityflowConfig) -> dict[str, tuple[float, float]]:
    """Disjoint intervals of a single uniform draw, one per defect.

    One draw and disjoint bands rather than one draw per defect, so a row
    carries at most one injected defect and the realised rate of each is the
    configured rate rather than the configured rate conditioned on the others
    not having fired.
    """
    bands: dict[str, tuple[float, float]] = {}
    cursor = 0.0
    for name, rate in config.synthetic.defect_rates.items():
        bands[name] = (cursor, cursor + rate)
        cursor += rate
    if cursor >= 1.0:
        raise ValueError("Defect rates sum to 1.0 or more, leaving no clean rows.")
    return bands


def generate_month(
    connection: duckdb.DuckDBPyConnection,
    config: CityflowConfig,
    source: SourceSpec,
    vintage: Vintage,
    out_path: Path,
    *,
    scale: float = 1.0,
) -> int:
    """Write one month of one service as parquet in that vintage's own schema.

    Returns the row count written, duplicates included.
    """
    service = source.service
    target = int(config.synthetic.trips_per_month[service] * scale)
    if target <= 0:
        raise ValueError(f"Nothing to generate for {source}: target row count is {target}")

    install_slot_table(connection, source, config)
    seed = (
        config.synthetic.seed
        + source.year * 100
        + source.month
        + {"yellow": 1, "green": 2, "fhvhv": 3}[service]
    )
    connection.execute(f"select setseed({(seed % 100000) / 100000.0:.6f})")

    bands = _defect_bands(config)
    mu, sigma = DISTANCE_LOGNORMAL[service]
    local_share = LOCAL_TRIP_SHARE[service]
    duplicate_rate = config.synthetic.defect_rates["exact_duplicate"]

    sql = _build_generation_sql(
        service=service,
        vintage=vintage,
        target=target,
        mu=mu,
        sigma=sigma,
        local_share=local_share,
        bands=bands,
        duplicate_rate=duplicate_rate,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    connection.execute(
        f"copy ({sql}) to '{out_path}' (format parquet, compression zstd, row_group_size 122880)"
    )
    count = connection.execute(f"select count(*) from read_parquet('{out_path}')").fetchone()
    return int(count[0]) if count else 0


def _build_generation_sql(
    *,
    service: Service,
    vintage: Vintage,
    target: int,
    mu: float,
    sigma: float,
    local_share: float,
    bands: dict[str, tuple[float, float]],
    duplicate_rate: float,
) -> str:
    """Assemble the generation query for one service and vintage."""
    speed = _speed_case()

    def band(name: str) -> str:
        low, high = bands[name]
        return f"(r_defect >= {low:.8f} and r_defect < {high:.8f})"

    # Stage one: draw a time slot and a pickup zone.
    picked = f"""
    select
        u.i,
        s.day_date, s.hour,
        zp.zone_id as pu_zone_id, zp.borough as pu_borough,
        zp.is_airport as pu_is_airport, zp.is_yellow_zone as pu_is_yellow,
        zp.is_unknown as pu_is_unknown,
        u.r_do, u.r_dob, u.r_local, u.r_dist1, u.r_dist2, u.r_spd, u.r_sec,
        u.r_pay, u.r_tip1, u.r_tip2, u.r_pax, u.r_defect, u.r_defect2, u.r_dup,
        u.r_extra
    from (
        select
            i,
            random() as r_slot, random() as r_pu, random() as r_do,
            random() as r_dob, random() as r_local, random() as r_dist1,
            random() as r_dist2, random() as r_spd, random() as r_sec,
            random() as r_pay, random() as r_tip1, random() as r_tip2,
            random() as r_pax, random() as r_defect, random() as r_defect2,
            random() as r_dup, random() as r_extra
        from range({target}) t(i)
    ) u
    asof join gen_slot s on u.r_slot >= s.lo
    asof join gen_zone_{service} zp on u.r_pu >= zp.lo
    """

    # Stage two: draw a destination, locally most of the time.
    dropped = f"""
    select
        p.*,
        case when p.r_local < {local_share} then zl.zone_id else zg.zone_id end
            as do_zone_id,
        case when p.r_local < {local_share} then false else zg.is_airport end
            as do_is_airport,
        case when p.r_local < {local_share} then false else zg.is_yellow_zone end
            as do_is_yellow
    from picked p
    asof join gen_zone_{service} zg on p.r_do >= zg.lo
    asof join gen_zone_boro_{service} zl
        on zl.borough = p.pu_borough and p.r_dob >= zl.lo
    """

    # Stage three: the physics and the money.
    shaped = f"""
    select
        d.*,
        d.day_date + to_hours(d.hour) + to_seconds(cast(d.r_sec * 3600 as integer))
            as pickup_clean,
        greatest(
            0.12,
            exp({mu} + {sigma} * sqrt(-2.0 * ln(greatest(d.r_dist1, 1e-12)))
                * cos(2.0 * pi() * d.r_dist2))
        ) as miles_clean,
        {speed} as speed_mph
    from dropped d
    """

    timed = """
    select
        s.*,
        greatest(60, cast(
            s.miles_clean / s.speed_mph * 3600.0 * (0.78 + 0.44 * s.r_spd) as bigint
        )) as duration_clean
    from shaped s
    """

    # Flat rate trips: JFK to or from Manhattan, and anything touching Newark.
    ratecode = """
    case
        when (pu_zone_id = 132 and do_is_yellow) or (do_zone_id = 132 and pu_is_yellow)
            then 2
        when pu_zone_id = 1 or do_zone_id = 1 then 3
        when r_extra > 0.997 then 5
        else 1
    end
    """

    if service == "fhvhv":
        money = """
        select
            t.*,
            cast(null as integer) as ratecode_clean,
            'app' as payment_clean,
            round(4.60 + 1.92 * t.miles_clean + 0.54 * (t.duration_clean / 60.0), 2)
                as fare_clean
        from timed t
        """
        # Three quarters of app trips carry no tip at all. That is a real zero,
        # not a missing value, and it is why the tip story differs by service.
        tip = """
        case when r_tip1 < 0.74 then 0.0
             else round(fare_clean * (0.08 + 0.22 * r_tip2), 2) end
        """
    else:
        money = f"""
        select
            t.*,
            {ratecode} as ratecode_clean,
            case
                when t.r_pay < 0.695 then 1
                when t.r_pay < 0.962 then 2
                when t.r_pay < 0.984 then 3
                when t.r_pay < 0.996 then 4
                else 5
            end as payment_clean,
            case
                when {ratecode} = 2 then 70.00
                when {ratecode} = 3
                    then round(3.00 + 3.50 * t.miles_clean
                               + 0.70 * (t.duration_clean / 60.0) + 20.00, 2)
                else round(greatest(3.00, 3.00 + 3.50 * t.miles_clean
                           + 0.70 * (t.duration_clean / 60.0)), 2)
            end as fare_clean
        from timed t
        """
        # Card trips tip. Cash trips record a zero that means nothing, which is
        # the defect this whole project is built to handle correctly.
        tip = """
        case when payment_clean <> 1 then 0.0
             when r_tip1 < 0.075 then 0.0
             else round(fare_clean * (0.12 + 0.20 * r_tip2), 2) end
        """

    congestion = (
        "case when pu_is_yellow or do_is_yellow then 2.75 else 0.0 end"
        if service == "fhvhv"
        else "case when pu_is_yellow or do_is_yellow then 2.50 else 0.0 end"
    )
    airport_fee = (
        "case when pu_is_airport then 2.50 else 0.0 end"
        if service == "fhvhv"
        else "case when pu_zone_id in (132, 138) then 1.75 else 0.0 end"
    )

    priced = f"""
    select
        m.*,
        {tip} as tip_clean,
        case when pu_is_airport or do_is_airport
             then round(6.94 * (0.4 + r_extra), 2) else 0.0 end as tolls_clean,
        {congestion} as congestion_clean,
        {airport_fee} as airport_fee_clean,
        case
            when hour >= 20 or hour < 6 then 0.50
            when hour between 16 and 19 and dayofweek(day_date) between 1 and 5 then 1.00
            else 0.0
        end as extra_clean,
        least(6, greatest(1, cast(1 + 5 * pow(r_pax, 3.1) as integer))) as pax_clean
    from money m
    """

    # Stage four: inject the defects. Every branch here corresponds to a named
    # quarantine rule, and the test asserts the caught count matches the
    # injected count for each one.
    injected = f"""
    select
        case
            when {band("timestamp_out_of_period")}
                then pickup_clean - to_days(cast(400 + 900 * r_defect2 as integer))
            else pickup_clean
        end as pickup_ts,
        case
            when {band("dropoff_before_pickup")}
                then pickup_clean - to_seconds(cast(60 + 600 * r_defect2 as integer))
            when {band("implausible_speed")}
                then pickup_clean + to_seconds(cast(20 + 40 * r_defect2 as integer))
            when {band("timestamp_out_of_period")}
                then pickup_clean - to_days(cast(400 + 900 * r_defect2 as integer))
                     + to_seconds(duration_clean)
            else pickup_clean + to_seconds(duration_clean)
        end as dropoff_ts,
        case
            when {band("non_positive_distance")} then 0.0
            when {band("implausible_speed")} then round(miles_clean + 14.0, 2)
            else round(miles_clean, 2)
        end as miles,
        case
            when {band("non_positive_fare")} then round(-1.0 * fare_clean, 2)
            else fare_clean
        end as fare,
        case
            when {band("passenger_count_zero")} then 0
            else pax_clean
        end as pax,
        case
            when {band("unknown_zone")}
                then (case when r_defect2 < 0.82 then 264 else 265 end)
            else pu_zone_id
        end as pu_zone,
        case
            when {band("unknown_zone")} and r_defect2 >= 0.5 then 265
            else do_zone_id
        end as do_zone,
        tip_clean, tolls_clean, congestion_clean, airport_fee_clean, extra_clean,
        ratecode_clean, payment_clean, r_dup, r_extra, hour
    from priced
    """

    projected = _project_to_vintage(service, vintage)

    # r_dup_keep rides along only so the duplicate arm can select against it,
    # and is excluded from the written file: a column that exists purely to
    # make the generator work has no business being in something that claims to
    # be a copy of a published file.
    return f"""
    select * exclude (r_dup_keep) from (
        with picked as ({picked}),
             dropped as ({dropped}),
             shaped as ({shaped}),
             timed as ({timed}),
             money as ({money}),
             priced as ({priced}),
             injected as ({injected}),
             final as ({projected})
        select * from final
        union all
        select * from final where r_dup_keep < {duplicate_rate}
    )
    """


def _project_to_vintage(service: Service, vintage: Vintage) -> str:
    """Emit the exact column names and order the vintage's files carry."""
    if service == "fhvhv":
        licence = (
            "case when r_extra < 0.66 then 'HV0003' "
            "when r_extra < 0.96 then 'HV0005' else 'HV0004' end"
        )
        columns = [
            f"{licence} as hvfhs_license_num",
            "'B03404' as dispatching_base_num",
            "'B03404' as originating_base_num",
            "pickup_ts - to_seconds(240) as request_datetime",
            "pickup_ts - to_seconds(90) as on_scene_datetime",
            f"pickup_ts as {vintage.pickup_column}",
            f"dropoff_ts as {vintage.dropoff_column}",
            "cast(pu_zone as integer) as PULocationID",
            "cast(do_zone as integer) as DOLocationID",
            f"miles as {vintage.distance_column}",
            "cast(date_diff('second', pickup_ts, dropoff_ts) as bigint) as trip_time",
            f"fare as {vintage.fare_column}",
            "tolls_clean as tolls",
            "round(fare * 0.03, 2) as bcf",
            "round(fare * 0.08875, 2) as sales_tax",
        ]
        if vintage.has_congestion_surcharge:
            columns.append("congestion_clean as congestion_surcharge")
        if vintage.has_airport_fee:
            columns.append("airport_fee_clean as airport_fee")
        if vintage.has_cbd_congestion_fee:
            columns.append("0.0 as cbd_congestion_fee")
        columns += [
            f"tip_clean as {vintage.tip_column}",
            "round(fare * 0.72, 2) as driver_pay",
            "'N' as shared_request_flag",
            "'N' as shared_match_flag",
            "'N' as access_a_ride_flag",
            "'N' as wav_request_flag",
            "'N' as wav_match_flag",
            "r_dup as r_dup_keep",
        ]
        return "select " + ", ".join(columns) + " from injected"

    columns = [
        "cast(case when r_extra < 0.52 then 1 when r_extra < 0.99 then 2 "
        "when r_extra < 0.995 then 6 else 7 end as integer) as VendorID",
        f"pickup_ts as {vintage.pickup_column}",
        f"dropoff_ts as {vintage.dropoff_column}",
        "cast(pax as integer) as passenger_count",
        f"miles as {vintage.distance_column}",
        "cast(ratecode_clean as integer) as RatecodeID",
        "'N' as store_and_fwd_flag",
        "cast(pu_zone as integer) as PULocationID",
        "cast(do_zone as integer) as DOLocationID",
        "cast(payment_clean as integer) as payment_type",
        f"fare as {vintage.fare_column}",
        "extra_clean as extra",
        "0.50 as mta_tax",
        f"tip_clean as {vintage.tip_column}",
        "tolls_clean as tolls",
    ]
    # Green files name the tolls column the same way yellow does, so the alias
    # above is corrected here rather than being special cased upstream.
    columns[-1] = f"tolls_clean as {vintage.tolls_column}"
    columns.append("1.00 as improvement_surcharge")
    if service == "green":
        columns += ["cast(null as double) as ehail_fee", "1 as trip_type"]
    if vintage.has_congestion_surcharge:
        columns.append("congestion_clean as congestion_surcharge")
    if vintage.has_airport_fee:
        columns.append("airport_fee_clean as airport_fee")
    if vintage.has_cbd_congestion_fee:
        columns.append("0.0 as cbd_congestion_fee")
    columns.append(
        "round(fare + extra_clean + 0.50 + tip_clean + tolls_clean + 1.00 "
        + ("+ congestion_clean " if vintage.has_congestion_surcharge else "")
        + ("+ airport_fee_clean " if vintage.has_airport_fee else "")
        + ", 2) as total_amount"
    )
    columns.append("r_dup as r_dup_keep")
    return "select " + ", ".join(columns) + " from injected"


def expected_row_count(config: CityflowConfig, source: SourceSpec, scale: float) -> int:
    """What the generator will write, duplicates included, for a size estimate."""
    base = int(config.synthetic.trips_per_month[source.service] * scale)
    return base + int(base * config.synthetic.defect_rates["exact_duplicate"])


def total_target(config: CityflowConfig, scale: float = 1.0) -> int:
    return sum(expected_row_count(config, source, scale) for source in config.sources())


def sanity_check_profiles() -> None:
    """Fail at import time if a profile table is the wrong length.

    Cheap, and it catches the class of bug where an hour is dropped from a
    24 element tuple and every later hour silently shifts by one.
    """
    for name, table in (
        ("WEEKDAY_HOURS", WEEKDAY_HOURS),
        ("WEEKEND_HOURS", WEEKEND_HOURS),
        ("SPEED_MPH", SPEED_MPH),
    ):
        if len(table) != 24:
            raise ValueError(f"{name} has {len(table)} entries, expected 24")
    if len(DOW_WEIGHT) != 7:
        raise ValueError("DOW_WEIGHT must have 7 entries")
    if len(MONTH_FACTOR) != 12:
        raise ValueError("MONTH_FACTOR must have 12 entries")
    if not math.isclose(sum(SERVICE_ZONE_WEIGHT["yellow"].values()), 1.4535, abs_tol=1.0):
        raise ValueError("Yellow zone weights look wrong")


sanity_check_profiles()
