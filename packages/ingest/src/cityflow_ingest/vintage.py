"""The per-vintage column mapping, written out rather than inferred.

The TLC has changed the trip record schema eleven times since 2009. Column
names change case, timestamps change prefix, the location columns stop being
coordinates and start being zone ids, and four surcharge columns appear in the
middle of the history. Inferring the mapping from whatever columns happen to be
in a file works until the day two vintages share a column name with different
semantics, and then it is wrong quietly.

So the mapping is a table. Each vintage states its own date range, its own
column names, and which optional columns exist in it. A file that does not
match its vintage's expected columns fails the ingest with the difference
printed, rather than producing nulls nobody notices.

The dates below are the month a column first appears in a published file, not
the month the rule took effect. Those differ, and the file is what we read.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from cityflow_core.config import Service

# The HVFHS licence numbers, which are the only place the operator is recorded.
HVFHS_OPERATORS: dict[str, str] = {
    "HV0002": "Juno",
    "HV0003": "Uber",
    "HV0004": "Via",
    "HV0005": "Lyft",
}

# Yellow and green record a payment type code. The code is not a tip flag, but
# it decides whether a recorded tip of zero means anything, which is the single
# most consequential lookup in this repository. See docs/data.md.
PAYMENT_TYPES: dict[int, str] = {
    0: "flex_fare",
    1: "card",
    2: "cash",
    3: "no_charge",
    4: "dispute",
    5: "unknown",
    6: "voided",
}

# A tip is observable when the payment channel records it. Card and in app
# payments do. Cash does not: the meter records zero because no tip passed
# through it, and that zero is a missing value. Averaging over both is how most
# published NYC tip rates end up wrong by a third.
TIP_OBSERVABLE_PAYMENT_TYPES: frozenset[str] = frozenset({"card", "app"})


@dataclass(frozen=True, slots=True)
class Vintage:
    """One era of one service's schema."""

    name: str
    service: Service
    first: dt.date
    last: dt.date | None  # None means current

    pickup_column: str
    dropoff_column: str
    pu_zone_column: str | None  # None in the coordinate era
    do_zone_column: str | None
    operator_column: str

    distance_column: str
    fare_column: str
    tip_column: str
    tolls_column: str

    has_passenger_count: bool = True
    has_ratecode: bool = True
    has_payment_type: bool = True
    has_extra: bool = True
    has_mta_tax: bool = True
    has_improvement_surcharge: bool = True
    has_congestion_surcharge: bool = False
    has_airport_fee: bool = False
    has_cbd_congestion_fee: bool = False
    has_total_amount: bool = True

    # The for hire vehicle files publish no total. They publish the components,
    # so the total is reconstructed from them rather than left null, and the
    # reconstruction is stated in docs/data.md so nobody compares it to the
    # yellow total without knowing the difference.
    has_black_car_fund: bool = False
    has_sales_tax: bool = False

    # Columns the file carries that this project does not model. Listed so the
    # schema check can distinguish "column we ignore" from "column we did not
    # expect", which are very different problems.
    ignored: tuple[str, ...] = field(default_factory=tuple)

    def covers(self, period: dt.date) -> bool:
        if period < self.first:
            return False
        return self.last is None or period <= self.last

    @property
    def is_coordinate_era(self) -> bool:
        """Before zone ids, trips carried raw pickup and dropoff coordinates.

        These files cannot be joined to a taxi zone without a spatial join
        against the shapefile. That is a different project. The registry
        refuses them with a clear message instead of half supporting them.
        """
        return self.pu_zone_column is None


YELLOW_IGNORED = ("store_and_fwd_flag",)
GREEN_IGNORED = ("store_and_fwd_flag", "ehail_fee", "trip_type")
FHVHV_IGNORED = (
    "dispatching_base_num",
    "originating_base_num",
    "request_datetime",
    "on_scene_datetime",
    "trip_time",
    "driver_pay",
    "shared_request_flag",
    "shared_match_flag",
    "access_a_ride_flag",
    "wav_request_flag",
    "wav_match_flag",
)

