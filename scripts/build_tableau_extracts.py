"""Write the three CSV extracts the Tableau companion reads.

Same marts as the custom dashboard, so the two reconcile by construction rather
than by coincidence. Every column is an additive component and none is a rate,
which matters more in Tableau than anywhere else: a published rate cannot be
re-aggregated, so a tip_rate column would be silently wrong the moment somebody
dragged Borough onto the view. tableau/README.md gives the calculated fields.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from cityflow_core import Paths

EXTRACTS: dict[str, str] = {
    "zone_month.csv": """
        select
            m.month, m.service, s.service_label, z.zone, z.borough,
            z.service_zone, z.is_airport, z.has_geometry,
            z.centroid_lon, z.centroid_lat,
            sum(m.trips) as trips,
            sum(m.total_amount_sum) as revenue,
            sum(m.fare_amount_sum) as fare_revenue,
            sum(m.trip_distance_sum) as distance_mi,
            sum(m.duration_s_sum) as duration_s,
            sum(m.airport_trips) as airport_trips,
            sum(m.tipped_trips) as tipped_trips,
            sum(m.tip_obs_trips) as tip_observable_trips,
            sum(m.tip_obs_tip_sum) as tip_observable_tip,
            sum(m.tip_obs_fare_sum) as tip_observable_fare,
            sum(m.unknown_zone_trips) as unknown_zone_trips
        from read_parquet('{d}/agg_zone_hour.parquet') m
        join read_parquet('{d}/dim_zone.parquet') z on z.zone_id = m.pu_zone_id
        join read_parquet('{d}/dim_service.parquet') s on s.service = m.service
        group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
        order by 1, 2, 4
    """,
    "daily.csv": """
        select
            d.date_day, d.service, s.service_label, d.day_type,
            d.is_holiday, d.holiday_name,
            d.trips,
            d.total_amount_sum as revenue,
            d.trip_distance_sum as distance_mi,
            d.duration_s_sum as duration_s,
            d.tipped_trips,
            d.tip_obs_trips as tip_observable_trips,
            d.tip_obs_tip_sum as tip_observable_tip,
            d.tip_obs_fare_sum as tip_observable_fare,
            x.trend, x.seasonal, x.resid
        from read_parquet('{d}/agg_daily.parquet') d
        join read_parquet('{d}/dim_service.parquet') s on s.service = d.service
        left join read_parquet('{d}/agg_daily_decomposition.parquet') x
            on x.service = d.service and x.date_day = d.date_day
        order by 1, 2
    """,
    "hour_of_week.csv": """
        select
            service, day_of_week, day_name, hour,
            sum(trips) as trips,
            sum(total_amount_sum) as revenue,
            sum(tipped_trips) as tipped_trips,
            sum(tip_obs_trips) as tip_observable_trips,
            sum(tip_obs_tip_sum) as tip_observable_tip,
            sum(tip_obs_fare_sum) as tip_observable_fare
        from read_parquet('{d}/agg_hour_of_week.parquet')
        group by 1, 2, 3, 4
        order by 1, 2, 4
    """,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="",
        help="Where to write the extracts. Defaults to tableau/ in the repository.",
    )
    args = parser.parse_args(argv)

    paths = Paths.resolve()
    shipped = paths.shipped
    if not (shipped / "agg_zone_hour.parquet").is_file():
        print(
            f"{shipped} has no shipped layer. Run 'make publish' first.",
            file=sys.stderr,
        )
        return 2

    out = Path(args.out) if args.out else paths.root / "tableau"
    out.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    for name, template in EXTRACTS.items():
        target = out / name
        connection.execute(
            f"copy ({template.format(d=shipped)}) to '{target}' (header, delimiter ',')"
        )
        rows = connection.execute(f"select count(*) from read_csv('{target}')").fetchone()
        count = int(rows[0]) if rows else 0
        print(f"{name:<24} {count:>8,} rows  {target.stat().st_size / 1e6:>6.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
