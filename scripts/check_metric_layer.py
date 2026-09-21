"""Fail the build when the dashboard computes a metric the layer already defines.

The metric layer is only worth having if it is the only place a metric is
written. A page that splices `browser_sql` out of the catalog for fourteen
metrics and hand writes the fifteenth has fifteen metrics and one document
about fourteen of them.

So this scans the web source for two things.

**Aggregate SQL over a component column.** `sum(tip_obs_tip_sum)` in a string
literal anywhere outside the catalog module is a metric being written by hand,
whatever it is called.

**Arithmetic between two component columns.** `row.tipped_trips /
row.tip_obs_trips` is the tipped share, computed in TypeScript, and it will
drift from the definition the moment the definition changes.

The allowlist is short and each entry has a reason. Row level display fields
are not metrics: deriving a speed from a distance and a duration on one trip in
the detail table is arithmetic on a row, not an aggregate over a population,
and the layer does not define it.

Usage:
    python scripts/check_metric_layer.py            # scan web/src
    python scripts/check_metric_layer.py <dir> ...  # scan given roots
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ROOTS = (REPO / "web" / "src", REPO / "web" / "app")
METRICS_FILE = REPO / "metrics" / "metrics.yml"

SCANNED_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx"})
SKIP_DIRECTORIES = frozenset({"node_modules", ".next", "out", "dist", "tests"})

# Files permitted to name a component in an aggregate, with the reason.
ALLOWLIST: dict[str, str] = {
    "src/lib/metrics.ts": (
        "The catalog module. It loads metric_catalog.json and splices the "
        "layer's own browser_sql into queries, which is the mechanism this "
        "gate exists to protect."
    ),
    "src/lib/sql.ts": (
        "The query builders. They select component columns and hand them to "
        "the catalog's expression; the gate checks below that they do not "
        "aggregate or divide them themselves."
    ),
}

AGGREGATES = ("sum", "count", "avg", "median", "min", "max")


def components(path: Path = METRICS_FILE) -> set[str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: set[str] = set()
    for metric in raw.get("metrics", []):
        out.update(metric.get("components", []))
    if not out:
        raise RuntimeError(f"{path} names no components; nothing to protect.")
    return out


def _aggregate_pattern(names: set[str]) -> re.Pattern[str]:
    joined = "|".join(sorted(re.escape(n) for n in names))
    functions = "|".join(AGGREGATES)
    return re.compile(rf"\b(?:{functions})\s*\(\s*(?:{joined})\b", re.IGNORECASE)


def _ratio_pattern(names: set[str]) -> re.Pattern[str]:
    joined = "|".join(sorted(re.escape(n) for n in names))
    # a.tipped_trips / a.tip_obs_trips, or tipped_trips / tip_obs_trips
    return re.compile(rf"\b(?:\w+\.)?(?:{joined})\s*/\s*(?:\w+\.)?(?:{joined})\b")


def scan_file(
    path: Path, aggregate: re.Pattern[str], ratio: re.Pattern[str]
) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return findings
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        if aggregate.search(line):
            findings.append((number, "aggregate over a component column", stripped))
        elif ratio.search(line):
            findings.append((number, "arithmetic between two components", stripped))
    return findings


def files_under(roots: list[Path]) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                continue
            if any(part in SKIP_DIRECTORIES for part in path.parts):
                continue
            out.append(path)
    return out


def allowlisted(path: Path, base: Path) -> bool:
    try:
        relative = path.relative_to(base).as_posix()
    except ValueError:
        return False
    return relative in ALLOWLIST


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", help="Directories to scan.")
    parser.add_argument(
        "--base",
        default=str(REPO / "web"),
        help="Path the allowlist is relative to.",
    )
    args = parser.parse_args(argv)

    roots = [Path(r).resolve() for r in args.roots] or list(DEFAULT_ROOTS)
    base = Path(args.base).resolve()
    names = components()
    aggregate = _aggregate_pattern(names)
    ratio = _ratio_pattern(names)

    paths = files_under(roots)
    if not paths:
        print(
            f"No source files under {[str(r) for r in roots]}. Refusing to report a pass.",
            file=sys.stderr,
        )
        return 2

    violations = 0
    for path in paths:
        if allowlisted(path, base):
            continue
        for number, reason, line in scan_file(path, aggregate, ratio):
            violations += 1
            shown = path.relative_to(base) if path.is_relative_to(base) else path
            print(f"{shown}:{number}: {reason}")
            print(f"    {line}")

    if violations:
        print(
            f"\n{violations} metric computation(s) outside the metric layer.\n"
            "Read the definition out of metric_catalog.json and splice its "
            "browser_sql into the query instead. If the expression genuinely is "
            "not a metric, say so by adding the file to ALLOWLIST with a reason."
        )
        return 1

    print(
        f"No metric computed outside the layer across {len(paths)} files "
        f"({len(names)} component columns protected)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
