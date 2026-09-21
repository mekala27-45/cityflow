"""Fail the build when a shipped file grows past what GitHub will serve.

GitHub refuses a file over 100MB outright and warns past 50MB. A repository
that serves its own demo from Pages therefore has a hard per file ceiling, and
discovering it at push time, after the warehouse has been rebuilt, is the
expensive way to find out.

The budget is 95MB, which leaves room to be wrong about compression. The total
budget exists for a different reason: nobody clones a repository to look at a
gigabyte of parquet, and a shipped layer that keeps growing is a sign the
aggregate grain has drifted towards the fact table.

Usage:
    python scripts/check_shipped_size.py
    python scripts/check_shipped_size.py --dir <path> --file-budget-mb 95
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DIR = REPO / "web" / "public" / "data"
CONFIG = REPO / "config" / "cityflow.yml"

# Anything not in this set is a build artifact that should not be in the
# shipped directory at all, which is its own kind of mistake.
EXPECTED_SUFFIXES = frozenset({".parquet", ".json", ".geojson"})


def budgets() -> tuple[float, float]:
    """Read the budget from the same config the pipeline used."""
    if not CONFIG.is_file():
        return 95.0, 400.0
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    return (
        float(raw.get("shipped_file_budget_mb", 95.0)),
        float(raw.get("shipped_total_budget_mb", 400.0)),
    )


def main(argv: list[str]) -> int:
    file_budget, total_budget = budgets()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=str(DEFAULT_DIR))
    parser.add_argument("--file-budget-mb", type=float, default=file_budget)
    parser.add_argument("--total-budget-mb", type=float, default=total_budget)
    args = parser.parse_args(argv)

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        print(f"{directory} does not exist.", file=sys.stderr)
        return 2

    files = sorted(p for p in directory.iterdir() if p.is_file())
    if not files:
        print(
            f"No shipped files in {directory}. Refusing to report a pass.",
            file=sys.stderr,
        )
        return 2

    over: list[tuple[Path, float]] = []
    unexpected: list[Path] = []
    total = 0.0
    print(f"{'file':<40} {'MB':>8}  budget {args.file_budget_mb:.0f}MB")
    for path in files:
        megabytes = path.stat().st_size / 1_000_000
        total += megabytes
        flag = ""
        if megabytes > args.file_budget_mb:
            over.append((path, megabytes))
            flag = "  OVER"
        if path.suffix not in EXPECTED_SUFFIXES:
            unexpected.append(path)
            flag += "  UNEXPECTED TYPE"
        print(f"{path.name:<40} {megabytes:>8.2f}{flag}")
    print(f"{'total':<40} {total:>8.2f}  budget {args.total_budget_mb:.0f}MB")

    failed = False
    for path, megabytes in over:
        print(
            f"\n{path.name} is {megabytes:.1f}MB against a "
            f"{args.file_budget_mb:.0f}MB per file budget. Coarsen its grain or "
            "narrow the window. Splitting it to get under a per file limit is "
            "dodging the budget, not meeting it."
        )
        failed = True
    for path in unexpected:
        print(f"\n{path.name} is not a shipped artifact type. Why is it here?")
        failed = True
    if total > args.total_budget_mb:
        print(
            f"\nThe shipped layer is {total:.1f}MB against a "
            f"{args.total_budget_mb:.0f}MB total budget. A shipped layer that "
            "keeps growing is a sign the aggregate grain has drifted towards "
            "the fact table."
        )
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
