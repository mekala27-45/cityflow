"""Placeholder replaced during the build."""

from __future__ import annotations

import typer

app = typer.Typer()


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo("0.1.0")
