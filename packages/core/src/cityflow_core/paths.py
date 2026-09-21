"""Every path in the project resolves through here.

Hardcoded relative paths scattered through a pipeline are the reason a build
works from the repository root and nowhere else. One resolver, anchored on the
marker file, and the pipeline runs from any working directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

MARKER = "pyproject.toml"
WORKSPACE_KEY = "[tool.uv.workspace]"


@cache
def repo_root() -> Path:
    """Walk up until the workspace root pyproject is found.

    The workspace marker distinguishes the root from the four member packages,
    each of which also has a pyproject.
    """
    override = os.environ.get("CITYFLOW_ROOT")
    if override:
        return Path(override).resolve()

    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        marker = candidate / MARKER
        if marker.is_file() and WORKSPACE_KEY in marker.read_text(encoding="utf-8"):
            return candidate
    raise RuntimeError(
        "Could not locate the cityflow workspace root. Set CITYFLOW_ROOT to the repository root."
    )


@dataclass(frozen=True, slots=True)
class Paths:
    """Resolved locations for everything the pipeline reads or writes."""

    root: Path

    @classmethod
    def resolve(cls) -> Paths:
        return cls(root=repo_root())

    # Inputs -----------------------------------------------------------------
    @property
    def reference(self) -> Path:
        """Committed reference data: the zone lookup, the zone geometry."""
        return self.root / "data" / "reference"

    @property
    def raw(self) -> Path:
        """Downloaded or generated source parquet. Gitignored, can be large."""
        return self.root / "raw"

    # The warehouse ----------------------------------------------------------
    @property
    def warehouse_dir(self) -> Path:
        return self.root / "warehouse"

    @property
    def warehouse(self) -> Path:
        return self.warehouse_dir / "cityflow.duckdb"

    # Transform --------------------------------------------------------------
    @property
    def transform(self) -> Path:
        return self.root / "transform"

    @property
    def metrics_file(self) -> Path:
        return self.root / "metrics" / "metrics.yml"

    # Outputs ----------------------------------------------------------------
    @property
    def shipped(self) -> Path:
        """What the browser can reach. Served from web/public/data."""
        return self.root / "web" / "public" / "data"

    @property
    def manifest(self) -> Path:
        """The build manifest every published figure is re-derived from."""
        return self.shipped / "manifest.json"

    @property
    def docs(self) -> Path:
        return self.root / "docs"

    def ensure(self) -> None:
        """Create the directories the pipeline writes into."""
        for directory in (self.raw, self.warehouse_dir, self.shipped, self.reference):
            directory.mkdir(parents=True, exist_ok=True)