VINTAGES: tuple[Vintage, ...] = (
    # Yellow ----------------------------------------------------------------
    Vintage(
        name="yellow_v1_coordinates",
        service="yellow",
        first=dt.date(2009, 1, 1),
        last=dt.date(2016, 6, 1),
        pickup_column="tpep_pickup_datetime",
        dropoff_column="tpep_dropoff_datetime",
        pu_zone_column=None,
        do_zone_column=None,
        operator_column="VendorID",
        distance_column="trip_distance",
        fare_column="fare_amount",
        tip_column="tip_amount",
        tolls_column="tolls_amount",
        ignored=YELLOW_IGNORED,
    ),
    Vintage(
        name="yellow_v2_zones",
        service="yellow",
        first=dt.date(2016, 7, 1),
        last=dt.date(2019, 1, 1),
        pickup_column="tpep_pickup_datetime",
        dropoff_column="tpep_dropoff_datetime",
        pu_zone_column="PULocationID",
        do_zone_column="DOLocationID",
        operator_column="VendorID",
        distance_column="trip_distance",
        fare_column="fare_amount",
        tip_column="tip_amount",
        tolls_column="tolls_amount",
        ignored=YELLOW_IGNORED,
    ),
    Vintage(
        name="yellow_v3_congestion",
        service="yellow",
        first=dt.date(2019, 2, 1),
        last=dt.date(2021, 12, 1),
        pickup_column="tpep_pickup_datetime",
        dropoff_column="tpep_dropoff_datetime",
        pu_zone_column="PULocationID",
        do_zone_column="DOLocationID",
        operator_column="VendorID",
        distance_column="trip_distance",
        fare_column="fare_amount",
        tip_column="tip_amount",
        tolls_column="tolls_amount",
        has_congestion_surcharge=True,
        ignored=YELLOW_IGNORED,
    ),
    Vintage(
        name="yellow_v4_airport_fee",
        service="yellow",
        first=dt.date(2022, 1, 1),
        last=dt.date(2024, 12, 1),
        pickup_column="tpep_pickup_datetime",
        dropoff_column="tpep_dropoff_datetime",
        pu_zone_column="PULocationID",
        do_zone_column="DOLocationID",
        operator_column="VendorID",
        distance_column="trip_distance",
        fare_column="fare_amount",
        tip_column="tip_amount",
        tolls_column="tolls_amount",
        has_congestion_surcharge=True,
        has_airport_fee=True,
        ignored=YELLOW_IGNORED,
    ),
    Vintage(
        name="yellow_v5_cbd",
        service="yellow",
        first=dt.date(2025, 1, 1),
        last=None,
        pickup_column="tpep_pickup_datetime",
        dropoff_column="tpep_dropoff_datetime",
        pu_zone_column="PULocationID",
        do_zone_column="DOLocationID",
        operator_column="VendorID",
        distance_column="trip_distance",
        fare_column="fare_amount",
        tip_column="tip_amount",
        tolls_column="tolls_amount",
        has_congestion_surcharge=True,
        has_airport_fee=True,
        has_cbd_congestion_fee=True,
        ignored=YELLOW_IGNORED,
    ),
    # Green -----------------------------------------------------------------
    Vintage(
        name="green_v1_coordinates",
        service="green",
        first=dt.date(2013, 8, 1),
        last=dt.date(2016, 6, 1),
        pickup_column="lpep_pickup_datetime",
        dropoff_column="lpep_dropoff_datetime",
        pu_zone_column=None,
        do_zone_column=None,
        operator_column="VendorID",
        distance_column="trip_distance",
        fare_column="fare_amount",
        tip_column="tip_amount",
        tolls_column="tolls_amount",
        ignored=GREEN_IGNORED,
    ),
    Vintage(
        name="green_v2_zones",
        service="green",
        first=dt.date(2016, 7, 1),
        last=dt.date(2019, 1, 1),
        pickup_column="lpep_pickup_datetime",
        dropoff_column="lpep_dropoff_datetime",
        pu_zone_column="PULocationID",
        do_zone_column="DOLocationID",
        operator_column="VendorID",
        distance_column="trip_distance",
        fare_column="fare_amount",
        tip_column="tip_amount",
        tolls_column="tolls_amount",
        ignored=GREEN_IGNORED,
    ),
    Vintage(
        name="green_v3_congestion",
        service="green",
        first=dt.date(2019, 2, 1),
        last=dt.date(2024, 12, 1),
        pickup_column="lpep_pickup_datetime",
        dropoff_column="lpep_dropoff_datetime",
        pu_zone_column="PULocationID",
        do_zone_column="DOLocationID",
        operator_column="VendorID",
        distance_column="trip_distance",
        fare_column="fare_amount",
        tip_column="tip_amount",
        tolls_column="tolls_amount",
        has_congestion_surcharge=True,
        ignored=GREEN_IGNORED,
    ),
    Vintage(
        name="green_v4_cbd",
        service="green",
        first=dt.date(2025, 1, 1),
        last=None,
        pickup_column="lpep_pickup_datetime",
        dropoff_column="lpep_dropoff_datetime",
        pu_zone_column="PULocationID",
        do_zone_column="DOLocationID",
        operator_column="VendorID",
        distance_column="trip_distance",
        fare_column="fare_amount",
        tip_column="tip_amount",
        tolls_column="tolls_amount",
        has_congestion_surcharge=True,
        has_cbd_congestion_fee=True,
        ignored=GREEN_IGNORED,
    ),
    # High volume for hire vehicles -----------------------------------------
    # A different shape entirely: no passenger count, no rate code, no payment
    # type, distance and fare under different names, and the operator carried
    # as a licence number that has to be looked up.
    Vintage(
        name="fhvhv_v1",
        service="fhvhv",
        first=dt.date(2019, 2, 1),
        last=dt.date(2021, 12, 1),
        pickup_column="pickup_datetime",
        dropoff_column="dropoff_datetime",
        pu_zone_column="PULocationID",
        do_zone_column="DOLocationID",
        operator_column="hvfhs_license_num",
        distance_column="trip_miles",
        fare_column="base_passenger_fare",
        tip_column="tips",
        tolls_column="tolls",
        has_passenger_count=False,
        has_ratecode=False,
        has_payment_type=False,
        has_extra=False,
        has_mta_tax=False,
        has_improvement_surcharge=False,
        has_congestion_surcharge=True,
        has_total_amount=False,
        has_black_car_fund=True,
        has_sales_tax=True,
        ignored=FHVHV_IGNORED,
    ),
    Vintage(
        name="fhvhv_v2_airport_fee",
        service="fhvhv",
        first=dt.date(2022, 1, 1),
        last=dt.date(2024, 12, 1),
        pickup_column="pickup_datetime",
        dropoff_column="dropoff_datetime",
        pu_zone_column="PULocationID",
        do_zone_column="DOLocationID",
        operator_column="hvfhs_license_num",
        distance_column="trip_miles",
        fare_column="base_passenger_fare",
        tip_column="tips",
        tolls_column="tolls",
        has_passenger_count=False,
        has_ratecode=False,
        has_payment_type=False,
        has_extra=False,
        has_mta_tax=False,
        has_improvement_surcharge=False,
        has_congestion_surcharge=True,
        has_airport_fee=True,
        has_total_amount=False,
        has_black_car_fund=True,
        has_sales_tax=True,
        ignored=FHVHV_IGNORED,
    ),
    Vintage(
        name="fhvhv_v3_cbd",
        service="fhvhv",
        first=dt.date(2025, 1, 1),
        last=None,
        pickup_column="pickup_datetime",
        dropoff_column="dropoff_datetime",
        pu_zone_column="PULocationID",
        do_zone_column="DOLocationID",
        operator_column="hvfhs_license_num",
        distance_column="trip_miles",
        fare_column="base_passenger_fare",
        tip_column="tips",
        tolls_column="tolls",
        has_passenger_count=False,
        has_ratecode=False,
        has_payment_type=False,
        has_extra=False,
        has_mta_tax=False,
        has_improvement_surcharge=False,
        has_congestion_surcharge=True,
        has_airport_fee=True,
        has_cbd_congestion_fee=True,
        ignored=FHVHV_IGNORED,
    ),
)


