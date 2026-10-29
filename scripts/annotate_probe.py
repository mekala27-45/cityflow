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

from cityflow_ingest.probe import group_notes


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


# GitHub cuts an annotation's message at 4,096 characters, so a longer one is
# split at line boundaries and numbered rather than silently truncated, which is
# what the first real probe run did to its list of file sizes.
MESSAGE_LIMIT = 4_000


def annotation(level: str, title: str, lines: list[str]) -> str:
    """One workflow command. Titles are properties, so commas and colons are escaped too."""
    safe_title = _escape(title).replace(":", "%3A").replace(",", "%2C")
    return f"::{level} title={safe_title}::{_escape(chr(10).join(lines))}"


def annotations(level: str, title: str, lines: list[str]) -> list[str]:
    """As many commands as the message needs, each under the limit once escaped."""
    pieces: list[list[str]] = [[]]
    size = 0
    for line in lines:
        cost = len(_escape(line)) + 3
        if pieces[-1] and size + cost > MESSAGE_LIMIT:
            pieces.append([])
            size = 0
        pieces[-1].append(line[: MESSAGE_LIMIT - 3])
        size += cost
    if len(pieces) == 1:
        return [annotation(level, title, pieces[0])]
    return [
        annotation(level, f"{title} ({i} of {len(pieces)})", piece)
        for i, piece in enumerate(pieces, start=1)
    ]


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
        notes = group_notes(g)
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
        *annotations("notice", "probe summary", summary),
        *annotations("notice", "schemas", schemas or ["no published file"]),
        *annotations("notice", "sizes", sizes or ["no published file"]),
    ]
    if absent:
        out.extend(annotations("warning", "not published", absent))
    broken = [
        f"{f['source']}: {f['error'] or 'missing ' + ', '.join(f['missing'])}"
        for f in published
        if f["error"] or f["missing"]
    ]
    if broken:
        out.extend(annotations("error", "unreadable or missing columns", broken))
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
