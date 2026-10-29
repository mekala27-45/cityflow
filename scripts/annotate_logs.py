#!/usr/bin/env python3
"""Put the tail of each build log on the run page, one annotation per log.

A real build on a runner takes hours and writes its evidence to logs that the
run page shows only to someone who opens each step. The refresh workflow tees
every stage to logs/<stage>.log and calls this at the end: on success with the
summary lines worth reading (the quarantine table, the shipped layer, the
reconcile verdict), on failure with the last lines of every stage, so the stage
that broke says why on the page and in the checks API without anyone
downloading a log.

Colour codes are stripped, and a tail longer than an annotation can carry keeps
its last lines, since that is where a failure explains itself.

Usage:
    python scripts/annotate_logs.py --level error --tail 80 logs/*.log
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from annotate_probe import annotation

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# An annotation message carries up to 64 KB. Leave room for the escaping.
MAX_CHARACTERS = 48_000


def tail(path: Path, lines: int) -> list[str]:
    """The last lines of a log, without colour codes or trailing blanks."""
    text = ANSI.sub("", path.read_text(encoding="utf-8", errors="replace"))
    kept = [line.rstrip() for line in text.splitlines()]
    while kept and not kept[-1]:
        kept.pop()
    kept = kept[-lines:]
    while sum(len(line) + 1 for line in kept) > MAX_CHARACTERS:
        kept.pop(0)
    return kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Annotate the tail of each log.")
    parser.add_argument("--level", choices=("notice", "warning", "error"), default="notice")
    parser.add_argument("--tail", type=int, default=40)
    parser.add_argument("logs", nargs="+", type=Path)
    args = parser.parse_args(argv)

    for path in args.logs:
        if not path.is_file():
            continue
        lines = tail(path, args.tail)
        print(annotation(args.level, path.stem, lines or ["(empty)"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