class UnknownVintageError(LookupError):
    """No vintage covers this service and month."""


def resolve_vintage(service: Service, period: dt.date) -> Vintage:
    """The one vintage covering this service and month.

    Ranges are asserted non overlapping by a test, so the first match is the
    only match and returning it is not an arbitrary choice.
    """
    for vintage in VINTAGES:
        if vintage.service == service and vintage.covers(period.replace(day=1)):
            return vintage
    raise UnknownVintageError(
        f"No vintage covers {service} for {period:%Y-%m}. "
        f"The earliest published {service} file is "
        f"{min(v.first for v in VINTAGES if v.service == service):%Y-%m}."
    )


def expected_columns(vintage: Vintage) -> set[str]:
    """Every column this project reads from a file of this vintage."""
    columns = {
        vintage.pickup_column,
        vintage.dropoff_column,
        vintage.operator_column,
        vintage.distance_column,
        vintage.fare_column,
        vintage.tip_column,
        vintage.tolls_column,
    }
    if vintage.pu_zone_column and vintage.do_zone_column:
        columns |= {vintage.pu_zone_column, vintage.do_zone_column}
    if vintage.has_passenger_count:
        columns.add("passenger_count")
    if vintage.has_ratecode:
        columns.add("RatecodeID")
    if vintage.has_payment_type:
        columns.add("payment_type")
    if vintage.has_extra:
        columns.add("extra")
    if vintage.has_mta_tax:
        columns.add("mta_tax")
    if vintage.has_improvement_surcharge:
        columns.add("improvement_surcharge")
    if vintage.has_congestion_surcharge:
        columns.add("congestion_surcharge")
    if vintage.has_airport_fee:
        columns.add("airport_fee")
    if vintage.has_cbd_congestion_fee:
        columns.add("cbd_congestion_fee")
    if vintage.has_total_amount:
        columns.add("total_amount")
    if vintage.has_black_car_fund:
        columns.add("bcf")
    if vintage.has_sales_tax:
        columns.add("sales_tax")
    return columns
