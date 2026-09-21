"""The cityflow command line.

Every stage of the build is a subcommand and every subcommand is idempotent, so
a failed run is resumed rather than restarted. The Makefile is a thin wrapper
over these, not a second implementation of them.
"""

from __future__ import annotations

import csv
import json
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cityflow_core import Paths, load_config, session
from cityflow_core.config import CityflowConfig

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="An analytics warehouse and dashboard over NYC trip records.",
)
console = Console()


def _config(backend: str | None) -> CityflowConfig:
    config = load_config()
    if backend:
        config = config.model_copy(update={"backend": backend})
    return config


@app.command()
def zones(
    tolerance: Annotated[float, typer.Option(help="Simplification tolerance in feet.")] = 150.0,
) -> None:
    """Build the zone GeoJSON and dim_zone reference from the TLC shapefile."""
    from cityflow_publish.geometry import prepare_zones

    paths = Paths.resolve()
    paths.ensure()
    stem = paths.raw / "zones" / "taxi_zones"
    if not stem.with_suffix(".shp").is_file():
        console.print(
            f"[red]Missing {stem}.shp[/red]. See docs/runbook.md for where the "
            "official TLC shapefile comes from."
        )
        raise typer.Exit(code=2)

    rows, stats = prepare_zones(stem, paths.shipped / "zones.geojson", tolerance_feet=tolerance)
    reference = paths.reference / "dim_zone.csv"
    with reference.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "zone_id",
                "zone",
                "borough",
                "service_zone",
                "is_unknown",
                "is_airport",
                "has_geometry",
                "part_count",
                "centroid_lon",
                "centroid_lat",
                "area_sq_mi",
            ]
        )
        for zone in sorted(rows, key=lambda z: z.zone_id):
            writer.writerow(
                [
                    zone.zone_id,
                    zone.zone,
                    zone.borough,
                    zone.service_zone,
                    int(zone.is_unknown),
                    int(zone.is_airport),
                    int(zone.has_geometry),
                    zone.part_count,
                    "" if zone.centroid_lon is None else zone.centroid_lon,
                    "" if zone.centroid_lat is None else zone.centroid_lat,
                    "" if zone.area_sq_mi is None else zone.area_sq_mi,
                ]
            )

    table = Table(title="Zone geometry", show_header=False)
    for key, value in stats.items():
        table.add_row(key.replace("_", " "), f"{value:,.2f}")
    console.print(table)
    console.print(f"Wrote {reference} and {paths.shipped / 'zones.geojson'}")


@app.command()
def ingest(
    scale: Annotated[
        float,
        typer.Option(help="Fraction of full volume, synthetic backend only."),
    ] = 1.0,
    backend: Annotated[str | None, typer.Option(help="Override the configured backend.")] = None,
    keep_source: Annotated[
        bool, typer.Option(help="Do not delete source files after ingest.")
    ] = False,
    fresh: Annotated[bool, typer.Option(help="Drop the warehouse and start over.")] = False,
) -> None:
    """Resolve, normalize, quarantine and land every month in the window."""
    from cityflow_ingest.pipeline import IngestReport, MonthReport, run_ingest
    from cityflow_ingest.registry import SourceUnavailableError

    config = _config(backend)
    paths = Paths.resolve()
    paths.ensure()
    if fresh:
        paths.warehouse.unlink(missing_ok=True)

    console.print(
        f"backend [bold]{config.backend}[/bold], "
        f"{config.start:%Y-%m} to {config.end:%Y-%m}, "
        f"{len(config.services)} services"
        + (f", scale {scale}" if config.backend == "synthetic" else "")
    )

    def progress(month: MonthReport) -> None:
        console.print(
            f"  {month.spec!s:<18} {month.vintage:<22} "
            f"{month.source_rows:>11,} rows  "
            f"{month.quarantined_total:>8,} quarantined  "
            f"{month.seconds:5.1f}s"
        )

    # A source that cannot be reached is an ordinary outcome, not a defect in
    # this program, so it is reported as a message and an exit code rather than
    # as a traceback. The registry already writes a message worth reading: it
    # names the URL, gives curl's exit code, and says which backend needs no
    # network. All that was missing was anything that printed it.
    try:
        with session(config, paths.warehouse) as connection:
            report: IngestReport = run_ingest(
                connection,
                config,
                paths=paths,
                scale=scale,
                release_source=not keep_source,
                on_progress=progress,
            )
    except SourceUnavailableError as error:
        console.print(f"[red]Source unavailable[/red]\n{error}")
        raise typer.Exit(code=2) from error

    console.print()
    table = Table(title="Quarantine, by rule")
    table.add_column("rule")
    table.add_column("rows", justify="right")
    table.add_column("share of source", justify="right")
    for rule, count in report.by_rule().items():
        table.add_row(rule, f"{count:,}", f"{100 * count / report.source_rows:.4f}%")
    table.add_row(
        "[bold]total[/bold]",
        f"[bold]{report.quarantined_total:,}[/bold]",
        f"[bold]{100 * report.quarantine_share:.4f}%[/bold]",
    )
    console.print(table)
    console.print(
        f"{report.source_rows:,} source rows, {report.clean_rows:,} landed in "
        f"the warehouse, backend {config.backend}."
    )


