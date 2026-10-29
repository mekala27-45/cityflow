#!/usr/bin/env python3
"""Turn a probe report into GitHub Actions annotations.

`cityflow probe --json` writes one JSON document per run. On a runner that
document is an artifact, which needs a download to read; annotations sit on the
run's own page and in the checks API, so the three things worth knowing before
a real build (the schema groups, each file's size, and anything that would stop
the build) are written as a handful of them. GitHub caps the number of
annotations a step may raise, so each kind is one multi line annotation rather
than one per file.

Usage:
    python scripts/annotate_probe.py probe.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotation(level: str, title: str, lines: list[str]) -> str:
    """One workflow command. Titles are properties, so commas and colons are escaped too."""
    safe_title = _escape(title).replace(":", "%3A").replace(",", "%2C")
    return f"::{level} title={safe_title}::{_escape(chr(10).join(lines))}"


def render(document: dict[str, Any]) -> list[str]:
    files: list[dict[str, Any]] = document["files"]
    groups: list[dict[str, Any]] = document["groups"]
    published = [f for f in files if f["published"]]
    total_rows = sum(f["rows"] or 0 for f in published)
    total_bytes = sum(f["bytes"] or 0 for f in published)

    summary = [
        f"{len(published)} of {len(files)} files published, "
        f"{total_rows:,} rows, {total_bytes / 1e9:.2f} GB"
    ]
    for g in groups:
        notes = []
        if g["missing"]:
            notes.append("missing " + ", ".join(g["missing"]))
        if g["case_only"]:
            notes.append("case only " + ", ".join(f"{a} is {b}" for a, b in g["case_only"]))
        if g["unexpected"]:
            notes.append("unexpected " + ", ".join(g["unexpected"]))
        if g["empty"]:
            notes.append("all null " + ", ".join(g["empty"]))
        summary.append(
            f"{g['service']} {g['vintage']} {g['first']} to {g['last']} ({g['months']}): "
            f"{g['rows']:,} rows, {g['bytes'] / 1e9:.2f} GB, " + ("; ".join(notes) or "as expected")
        )

    schemas = [
        f"{g['service']} {g['first']} to {g['last']}: "
        + ", ".join(f"{name} {kind}" for name, kind in g["columns"])
        for g in groups
    ]
    sizes = [
        f"{f['source']} {f['rows'] or 0:,} rows {(f['bytes'] or 0) / 1e6:.1f} MB "
        f"{f['row_groups'] or 0} row groups"
        for f in published
    ]
    absent = [
        f"{f['source']}: {f['error'] or 'not published'}" for f in files if not f["published"]
    ]

    out = [
        annotation("notice", "probe summary", summary),
        annotation("notice", "schemas", schemas or ["no published file"]),
        annotation("notice", "sizes", sizes or ["no published file"]),
    ]
    if absent:
        out.append(annotation("warning", "not published", absent))
    broken = [
        f"{f['source']}: {f['error'] or 'missing ' + ', '.join(f['missing'])}"
        for f in published
        if f["error"] or f["missing"]
    ]
    if broken:
        out.append(annotation("error", "unreadable or missing columns", broken))
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: annotate_probe.py PROBE_JSON", file=sys.stderr)
        return 2
    document = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    for line in render(document):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