@app.command(name="metrics")
def metrics_command(
    action: Annotated[str, typer.Argument(help="list, or compile <name>.")] = "list",
    name: Annotated[str | None, typer.Argument(help="Metric to compile.")] = None,
) -> None:
    """The metric catalog: every published number, defined once."""
    from cityflow_publish.metric_layer import MetricLayer

    layer = MetricLayer.load(Paths.resolve().metrics_file)
    if action == "list":
        table = Table(title=f"{len(layer.metrics)} metrics")
        table.add_column("name")
        table.add_column("grain")
        table.add_column("interval")
        table.add_column("owner")
        table.add_column("description", overflow="fold")
        for metric in layer.metrics:
            table.add_row(
                metric.name,
                ", ".join(metric.grain),
                metric.interval or "none",
                metric.owner,
                metric.description.strip().splitlines()[0],
            )
        console.print(table)
        return

    if action == "compile":
        if name is None:
            console.print("[red]compile needs a metric name[/red]")
            raise typer.Exit(code=2)
        console.print(layer.compile(name))
        return

    console.print(f"[red]Unknown action {action!r}. Use list or compile.[/red]")
    raise typer.Exit(code=2)


@app.command()
def publish(
    backend: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Build the shipped aggregate layer the browser queries."""
    from cityflow_publish.aggregates import build_all

    config = _config(backend)
    paths = Paths.resolve()
    paths.ensure()
    from cityflow_publish.analysis import run_analysis

    with session(config, paths.warehouse, read_only=True) as connection:
        results = build_all(connection, config, paths)
        analysis = run_analysis(connection, config, paths)

    table = Table(title="Shipped layer")
    table.add_column("file")
    table.add_column("rows", justify="right")
    table.add_column("MB", justify="right")
    table.add_column("summarizes rows", justify="right")
    table.add_column("ratio", justify="right")
    total_mb = 0.0
    for result in results:
        total_mb += result.megabytes
        table.add_row(
            result.name,
            f"{result.rows:,}",
            f"{result.megabytes:.2f}",
            f"{result.source_rows:,}",
            f"{result.compression_ratio:,.0f}x",
        )
    table.add_row("[bold]total[/bold]", "", f"[bold]{total_mb:.2f}[/bold]", "", "")
    console.print(table)

    console.print(
        f"decomposition {analysis.decomposition_rows:,} rows, "
        f"{sum(len(v) for v in analysis.changepoints.values())} changepoints, "
        f"{analysis.zone_comparisons} zone comparisons of which "
        f"{analysis.zone_comparisons_significant} survive Benjamini-Hochberg "
        f"at q={analysis.fdr_q}"
    )
    for note in analysis.notes:
        console.print(f"  {note}")


@app.command()
def reconcile() -> None:
    """Run every metric twice, from the warehouse and from the shipped layer."""
    from cityflow_publish.reconcile import reconcile_all

    config = load_config()
    paths = Paths.resolve()
    with session(config, paths.warehouse, read_only=True) as connection:
        report = reconcile_all(connection, paths)

    if report.ok:
        console.print(
            f"[green]{report.cells} metrics reconcile[/green] across "
            f"{report.checks} grain checks: the shipped aggregates agree with "
            "the warehouse everywhere."
        )
        return

    table = Table(title=f"{len(report.disagreements)} disagreements")
    table.add_column("metric")
    table.add_column("grain")
    table.add_column("key")
    table.add_column("warehouse", justify="right")
    table.add_column("browser", justify="right")
    for item in report.disagreements[:40]:
        table.add_row(
            item.metric,
            item.grain,
            item.key,
            f"{item.warehouse!r}",
            f"{item.browser!r}",
        )
    console.print(table)
    if len(report.disagreements) > 40:
        console.print(f"... and {len(report.disagreements) - 40} more")
    raise typer.Exit(code=1)


@app.command()
def manifest() -> None:
    """Write the build manifest every published figure is re-derived from."""
    from cityflow_publish.manifest import build_manifest

    config = load_config()
    paths = Paths.resolve()
    with session(config, paths.warehouse, read_only=True) as connection:
        data = build_manifest(connection, config, paths)
    paths.manifest.parent.mkdir(parents=True, exist_ok=True)
    paths.manifest.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    console.print(f"Wrote {paths.manifest} with {len(data)} sections")


@app.command()
def doctor() -> None:
    """Report what this machine can and cannot do, before a build fails halfway."""
    import shutil

    config = load_config()
    paths = Paths.resolve()
    checks: list[tuple[str, bool, str]] = [
        ("repository root", paths.root.is_dir(), str(paths.root)),
        (
            "zone reference",
            (paths.reference / "dim_zone.csv").is_file(),
            "run: cityflow zones",
        ),
        (
            "zone shapefile",
            (paths.raw / "zones" / "taxi_zones.shp").is_file(),
            "see docs/runbook.md",
        ),
        ("curl", shutil.which("curl") is not None, "needed by the tlc backend"),
        ("dbt", shutil.which("dbt") is not None, "needed by: make transform"),
        ("node", shutil.which("node") is not None, "needed by: make web"),
        ("warehouse", paths.warehouse.is_file(), "run: cityflow ingest"),
    ]
    table = Table(title=f"cityflow doctor (backend: {config.backend})")
    table.add_column("check")
    table.add_column("state")
    table.add_column("note")
    ok = True
    for label, passed, note in checks:
        ok = ok and passed
        table.add_row(label, "[green]yes[/green]" if passed else "[red]no[/red]", note)
    console.print(table)
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the version."""
    console.print("cityflow 0.1.0")


def main() -> int:
    app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
